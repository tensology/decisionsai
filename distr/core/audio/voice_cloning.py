"""
Voice cloning business logic — extracted from route handlers.

Handles ElevenLabs IVC and Kokoro/Kanade cloning.
"""
import logging
import os

logger = logging.getLogger(__name__)


def process_custom_voice(voice_id: int) -> None:
    """Background worker: clone voice via provider API, update DB status."""
    from distr.core.agent.services.tts.registry import tts_registry
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

        try:
            descriptor = tts_registry.get(voice.provider)
        except KeyError:
            voice.status = "failed"
            voice.error_message = f"Unsupported provider: {voice.provider}"
            session.commit()
            return

        try:
            descriptor.clone_voice(voice, audio_files, session)
        except NotImplementedError:
            voice.status = "failed"
            voice.error_message = f"Provider '{voice.provider}' does not support voice cloning"
            session.commit()
            return
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
