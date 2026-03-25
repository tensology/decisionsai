"""
VRAM-aware chunked voice conversion using the Kanade model.

Adapted from KokoClone (https://github.com/Ashish-Patnaik/kokoclone).
Splits long audio into chunks that respect the model's RoPE positional
embedding ceiling (~10.9s per chunk) and available VRAM.
"""

from __future__ import annotations

import time
import torch
from kanade_tokenizer import vocode

# RoPE ceiling constants (from Kanade mel_decoder Transformer)
_ROPE_MAX_FRAMES: int = 1024
_MEL_HOP_LENGTH: int = 256
_ROPE_SAFETY_MARGIN: float = 0.75
_OVERLAP_SECONDS: float = 0.5
_SECONDS_PER_GB: float = 10.0


def chunked_voice_conversion(
    kanade,
    vocoder_model,
    source_wav: torch.Tensor,
    ref_wav: torch.Tensor,
    sample_rate: int,
    vram_fraction: float = 0.9,
) -> torch.Tensor:
    """Convert source_wav to the reference voice in VRAM-safe chunks.

    Returns a 1-D CPU float32 tensor.
    """
    device: torch.device = source_wav.device
    n_samples: int = source_wav.shape[-1]
    t0 = time.perf_counter()

    # Determine chunk size from RoPE ceiling
    overlap_samples = int(_OVERLAP_SECONDS * sample_rate)
    rope_max_window = (_ROPE_MAX_FRAMES - 1) * _MEL_HOP_LENGTH
    rope_safe_chunk = int((rope_max_window - 2 * overlap_samples) * _ROPE_SAFETY_MARGIN)

    if device.type == "cuda":
        total_vram = torch.cuda.get_device_properties(device).total_memory
        budget_gb = (total_vram * vram_fraction) / (1024 ** 3)
        vram_chunk = int(max(5.0, budget_gb * _SECONDS_PER_GB) * sample_rate)
        chunk_samples = min(vram_chunk, rope_safe_chunk)
    else:
        chunk_samples = rope_safe_chunk

    # Short-circuit: whole file fits in one chunk
    if n_samples <= chunk_samples:
        with torch.inference_mode():
            mel = kanade.voice_conversion(
                source_waveform=source_wav, reference_waveform=ref_wav
            )
            wav = vocode(vocoder_model, mel.unsqueeze(0))
        return wav.squeeze().cpu()

    # Chunked processing with overlap
    overlap_frames = overlap_samples // _MEL_HOP_LENGTH
    mel_parts: list[torch.Tensor] = []
    pos = 0

    while pos < n_samples:
        win_start = max(0, pos - overlap_samples)
        win_end = min(n_samples, pos + chunk_samples + overlap_samples)
        chunk = source_wav[..., win_start:win_end]

        with torch.inference_mode():
            mel_chunk = kanade.voice_conversion(
                source_waveform=chunk, reference_waveform=ref_wav
            )

        mel_chunk = mel_chunk.cpu()

        left_trim = 0 if pos == 0 else overlap_frames
        right_trim = mel_chunk.shape[-1] if win_end >= n_samples else mel_chunk.shape[-1] - overlap_frames
        mel_parts.append(mel_chunk[..., left_trim:right_trim])

        pos += chunk_samples

        if device.type == "cuda":
            torch.cuda.empty_cache()

    # Assemble and vocode
    full_mel = torch.cat(mel_parts, dim=-1).to(device)
    with torch.inference_mode():
        wav = vocode(vocoder_model, full_mel.unsqueeze(0))

    elapsed = time.perf_counter() - t0
    if elapsed > 1.0:
        import logging
        logging.getLogger("decisions").debug(
            "chunked_convert: %.1fs audio in %.1fs", n_samples / sample_rate, elapsed
        )

    return wav.squeeze().cpu()
