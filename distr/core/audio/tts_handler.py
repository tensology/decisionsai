"""TTS Handler for generating voice samples from web settings."""
import io
import os
import re
import hashlib
import logging
import soundfile as sf
from distr.core.paths import TMP_DIR
from distr.core.utils import load_settings_from_db

logger = logging.getLogger(__name__)


def _tts_provider_to_internal(provider: str) -> str:
    """Map display or settings provider name to internal name (kokoro, elevenlabs, openai, qwen3)."""
    p = (provider or "").strip().lower()
    if p in ("kokoro", "kokoro (offline)"):
        return "kokoro"
    if p in ("elevenlabs", "elevenlabs (online)"):
        return "elevenlabs"
    if p in ("openai", "openai (online)"):
        return "openai"
    if p in ("qwen3", "qwen3-tts (offline)", "qwen3-tts (online)", "qwen3 (online)"):
        return "qwen3"
    if p in ("coqui", "coqui tts (offline)", "coqui tts"):
        return "coqui"
    return p or "kokoro"


def _normalize_voice_for_provider(provider: str, voice: str, settings: dict) -> str:
    """Resolve mixed/stale voice labels into valid provider voice IDs."""
    raw = (voice or "").strip()
    prov = _tts_provider_to_internal(provider)

    if prov == "openai":
        allowed = {"alloy", "echo", "fable", "onyx", "nova", "shimmer", "ash", "sage", "coral"}
        v = raw.lower()
        if v in allowed:
            return v
        # Handle labels like "Kiran nova" by matching any token to a valid id.
        tokens = re.split(r"[^a-zA-Z0-9_]+", v)
        for token in tokens:
            if token in allowed:
                return token
        configured = (settings.get("openai_voice") or "").strip().lower()
        if configured in allowed:
            return configured
        return "alloy"

    if prov == "kokoro":
        # Custom cloned voices pass through directly
        if raw.startswith("custom_"):
            return raw
        try:
            from distr.core.agent.session import KOKORO_VOICES
            valid_ids = set(KOKORO_VOICES.keys())
        except Exception:
            valid_ids = set()
        if raw in valid_ids:
            return raw
        # Try normalization used in UI labels, then fallback.
        candidate = raw.lower().replace(" ", "_")
        if candidate in valid_ids:
            return candidate
        configured = (settings.get("kokoro_voice") or "").strip()
        if configured.startswith("custom_"):
            return configured
        if configured in valid_ids:
            return configured
        return "af_heart"

    if prov == "qwen3":
        # Custom cloned voices pass through directly
        if raw.startswith("custom_"):
            return raw
        try:
            from distr.core.agent.constants import QWEN3_PRESETS, DEFAULT_QWEN3_VOICE
            valid_ids = {pr["id"] for pr in QWEN3_PRESETS}
        except Exception:
            valid_ids = {"Aiden"}
        # IDs are now capitalised (e.g. "Aiden", "Ryan") — match case-insensitively
        raw_lower = raw.lower()
        for vid in valid_ids:
            if vid.lower() == raw_lower:
                return vid
        configured = (settings.get("qwen3_voice") or "").strip()
        if configured.startswith("custom_"):
            return configured
        for vid in valid_ids:
            if vid.lower() == configured.lower():
                return vid
        return DEFAULT_QWEN3_VOICE

    # ElevenLabs is resolved again inside _generate_elevenlabs; keep raw here.
    # Coqui: validate speaker ID against known voices
    if prov == "coqui":
        try:
            from distr.core.agent.constants import COQUI_VOICES, DEFAULT_COQUI_VOICE
            if raw in COQUI_VOICES:
                return raw
            configured = (settings.get("coqui_voice") or "").strip()
            if configured in COQUI_VOICES:
                return configured
            return DEFAULT_COQUI_VOICE
        except Exception:
            return raw or "p225"

    return raw


def generate_tts_audio(
    text: str,
    provider: str = None,
    voice: str = None,
    speed: float = 1.0,
) -> str:
    """
    Generate TTS audio for arbitrary text and return the WAV file path.
    If provider/voice not specified, reads from settings DB (tts_provider, kokoro_voice/elevenlabs_voice/openai_voice).
    """
    from distr.core.utils import load_settings_from_db
    settings = load_settings_from_db()
    prov = _tts_provider_to_internal(provider or settings.get("tts_provider", "Kokoro (Offline)"))
    if voice is None:
        if prov == "kokoro":
            voice = (settings.get("kokoro_voice") or "af_heart").strip()
        elif prov == "elevenlabs":
            voice = (settings.get("elevenlabs_voice") or "default").strip()
        elif prov == "openai":
            voice = (settings.get("openai_voice") or "alloy").strip()
        elif prov == "qwen3":
            voice = (settings.get("qwen3_voice") or "Aiden").strip()
        elif prov == "coqui":
            voice = (settings.get("coqui_voice") or "p225").strip()
        else:
            voice = "af_heart"
    else:
        voice = voice.strip()
    voice = _normalize_voice_for_provider(prov, voice, settings)
    text = (text or "").strip()
    if not text:
        raise ValueError("Text is required for TTS")
    speed = max(0.5, min(2.0, float(speed)))
    os.makedirs(TMP_DIR, exist_ok=True)
    cache_key = hashlib.md5(f"{text}:{prov}:{voice}:{speed}".encode()).hexdigest()[:12]
    out_file = os.path.join(TMP_DIR, f"tts_chat_{prov}_{cache_key}.wav")
    if prov not in ("elevenlabs", "qwen3", "coqui") and os.path.exists(out_file):
        logger.debug("Using cached TTS: %s", out_file)
        return out_file
    logger.info("Generating TTS: provider=%s, voice=%s, speed=%s", prov, voice, speed)
    if prov == "kokoro":
        _generate_kokoro(text, voice, speed, out_file)
    elif prov == "elevenlabs":
        _generate_elevenlabs(text, voice, speed, out_file)
    elif prov == "openai":
        _generate_openai(text, voice, speed, out_file)
    elif prov == "qwen3":
        _generate_qwen3(text, voice, speed, out_file)
    elif prov == "coqui":
        _generate_coqui(text, voice, speed, out_file)
    else:
        raise ValueError(f"Unknown TTS provider: {provider or prov}")
    return out_file


def wav_to_mp3(wav_path: str, out_mp3_path: str = None) -> str:
    """Convert a WAV file to MP3 using pydub. Returns the path to the MP3 file."""
    if out_mp3_path is None:
        out_mp3_path = os.path.splitext(wav_path)[0] + ".mp3"
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_wav(wav_path)
        seg.export(out_mp3_path, format="mp3", bitrate="192k")
        logger.debug("Converted WAV to MP3: %s -> %s", wav_path, out_mp3_path)
        return out_mp3_path
    except ImportError as e:
        logger.warning("pydub required for MP3 conversion: %s", e)
        raise ValueError("MP3 export requires pydub. Install with: pip install pydub")


def generate_voice_sample(provider: str, voice: str, speed: float = 1.0, voice_name: str = None) -> str:
    """
    Generate a voice sample WAV file and return its path.

    Args:
        provider: TTS provider (kokoro, elevenlabs, openai)
        voice: Voice ID
        speed: Playback speed (0.5 - 2.0)
        voice_name: Display name for the voice

    Returns:
        Absolute path to the generated WAV file.
    """
    settings = load_settings_from_db()
    provider = _tts_provider_to_internal(provider)
    voice = _normalize_voice_for_provider(provider, voice, settings)
    display_name = _resolve_display_name(provider, voice, voice_name)
    # Sanitize display name — phonemizer/espeak can't handle newlines or special chars
    display_name = re.sub(r'\s+', ' ', display_name).strip()
    test_text = (
        f"Hi there, my name is {display_name}. I would love to help you get things done. "
        "Save this voice and let's get started."
    )

    os.makedirs(TMP_DIR, exist_ok=True)
    text_hash = hashlib.md5(f"{test_text}:{speed}".encode()).hexdigest()[:8]
    out_file = os.path.join(TMP_DIR, f"tts_{provider}_{voice}_{text_hash}_web.wav")

    if provider not in ("elevenlabs", "qwen3", "coqui") and not voice.startswith("custom_") and os.path.exists(out_file):
        logger.info("Using cached voice sample: %s", out_file)
        return out_file

    logger.info("Generating %s voice sample: voice=%s, speed=%s", provider, voice, speed)

    if provider == 'kokoro':
        _generate_kokoro(test_text, voice, speed, out_file)
    elif provider == 'elevenlabs':
        _generate_elevenlabs(test_text, voice, speed, out_file)
    elif provider == 'openai':
        _generate_openai(test_text, voice, speed, out_file)
    elif provider == 'qwen3':
        _generate_qwen3(test_text, voice, speed, out_file)
    elif provider == 'coqui':
        _generate_coqui(test_text, voice, speed, out_file)
    else:
        raise ValueError("Unknown TTS provider: %s", provider)

    return out_file


def _resolve_display_name(provider: str, voice: str, voice_name: str = None) -> str:
    """Resolve a clean display name for the voice, with server-side fallback."""
    if voice_name and voice_name.strip() and '_' not in voice_name:
        cleaned = voice_name.strip().lstrip('⭐').strip()
        if cleaned:
            return cleaned

    if provider == 'kokoro':
        if voice and voice.startswith('custom_'):
            try:
                from distr.core.db import get_session, CustomVoice
                db_id = int(voice.split('_', 1)[1])
                session = get_session()
                try:
                    cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                    if cv:
                        return cv.name
                finally:
                    session.close()
            except Exception:
                pass
            return "Custom Voice"
        try:
            from distr.core.agent.session import KOKORO_VOICES
            name = KOKORO_VOICES.get(voice)
            if name:
                return name
        except ImportError:
            pass
        if '_' in voice:
            return voice.rsplit('_', 1)[-1].capitalize()

    if provider == 'openai':
        return voice.capitalize() if voice else "Alloy"

    if provider == 'qwen3':
        if voice and voice.startswith('custom_'):
            try:
                from distr.core.db import get_session, CustomVoice
                db_id = int(voice.split('_', 1)[1])
                session = get_session()
                try:
                    cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                    if cv:
                        return cv.name
                finally:
                    session.close()
            except Exception:
                pass
            return "Custom Voice"
        try:
            from distr.core.agent.constants import QWEN3_PRESETS
            for pr in QWEN3_PRESETS:
                if pr.get("id") == voice:
                    return (pr.get("name") or voice).split(" ")[0]
        except Exception:
            pass
        return voice.capitalize() if voice else "Aiden"

    if provider == 'coqui':
        try:
            from distr.core.agent.constants import COQUI_VOICES
            return COQUI_VOICES.get(voice, voice)
        except Exception:
            pass
        return voice or "Sarah"

    return (voice_name or voice or "Assistant").strip()


def _resample_audio(audio, src_rate: int, target_rate: int):
    """Resample audio using the best available library."""
    import numpy as np
    if src_rate == target_rate:
        return audio, target_rate

    try:
        import soxr
        resampled = soxr.resample(audio, src_rate, target_rate, quality='VHQ')
        return resampled.astype(np.float32), target_rate
    except ImportError:
        pass

    try:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(target_rate, src_rate)
        resampled = resample_poly(audio, target_rate // g, src_rate // g)
        return resampled.astype(np.float32), target_rate
    except ImportError:
        pass

    num_samples = int(len(audio) * target_rate / src_rate)
    indices = np.linspace(0, len(audio) - 1, num_samples)
    resampled = np.interp(indices, np.arange(len(audio)), audio)
    return resampled.astype(np.float32), target_rate


def _generate_kokoro(text: str, voice: str, speed: float, out_file: str):
    """Generate Kokoro voice sample to WAV file. Applies Kanade voice conversion for custom voices."""
    import numpy as np
    from kokoro_onnx import Kokoro

    # Detect custom voice — resolve reference audio path from DB
    reference_path = None
    base_voice = voice
    if voice.startswith("custom_"):
        try:
            from distr.core.db import get_session, CustomVoice
            db_id = int(voice.split("_", 1)[1])
            session = get_session()
            try:
                cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                if cv and cv.audio_dir and os.path.isdir(cv.audio_dir):
                    # Find the first audio clip in the directory
                    for fname in os.listdir(cv.audio_dir):
                        if fname.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.webm')):
                            reference_path = os.path.join(cv.audio_dir, fname)
                            break
                # Pick base voice matching the reference speaker's gender
                gender = getattr(cv, 'gender', 'female') if cv else 'female'
                base_voice = "am_puck" if gender == "male" else "af_heart"
            finally:
                session.close()
        except Exception as e:
            logger.warning("Failed to resolve custom voice %s: %s", voice, e)
            base_voice = "af_heart"
        if not reference_path:
            raise FileNotFoundError(f"No reference audio found for custom voice {voice}")
        logger.info("Kokoro custom voice: base=%s, reference=%s", base_voice, os.path.basename(reference_path))

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../'))
    models_dir = os.path.join(base_dir, "distr", "core", "agent", "models")
    kokoro_model = os.path.join(models_dir, "kokoro-v1.0.onnx")
    kokoro_voices = os.path.join(models_dir, "voices-v1.0.bin")

    logger.debug("Kokoro model path: %s (exists=%s)", kokoro_model, os.path.exists(kokoro_model))
    logger.debug("Kokoro voices path: %s (exists=%s)", kokoro_voices, os.path.exists(kokoro_voices))

    if not os.path.exists(kokoro_model) or not os.path.exists(kokoro_voices):
        raise FileNotFoundError(f"Kokoro model files not found at {models_dir}")

    kokoro = Kokoro(kokoro_model, kokoro_voices)
    clamped_speed = max(0.5, min(2.0, speed))

    # Sanitize text — phonemizer/espeak chokes on embedded newlines
    text = re.sub(r'\s+', ' ', text).strip()

    # Normalize smart quotes for correct pronunciation
    from distr.core.agent.services.tts.kokoro import _normalize_text_for_tts
    text = _normalize_text_for_tts(text)

    # Split into sentences — Kokoro handles single sentences much better
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    if not sentences:
        sentences = [text]

    chunks = []
    sample_rate = None
    for sentence in sentences:
        audio, sr = kokoro.create(sentence, voice=base_voice, speed=clamped_speed)
        if audio is not None and len(audio) > 0:
            chunks.append(audio)
            sample_rate = sr

    if not chunks:
        raise ValueError("Kokoro returned no audio")

    audio = np.concatenate(chunks)

    # Apply Kanade voice conversion for custom voices
    if reference_path:
        logger.info("Applying Kanade voice conversion (%.1fs of audio)...", len(audio) / sample_rate)
        from distr.core.audio.voice_cloner import convert_voice, get_output_sample_rate
        audio = convert_voice(audio, sample_rate, reference_path)
        sample_rate = get_output_sample_rate()

    audio, sample_rate = _resample_audio(audio, sample_rate, 48000)
    sf.write(out_file, audio, sample_rate)
    logger.info(f"Wrote Kokoro sample to {out_file}")


def clear_elevenlabs_voice_cache():
    """Delete any cached ElevenLabs voice files (web samples and previews). Call when stability/similarity/style change."""
    if not os.path.isdir(TMP_DIR):
        return
    removed = 0
    for name in os.listdir(TMP_DIR):
        if name.startswith("tts_elevenlabs_") or name.startswith("elevenlabs_preview_"):
            path = os.path.join(TMP_DIR, name)
            try:
                os.remove(path)
                removed += 1
            except OSError as e:
                logger.warning("Could not remove %s: %s", path, e)
    if removed:
        logger.info("Cleared %d ElevenLabs voice cache file(s)", removed)


def _generate_elevenlabs(text: str, voice: str, speed: float, out_file: str):
    """Generate ElevenLabs voice sample to WAV file. Never uses cache; uses current DB voice_settings."""
    from elevenlabs import ElevenLabs
    import numpy as np

    settings = load_settings_from_db()
    api_key = settings.get('elevenlabs_key', '')
    if not api_key:
        raise ValueError("ElevenLabs API key not configured")
    stability = float(settings.get("elevenlabs_stability", 0.5))
    similarity_boost = float(settings.get("elevenlabs_similarity_boost", 0.6))
    style = float(settings.get("elevenlabs_style", 0.25))
    use_speaker_boost = bool(settings.get("elevenlabs_use_speaker_boost", True))

    client = ElevenLabs(api_key=api_key)
    requested_voice = (voice or "").strip()
    resolved_voice = requested_voice

    # Defensive resolution: some web/chat paths may pass display names or "default"
    # instead of a valid ElevenLabs voice_id.
    try:
        voices = client.voices.get_all().voices or []
        if voices:
            by_id = {v.voice_id: v.voice_id for v in voices if getattr(v, "voice_id", None)}
            by_name = {
                (v.name or "").strip().lower(): v.voice_id
                for v in voices
                if getattr(v, "voice_id", None) and getattr(v, "name", None)
            }
            configured_voice = (settings.get("elevenlabs_voice", "") or "").strip()
            fallback_voice = voices[0].voice_id
            req_lower = requested_voice.lower()
            if requested_voice in by_id:
                resolved_voice = requested_voice
            elif req_lower and req_lower in by_name:
                resolved_voice = by_name[req_lower]
            elif configured_voice in by_id:
                resolved_voice = configured_voice
            elif fallback_voice:
                resolved_voice = fallback_voice
    except Exception as resolve_err:
        logger.warning("ElevenLabs voice resolution failed, using raw voice '%s': %s", requested_voice, resolve_err)

    api_speed = max(0.7, min(1.2, float(speed)))

    audio_stream = client.text_to_speech.convert(
        text=text,
        voice_id=resolved_voice,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        voice_settings={
            "stability": stability,
            "similarity_boost": similarity_boost,
            "style": style,
            "use_speaker_boost": use_speaker_boost,
            "speed": api_speed
        }
    )
    audio_bytes = b"".join(audio_stream)

    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_mp3(io.BytesIO(audio_bytes))
        if seg.channels > 1:
            seg = seg.set_channels(1)
        sample_rate = seg.frame_rate
        samples = seg.get_array_of_samples()
        audio = np.array(samples, dtype=np.float32) / 32768.0
    except ImportError:
        with io.BytesIO(audio_bytes) as f:
            audio, sample_rate = sf.read(f)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32) / (32768.0 if audio.dtype == np.int16 else 2147483648.0)

    audio, sample_rate = _resample_audio(audio, sample_rate, 48000)
    sf.write(out_file, audio, sample_rate)
    logger.info(f"Wrote ElevenLabs sample to {out_file}")


def _generate_openai(text: str, voice: str, speed: float, out_file: str):
    """Generate OpenAI voice sample to WAV file."""
    from openai import OpenAI

    settings = load_settings_from_db()
    api_key = settings.get('openai_key', '')
    if not api_key:
        raise ValueError("OpenAI API key not configured")

    client = OpenAI(api_key=api_key)

    response = client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        speed=speed
    )
    # OpenAI returns MP3 by default; write to temp then convert to WAV
    import tempfile
    import numpy as np
    tmp_mp3 = out_file + ".tmp.mp3"
    response.stream_to_file(tmp_mp3)

    try:
        # Read the MP3 and write as WAV at 48kHz
        audio, sample_rate = sf.read(tmp_mp3, dtype='float32')
        audio, sample_rate = _resample_audio(audio, sample_rate, 48000)
        sf.write(out_file, audio, sample_rate)
    finally:
        if os.path.exists(tmp_mp3):
            os.remove(tmp_mp3)

    logger.info("Wrote OpenAI sample to %s", out_file)


# Module-level cache for Qwen3 model (loading takes 10-30s, reuse across calls)
_qwen3_model_cache = {"model": None, "model_name": None, "device": None}
# Separate cache for the Base model (needed for voice cloning — CustomVoice model can't clone)
_qwen3_base_model_cache = {"model": None, "device": None}


def _get_qwen3_model():
    """Return a cached Qwen3TTSModel, loading it only on first call or config change."""
    from qwen_tts import Qwen3TTSModel

    settings = load_settings_from_db()
    model_name = (settings.get("qwen3_model_name") or "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice").strip()
    device = (settings.get("qwen3_device") or "").strip() or None

    # Prefer local model in distr/core/agent/models/qwen3-tts/ (no HF cache validation)
    _local_model_dir = os.path.join(
        os.path.dirname(__file__), '..', '..', '..', 'distr', 'core', 'agent', 'models', 'qwen3-tts'
    )
    _local_model_dir = os.path.abspath(_local_model_dir)
    if os.path.isfile(os.path.join(_local_model_dir, 'config.json')):
        model_name = _local_model_dir

    if device is None:
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda:0"
            else:
                # MPS crashes on Qwen3-TTS grouped-query attention (16 Q heads vs 8 KV heads)
                # — MPSGraph matmul can't handle mismatched head counts. Force CPU.
                device = "cpu"
        except ImportError:
            device = "cpu"

    # Return cached model if config hasn't changed
    if (_qwen3_model_cache["model"] is not None
            and _qwen3_model_cache["model_name"] == model_name
            and _qwen3_model_cache["device"] == device):
        return _qwen3_model_cache["model"]

    try:
        import torch
        # MPS (Apple Silicon) matmul crashes with bfloat16 on grouped-query attention
        # (different num_heads vs num_kv_heads triggers "incompatible dimensions" in MPSGraph).
        # Use bfloat16 only on CUDA; float32 everywhere else.
        dtype = torch.bfloat16 if device is not None and device.startswith("cuda") else torch.float32
    except ImportError:
        dtype = None

    load_kwargs = {"device_map": device}
    if dtype is not None:
        load_kwargs["dtype"] = dtype

    logger.info("Loading Qwen3-TTS model %s on %s (first call or config change)...", model_name, device)
    model = Qwen3TTSModel.from_pretrained(model_name, **load_kwargs)
    _qwen3_model_cache.update({"model": model, "model_name": model_name, "device": device})
    logger.info("Qwen3-TTS model loaded and cached.")
    return model


def _get_qwen3_base_model():
    """Return a cached Qwen3-TTS Base model for voice cloning.

    The CustomVoice model (used for preset speakers) does NOT support
    generate_voice_clone(). The Base model is required for that.
    Loaded lazily on first clone request and cached for reuse.
    """
    from qwen_tts import Qwen3TTSModel

    settings = load_settings_from_db()
    device = (settings.get("qwen3_device") or "").strip() or None

    if device is None:
        try:
            import torch
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    if (_qwen3_base_model_cache["model"] is not None
            and _qwen3_base_model_cache["device"] == device):
        return _qwen3_base_model_cache["model"]

    try:
        import torch
        dtype = torch.bfloat16 if device is not None and device.startswith("cuda") else torch.float32
    except ImportError:
        dtype = None

    load_kwargs = {"device_map": device}
    if dtype is not None:
        load_kwargs["dtype"] = dtype

    # Prefer local base model in distr/core/agent/models/qwen3-tts-base/
    _local_base_dir = os.path.join(
        os.path.dirname(__file__), '..', '..', '..', 'distr', 'core', 'agent', 'models', 'qwen3-tts-base'
    )
    _local_base_dir = os.path.abspath(_local_base_dir)
    if os.path.isfile(os.path.join(_local_base_dir, 'config.json')):
        base_model_name = _local_base_dir
    else:
        base_model_name = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    logger.info("Loading Qwen3-TTS Base model %s on %s for voice cloning...", base_model_name, device)
    model = Qwen3TTSModel.from_pretrained(base_model_name, **load_kwargs)
    _qwen3_base_model_cache.update({"model": model, "device": device})
    logger.info("Qwen3-TTS Base model loaded and cached.")
    return model


def _generate_qwen3(text: str, voice: str, speed: float, out_file: str):
    """Generate Qwen3-TTS voice sample locally via the qwen-tts package and write WAV file.
    Supports custom cloned voices when voice starts with 'custom_'."""
    import numpy as np

    try:
        from qwen_tts import Qwen3TTSModel  # noqa: F401 — validates package is installed
    except ImportError:
        raise ImportError("qwen-tts package is required. Install with: pip install qwen-tts")

    model = _get_qwen3_model()

    # Custom voice cloning: voice ID is "custom_<db_id>"
    if voice.startswith("custom_"):
        wavs, sr = _generate_qwen3_clone(model, text, voice)
    else:
        wavs, sr = model.generate_custom_voice(
            text=text,
            language="Auto",
            speaker=voice,
        )

    audio = wavs[0]
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)

    # Resample to 24000 Hz (matches SAMPLE_RATE_QWEN3 and what pydub/ffmpeg handle well)
    audio, sr = _resample_audio(audio, sr, 24000)
    sf.write(out_file, audio, sr)
    logger.info("Wrote Qwen3-TTS sample to %s", out_file)


def _get_cached_voice_prompt(audio_dir: str, base_model):
    """Load a cached VoiceClonePromptItem from disk, or return None if not cached."""
    cache_path = os.path.join(audio_dir, "voice_prompt.pt")
    if not os.path.isfile(cache_path):
        return None
    try:
        import torch
        data = torch.load(cache_path, map_location=base_model.device, weights_only=True)
        from qwen_tts import VoiceClonePromptItem
        item = VoiceClonePromptItem(
            ref_code=data["ref_code"],
            ref_spk_embedding=data["ref_spk_embedding"].to(base_model.device),
            x_vector_only_mode=bool(data.get("x_vector_only_mode", False)),
            icl_mode=bool(data.get("icl_mode", True)),
            ref_text=data.get("ref_text"),
        )
        logger.info("Loaded cached voice prompt from %s", cache_path)
        return item
    except Exception as e:
        logger.warning("Failed to load cached voice prompt %s: %s", cache_path, e)
        return None


def bake_voice_prompt(voice_id: int):
    """Pre-compute and cache the VoiceClonePromptItem for a Qwen3 custom voice.

    Call this once after voice creation so subsequent TTS calls skip the
    expensive audio-processing step. Saves a .pt file in the voice's audio_dir.
    """
    import torch
    from distr.core.db import get_session, CustomVoice

    session = get_session()
    try:
        cv = session.query(CustomVoice).filter(CustomVoice.id == voice_id).first()
        if not cv or cv.provider != "qwen3" or cv.status != "ready":
            return
        audio_dir = cv.audio_dir
        ref_text = cv.system_prompt or ""
    finally:
        session.close()

    if not audio_dir or not os.path.isdir(audio_dir):
        return

    ref_files = [
        os.path.join(audio_dir, f)
        for f in sorted(os.listdir(audio_dir))
        if f.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.webm'))
    ]
    if not ref_files:
        return

    base_model = _get_qwen3_base_model()
    logger.info("Baking voice prompt for custom voice %d from %s", voice_id, ref_files[0])

    items = base_model.create_voice_clone_prompt(
        ref_audio=ref_files[0],
        ref_text=ref_text if ref_text else None,
        x_vector_only_mode=not bool(ref_text),
    )
    item = items[0]

    cache_path = os.path.join(audio_dir, "voice_prompt.pt")
    torch.save({
        "ref_code": item.ref_code,
        "ref_spk_embedding": item.ref_spk_embedding.cpu(),
        "x_vector_only_mode": item.x_vector_only_mode,
        "icl_mode": item.icl_mode,
        "ref_text": item.ref_text,
    }, cache_path)
    logger.info("Cached voice prompt to %s", cache_path)


def _generate_qwen3_clone(model, text: str, voice_id: str):
    """Generate speech using a custom cloned voice. Loads reference audio from DB.

    Uses the Base model (not the CustomVoice model) because only the Base
    variant supports generate_voice_clone().
    Uses a cached voice prompt (.pt) when available to skip re-processing audio.
    """
    import os

    # Extract DB id from "custom_<id>"
    db_id_str = voice_id.split("_", 1)[1]
    try:
        db_id = int(db_id_str)
    except ValueError:
        raise ValueError(f"Invalid custom voice ID: {voice_id}")

    from distr.core.db import get_session, CustomVoice
    session = get_session()
    try:
        cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
        if not cv:
            raise ValueError(f"Custom voice {db_id} not found")
        if cv.status != "ready":
            raise ValueError(f"Custom voice {cv.name} is not ready (status: {cv.status})")
        audio_dir = cv.audio_dir
        system_prompt = cv.system_prompt or ""
    finally:
        session.close()

    if not audio_dir or not os.path.isdir(audio_dir):
        raise ValueError(f"Audio directory not found for custom voice {db_id}")

    # Load the Base model (CustomVoice model doesn't support generate_voice_clone)
    base_model = _get_qwen3_base_model()

    # Try cached voice prompt first (instant) — falls back to raw audio (slow)
    cached_prompt = _get_cached_voice_prompt(audio_dir, base_model)
    if cached_prompt is not None:
        logger.info("Using cached voice prompt for custom voice %d", db_id)
        wavs, sr = base_model.generate_voice_clone(
            text=text,
            language="Auto",
            voice_clone_prompt=[cached_prompt],
        )
        return wavs, sr

    # Fallback: process from raw audio
    ref_files = [
        os.path.join(audio_dir, f)
        for f in sorted(os.listdir(audio_dir))
        if f.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.webm'))
    ]
    if not ref_files:
        raise ValueError(f"No reference audio files found for custom voice {db_id}")

    ref_audio = ref_files[0]
    logger.info("Generating Qwen3-TTS clone with ref=%s prompt=%s (no cache)", ref_audio, system_prompt[:50])

    wavs, sr = base_model.generate_voice_clone(
        text=text,
        language="Auto",
        ref_audio=ref_audio,
        ref_text=system_prompt if system_prompt else None,
    )
    return wavs, sr


def _generate_coqui(text: str, voice: str, speed: float, out_file: str):
    """Generate Coqui TTS (VCTK multi-speaker) voice sample and write WAV file."""
    import numpy as np

    try:
        from TTS.api import TTS as CoquiTTS
    except ImportError:
        raise ImportError("TTS package is required for Coqui TTS. Install with: pip install TTS")

    tts = CoquiTTS("tts_models/en/vctk/vits", gpu=False)
    wav = tts.tts(text=text, speaker=voice)
    audio = np.array(wav, dtype=np.float32)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    sr = tts.synthesizer.output_sample_rate
    audio, sr = _resample_audio(audio, sr, 48000)
    sf.write(out_file, audio, sr)
    logger.info("Wrote Coqui TTS sample to %s", out_file)
