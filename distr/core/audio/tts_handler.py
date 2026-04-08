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
    if p in ("coqui", "coqui tts (offline)", "coqui tts"):
        return "coqui"
    if p in ("f5tts", "f5-tts", "f5-tts (offline)", "f5 tts (offline)", "f5 tts"):
        return "f5tts"
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

    # ElevenLabs is resolved again inside _generate_elevenlabs; keep raw here.
    # F5-TTS: custom voices pass through, default is "default"
    if prov == "f5tts":
        if raw.startswith("custom_"):
            return raw
        return raw if raw else "default"

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
        elif prov == "coqui":
            voice = (settings.get("coqui_voice") or "p225").strip()
        elif prov == "f5tts":
            voice = (settings.get("f5tts_voice") or "default").strip()
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
    if prov not in ("elevenlabs", "coqui") and os.path.exists(out_file):
        logger.debug("Using cached TTS: %s", out_file)
        return out_file
    logger.info("Generating TTS: provider=%s, voice=%s, speed=%s", prov, voice, speed)
    if prov == "kokoro":
        _generate_kokoro(text, voice, speed, out_file)
    elif prov == "elevenlabs":
        _generate_elevenlabs(text, voice, speed, out_file)
    elif prov == "openai":
        _generate_openai(text, voice, speed, out_file)
    elif prov == "coqui":
        _generate_coqui(text, voice, speed, out_file)
    elif prov == "f5tts":
        _generate_f5tts(text, voice, speed, out_file)
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

    if provider not in ("elevenlabs", "coqui") and not voice.startswith("custom_") and os.path.exists(out_file):
        logger.info("Using cached voice sample: %s", out_file)
        return out_file

    logger.info("Generating %s voice sample: voice=%s, speed=%s", provider, voice, speed)

    if provider == 'kokoro':
        _generate_kokoro(test_text, voice, speed, out_file)
    elif provider == 'elevenlabs':
        _generate_elevenlabs(test_text, voice, speed, out_file)
    elif provider == 'openai':
        _generate_openai(test_text, voice, speed, out_file)
    elif provider == 'coqui':
        _generate_coqui(test_text, voice, speed, out_file)
    elif provider == 'f5tts':
        _generate_f5tts(test_text, voice, speed, out_file)
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

    if provider == 'coqui':
        try:
            from distr.core.agent.constants import COQUI_VOICES
            return COQUI_VOICES.get(voice, voice)
        except Exception:
            pass
        return voice or "Sarah"

    if provider == 'f5tts':
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
        return voice.capitalize() if voice and voice != 'default' else "F5-TTS"

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


def _generate_f5tts(text: str, voice: str, speed: float, out_file: str):
    """Generate F5-TTS voice sample to WAV file. Supports voice cloning via reference audio."""
    import numpy as np

    # Uninstall torchcodec if it somehow got reinstalled — it's broken on macOS with torch 2.x
    try:
        import torchcodec  # noqa: F401
        logger.warning("F5-TTS: torchcodec is installed but broken on this system — ignoring")
    except Exception:
        pass

    try:
        from f5_tts.api import F5TTS
    except ImportError:
        raise ImportError("f5-tts is required. Install with: pip install f5-tts")
    except OSError as e:
        if "torchcodec" in str(e) or "libtorchcodec" in str(e):
            raise RuntimeError(
                "torchcodec is causing a conflict. Run: pip uninstall torchcodec -y"
            ) from e
        raise

    # Resolve reference audio and text
    ref_audio = None
    ref_text = None

    if voice and voice.startswith("custom_"):
        try:
            from distr.core.db import get_session, CustomVoice
            db_id = int(voice.split("_", 1)[1])
            session = get_session()
            try:
                cv = session.query(CustomVoice).filter(
                    CustomVoice.id == db_id, CustomVoice.provider == "f5tts", CustomVoice.status == "ready"
                ).first()
                if cv and cv.audio_dir and os.path.isdir(cv.audio_dir):
                    for fname in os.listdir(cv.audio_dir):
                        if fname.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac')):
                            ref_audio = os.path.join(cv.audio_dir, fname)
                            break
                    ref_text = getattr(cv, 'system_prompt', None) or None
            finally:
                session.close()
        except Exception as e:
            logger.warning("Failed to resolve F5-TTS custom voice %s: %s", voice, e)
        if not ref_audio:
            raise FileNotFoundError(f"No reference audio found for custom voice {voice}")

    # Fall back to default reference audio bundled with f5-tts
    if not ref_audio:
        try:
            import f5_tts
            pkg_dir = os.path.dirname(f5_tts.__file__)
            candidate = os.path.join(pkg_dir, "infer", "examples", "basic", "basic_ref_en.wav")
            if os.path.isfile(candidate):
                ref_audio = candidate
                ref_text = "Some call me nature, others call me mother nature."
        except Exception:
            pass

    if not ref_audio:
        raise FileNotFoundError(
            "F5-TTS requires a reference audio file. "
            "Upload a custom voice or ensure f5-tts is installed with its example files."
        )

    clamped_speed = max(0.5, min(2.0, speed))
    text = re.sub(r'\s+', ' ', text).strip()

    model = F5TTS()
    wav, sr, _ = model.infer(
        ref_file=ref_audio,
        ref_text=ref_text or "",
        gen_text=text,
        speed=clamped_speed,
    )

    audio = np.array(wav, dtype=np.float32)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    audio, sr = _resample_audio(audio, sr, 48000)
    sf.write(out_file, audio, sr)
    logger.info("Wrote F5-TTS sample to %s", out_file)
