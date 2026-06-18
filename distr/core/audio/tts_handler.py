"""TTS Handler for generating voice samples from web settings."""
import os
import re
import hashlib
import logging
from distr.core.paths import TMP_DIR
from distr.core.utils import load_settings_from_db
from distr.core.agent.services.tts.registry import tts_registry

logger = logging.getLogger(__name__)


def _tts_provider_to_internal(provider: str) -> str:
    """Map display or settings provider name to internal name (kokoro, elevenlabs, openai, etc.).

    Delegates to constants.normalize_voice_provider() which handles all known
    display-name variants and partial matches.
    """
    from distr.core.agent.constants import normalize_voice_provider
    return normalize_voice_provider(provider)


def _normalize_voice_for_provider(provider: str, voice: str, settings: dict) -> str:
    """Resolve mixed/stale voice labels into valid provider voice IDs.

    Delegates to the provider descriptor's normalize_voice() method via the registry.
    """
    raw = (voice or "").strip()
    prov = _tts_provider_to_internal(provider)

    try:
        descriptor = tts_registry.get(prov)
        return descriptor.normalize_voice(raw, settings)
    except KeyError:
        # Unknown provider — pass through raw voice unchanged
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
    from distr.core.agent.services.llm.text_utils import clean_text_for_tts
    settings = load_settings_from_db()
    prov = _tts_provider_to_internal(provider or settings.get("tts_provider", "Kokoro (Offline)"))
    if voice is None:
        # Resolve default voice from settings via registry descriptor
        try:
            descriptor = tts_registry.get(prov)
            voice = (settings.get(descriptor.settings_key) or descriptor.default_voice).strip()
        except KeyError:
            voice = "af_heart"
    else:
        voice = voice.strip()
    voice = _normalize_voice_for_provider(prov, voice, settings)
    text = (text or "").strip()
    if not text:
        raise ValueError("Text is required for TTS")
    # Clean text for TTS (remove markdown, emojis, etc.) to match agent's TTS pipeline
    text = clean_text_for_tts(text)
    if not text:
        raise ValueError("Text is required for TTS (after cleaning)")
    speed = max(0.5, min(2.0, float(speed)))
    os.makedirs(TMP_DIR, exist_ok=True)
    cache_key = hashlib.md5(f"{text}:{prov}:{voice}:{speed}".encode()).hexdigest()[:12]
    out_file = os.path.join(TMP_DIR, f"tts_chat_{prov}_{cache_key}.wav")
    if prov not in ("elevenlabs", "coqui") and os.path.exists(out_file):
        logger.debug("Using cached TTS: %s", out_file)
        return out_file
    logger.info("Generating TTS: provider=%s, voice=%s, speed=%s", prov, voice, speed)
    # Use registry-based dispatch, fall back to legacy functions for unknown providers
    try:
        descriptor = tts_registry.get(prov)
        descriptor.generate_audio(text, voice, speed, out_file)
    except KeyError:
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
    raw_voice = (voice or "").strip()
    voice = _normalize_voice_for_provider(provider, raw_voice, settings)
    if raw_voice != voice:
        logger.info(
            "play-voice: voice id normalized provider=%s raw=%r -> %r "
            "(saved settings may override unknown dropdown ids until you click Save)",
            provider,
            raw_voice,
            voice,
        )
    display_name = _resolve_display_name(provider, voice, voice_name)
    # Sanitize display name — phonemizer/espeak can't handle newlines or special chars
    display_name = re.sub(r'\s+', ' ', display_name).strip()
    pixazo_dit_steps = None
    if provider == "pixazo":
        from distr.core.pixazo_client import pixazo_dit_steps_from_settings

        pixazo_dit_steps = pixazo_dit_steps_from_settings(settings)
        test_text = f"Hi, I'm {display_name}. Ready when you are."
    else:
        test_text = (
            f"Hi there, my name is {display_name}. I would love to help you get things done. "
            "Save this voice and let's get started."
        )

    os.makedirs(TMP_DIR, exist_ok=True)
    cache_seed = f"{test_text}:{speed}"
    if pixazo_dit_steps is not None:
        cache_seed = f"{cache_seed}:{pixazo_dit_steps}"
    text_hash = hashlib.md5(cache_seed.encode()).hexdigest()[:8]
    out_file = os.path.join(TMP_DIR, f"tts_{provider}_{voice}_{text_hash}_web.wav")

    if provider not in ("elevenlabs", "coqui") and not voice.startswith("custom_") and os.path.exists(out_file):
        logger.info("Using cached voice sample: %s", out_file)
        return out_file

    logger.info(
        "Generating voice sample: provider=%s voice=%s speed=%s out_file=%s",
        provider,
        voice,
        speed,
        out_file,
    )

    # Use registry-based dispatch, fall back gracefully for unknown providers
    try:
        descriptor = tts_registry.get(provider)
        descriptor.generate_audio(test_text, voice, speed, out_file)
    except KeyError:
        raise ValueError(f"Unknown TTS provider: {provider}")

    return out_file


def _resolve_display_name(provider: str, voice: str, voice_name: str = None) -> str:
    """Resolve a clean display name for the voice, with server-side fallback.

    Delegates to the provider descriptor's resolve_display_name() method via the registry.
    """
    # Universal short-circuit: if a clean display name was already provided, use it directly.
    # This preserves the original behavior where voice_name without underscores was returned as-is.
    if voice_name and voice_name.strip() and '_' not in voice_name:
        cleaned = voice_name.strip().lstrip('⭐').strip()
        if cleaned:
            return cleaned

    try:
        descriptor = tts_registry.get(provider)
        settings = load_settings_from_db()
        return descriptor.resolve_display_name(voice, settings, voice_name)
    except KeyError:
        # Unknown provider — best-effort fallback
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
