"""
Voice cloning via Kanade voice conversion model.

Wraps the Kanade model (frothywater/kanade-12.5hz) to convert Kokoro TTS
output into a target voice using a reference audio clip. The model is loaded
lazily on first use and cached for subsequent calls.

Based on KokoClone (https://github.com/Ashish-Patnaik/kokoclone).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import numpy as np

logger = logging.getLogger("decisions")

# Lazy imports — torch and kanade_tokenizer are heavy
_kanade_model = None
_vocoder_model = None
_device = None
_sample_rate: Optional[int] = None


def _ensure_loaded():
    """Load Kanade model + vocoder on first call. Cached globally."""
    global _kanade_model, _vocoder_model, _device, _sample_rate
    if _kanade_model is not None:
        return

    import warnings
    import torch
    # Suppress CUDA-not-available warnings from torch autocast (running on CPU/MPS)
    warnings.filterwarnings("ignore", message=r".*CUDA is not available.*", category=UserWarning)
    # Suppress FlashAttention fallback log from kanade_tokenizer
    logging.getLogger("kanade_tokenizer").setLevel(logging.ERROR)

    from kanade_tokenizer import KanadeModel, load_vocoder

    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("VoiceCloner: loading Kanade model on %s", _device.type.upper())

    _kanade_model = KanadeModel.from_pretrained("frothywater/kanade-12.5hz").to(_device).eval()
    _vocoder_model = load_vocoder(_kanade_model.config.vocoder_name).to(_device)
    _sample_rate = _kanade_model.config.sample_rate
    logger.info("VoiceCloner: Kanade ready (sample_rate=%d)", _sample_rate)


def _load_reference(ref_path: str) -> "torch.Tensor":
    """Load a reference audio clip and resample to Kanade's sample rate.

    Kanade's ``load_audio`` uses soundfile internally, which only supports
    WAV/FLAC/OGG.  For other formats (m4a, mp3, webm …) we convert via
    pydub (ffmpeg backend) to an in-memory WAV first.
    """
    import torch

    _SOUNDFILE_EXTS = {'.wav', '.flac', '.ogg'}
    ext = os.path.splitext(ref_path)[1].lower()

    if ext in _SOUNDFILE_EXTS:
        try:
            from kanade_tokenizer import load_audio
            return load_audio(ref_path, sample_rate=_sample_rate).to(_device)
        except Exception:
            # File extension says WAV/FLAC/OGG but content might be something else
            logger.warning("VoiceCloner: soundfile failed on %s, trying pydub fallback", os.path.basename(ref_path))

    # Fallback: pydub (ffmpeg) handles any format including misnamed files
    import io
    import soundfile as sf
    from pydub import AudioSegment

    logger.info("VoiceCloner: converting %s via pydub/ffmpeg", os.path.basename(ref_path))
    seg = AudioSegment.from_file(ref_path)
    seg = seg.set_channels(1).set_frame_rate(_sample_rate).set_sample_width(2)
    buf = io.BytesIO()
    seg.export(buf, format='wav')
    buf.seek(0)
    data, sr = sf.read(buf, dtype='float32')
    return torch.from_numpy(data).float().to(_device)


# Cache reference waveforms by path to avoid re-loading every sentence
_ref_cache: dict[str, "torch.Tensor"] = {}


def _get_reference(ref_path: str) -> "torch.Tensor":
    """Get (cached) reference waveform tensor."""
    if ref_path not in _ref_cache:
        _ref_cache[ref_path] = _load_reference(ref_path)
        logger.info("VoiceCloner: cached reference clip %s", os.path.basename(ref_path))
    return _ref_cache[ref_path]


def clear_reference_cache():
    """Clear cached reference waveforms (call when voice changes)."""
    _ref_cache.clear()


def unload_model():
    """Release Kanade model weights from memory. Call when switching away from custom voice."""
    global _kanade_model, _vocoder_model, _device, _sample_rate
    if _kanade_model is None:
        return
    try:
        import torch
        import gc
        _ref_cache.clear()
        _kanade_model.cpu()
        _vocoder_model.cpu()
        del _kanade_model
        del _vocoder_model
        _kanade_model = None
        _vocoder_model = None
        _device = None
        _sample_rate = None
        gc.collect()
        # Free MPS cache if on Apple Silicon
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        logger.info("VoiceCloner: Kanade model unloaded, memory freed")
    except Exception as e:
        logger.warning("VoiceCloner: error during unload: %s", e)


def convert_voice(
    audio_float32: np.ndarray,
    source_sample_rate: int,
    reference_path: str,
) -> np.ndarray:
    """Convert audio to the reference voice using Kanade.

    Parameters
    ----------
    audio_float32 : np.ndarray
        Source audio as float32 in [-1, 1], shape ``(N,)``.
    source_sample_rate : int
        Sample rate of the source audio (e.g. 24000 from Kokoro).
    reference_path : str
        Path to the reference voice clip (.wav, .mp3, .m4a, etc.).

    Returns
    -------
    np.ndarray
        Converted audio as float32 in [-1, 1]. Sample rate is
        ``_sample_rate`` (Kanade's native rate, 24000).
    """
    import torch

    _ensure_loaded()

    t0 = time.perf_counter()

    # Resample source to Kanade's sample rate if needed
    if source_sample_rate != _sample_rate:
        from math import gcd
        from scipy.signal import resample_poly

        g = gcd(source_sample_rate, _sample_rate)
        up, down = _sample_rate // g, source_sample_rate // g
        resampled = resample_poly(audio_float32, up, down).astype(np.float32)
        source_wav = torch.from_numpy(resampled).float().to(_device)
    else:
        source_wav = torch.from_numpy(audio_float32).float().to(_device)

    ref_wav = _get_reference(reference_path)

    # Use chunked conversion for long audio (respects RoPE ceiling)
    from distr.core.audio.chunked_convert import chunked_voice_conversion

    converted = chunked_voice_conversion(
        kanade=_kanade_model,
        vocoder_model=_vocoder_model,
        source_wav=source_wav,
        ref_wav=ref_wav,
        sample_rate=_sample_rate,
    )

    elapsed = time.perf_counter() - t0
    duration = len(audio_float32) / source_sample_rate
    logger.info(
        "VoiceCloner: converted %.1fs audio in %.1fs (%.1fx realtime)",
        duration, elapsed, duration / elapsed if elapsed > 0 else 0,
    )

    return converted.numpy()


def get_output_sample_rate() -> int:
    """Return the sample rate of voice-converted output."""
    _ensure_loaded()
    return _sample_rate
