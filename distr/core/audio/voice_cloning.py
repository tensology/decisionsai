"""
Voice cloning business logic — extracted from route handlers.

Handles ElevenLabs IVC, Qwen3 voice prompt baking, and Kokoro/Kanade cloning.
"""
import logging
import os

logger = logging.getLogger(__name__)


def process_custom_voice(voice_id: int) -> None:
    """Background worker: clone voice via provider API, update DB status."""
    from distr.core.db import get_session, CustomVoice

    session = get_session()
    try:
        voice = session.query(CustomVoice).filter(CustomVoice.id == voice_id).first()
        if not voice:
            return
        voice.status = "processing"
        session.commit()

        audio_files = []
        if voice.audio_dir and os.path.isdir(voice.audio_dir):
            audio_files = [
                os.path.join(voice.audio_dir, f)
                for f in os.listdir(voice.audio_dir)
                if f.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.webm'))
            ]

        if not audio_files:
            voice.status = "failed"
            voice.error_message = "No audio files found"
            session.commit()
            return

        if voice.provider == "elevenlabs":
            _clone_elevenlabs(voice, audio_files, session)
        elif voice.provider == "qwen3":
            _clone_qwen3(voice, audio_files, session)
        elif voice.provider == "kokoro":
            _clone_kokoro(voice, audio_files, session)
        else:
            voice.status = "failed"
            voice.error_message = f"Unsupported provider: {voice.provider}"
            session.commit()
    except Exception as e:
        logger.error("Custom voice processing failed for id=%s: %s", voice_id, e, exc_info=True)
        try:
            voice.status = "failed"
            voice.error_message = str(e)[:500]
            session.commit()
        except Exception:
            pass
    finally:
        session.close()


def _clone_elevenlabs(voice, audio_files, session) -> None:
    """Clone voice via ElevenLabs Instant Voice Cloning (IVC) API."""
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    api_key = (settings.get("elevenlabs_key") or "").strip()
    if not api_key:
        voice.status = "failed"
        voice.error_message = "ElevenLabs API key not configured"
        session.commit()
        return

    from elevenlabs import ElevenLabs
    client = ElevenLabs(api_key=api_key)
    description = f"Custom voice: {voice.name}"

    file_handles = []
    try:
        for path in audio_files:
            file_handles.append(open(path, 'rb'))

        result = client.voices.ivc.create(
            name=voice.name,
            description=description,
            files=file_handles,
        )
        voice.provider_voice_id = result.voice_id
        voice.status = "ready"
        session.commit()
        logger.info("ElevenLabs custom voice created: %s -> %s", voice.name, result.voice_id)
    finally:
        for fh in file_handles:
            fh.close()


def _clone_qwen3(voice, audio_files, session) -> None:
    """Register Qwen3 custom voice and bake a cached voice prompt."""
    voice.provider_voice_id = f"custom_{voice.id}"
    voice.status = "ready"
    session.commit()
    logger.info("Qwen3 custom voice registered: %s -> %s", voice.name, voice.provider_voice_id)

    try:
        from distr.core.audio.tts_handler import bake_voice_prompt
        bake_voice_prompt(voice.id)
    except Exception as e:
        logger.warning("Failed to bake voice prompt for %s (will process at inference time): %s", voice.name, e)


def _clone_kokoro(voice, audio_files, session) -> None:
    """Register Kokoro custom voice for Kanade voice cloning.

    Converts any non-WAV audio files to WAV using pydub (ffmpeg backend).
    """
    from pydub import AudioSegment

    _NATIVE_EXTS = {'.wav', '.flac', '.ogg'}

    for fpath in audio_files:
        ext = os.path.splitext(fpath)[1].lower()
        if ext not in _NATIVE_EXTS:
            wav_path = os.path.splitext(fpath)[0] + '.wav'
            logger.info("Kokoro clone: converting %s -> %s", os.path.basename(fpath), os.path.basename(wav_path))
            audio_seg = AudioSegment.from_file(fpath)
            audio_seg = audio_seg.set_channels(1).set_frame_rate(24000).set_sample_width(2)
            audio_seg.export(wav_path, format='wav')
            try:
                os.remove(fpath)
            except OSError:
                pass

    voice.provider_voice_id = f"custom_{voice.id}"
    voice.status = "ready"
    session.commit()
    logger.info("Kokoro custom voice registered: %s -> %s (Kanade voice cloning)", voice.name, voice.provider_voice_id)


async def transcribe_audio_file(file_path: str) -> str:
    """Transcribe an audio file using the best available STT backend."""
    # Try OpenAI Whisper API first
    try:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        openai_key = (settings.get("openai_key") or "").strip()
        if openai_key:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            with open(file_path, "rb") as f:
                result = client.audio.transcriptions.create(model="whisper-1", file=f)
            return result.text.strip()
    except Exception as e:
        logger.warning("OpenAI Whisper transcription failed, trying fallback: %s", e)

    # Fallback: local whisper
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(file_path)
        return (result.get("text") or "").strip()
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Local whisper transcription failed: %s", e)

    raise RuntimeError("No transcription backend available. Enable OpenAI or install whisper.")
