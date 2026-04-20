"""
Voice cloning business logic — extracted from route handlers.

Handles ElevenLabs IVC and Kokoro/Kanade cloning.
"""
import logging
import os
import threading
import uuid

logger = logging.getLogger(__name__)
_local_whisper_model = None
_local_whisper_model_name = "base"
_local_whisper_model_lock = threading.Lock()


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


def _transcribe_via_loaded_agent_stt(file_path: str, timeout_seconds: float = 45.0) -> str | None:
    """Route transcription to the already-loaded agent STT service when available."""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if not app or not hasattr(app, "agent_command_queue"):
            return None

        req_id = f"file_stt_{uuid.uuid4()}"
        result_event = threading.Event()
        result_holder = {}

        if not hasattr(app, "_pending_stt_callbacks"):
            app._pending_stt_callbacks = {}
        app._pending_stt_callbacks[req_id] = (result_event, result_holder)

        app.agent_command_queue.put(
            ("transcribe_file", {
                "audio_file_path": file_path,
                "request_id": req_id,
                "input_type": "voice",
            }),
            block=False,
        )

        if result_event.wait(timeout=timeout_seconds):
            transcript = result_holder.get("transcript")
            if transcript:
                return str(transcript).strip()
            err = result_holder.get("error")
            if err:
                logger.warning("Agent STT transcription returned error: %s", err)
        else:
            logger.warning("Timed out waiting for agent STT transcription result")
        app._pending_stt_callbacks.pop(req_id, None)
    except Exception as e:
        logger.debug("Agent STT route unavailable, falling back: %s", e)
    return None


def transcribe_audio_file(file_path: str) -> str:
    """Transcribe an audio file with minimal model reloads.

    Priority:
    1) Already-loaded agent STT service (no model reload).
    2) OpenAI Whisper API.
    3) Cached local whisper model.
    """
    # 1) Try loaded agent STT service first (prevents reloading local models).
    via_agent = _transcribe_via_loaded_agent_stt(file_path)
    if via_agent:
        return via_agent

    # 2) Try OpenAI Whisper API.
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

    # 3) Fallback: local whisper with in-memory model cache.
    try:
        import whisper
        global _local_whisper_model
        with _local_whisper_model_lock:
            if _local_whisper_model is None:
                logger.info("Loading local whisper model '%s' for file transcription", _local_whisper_model_name)
                _local_whisper_model = whisper.load_model(_local_whisper_model_name)
            model = _local_whisper_model
        result = model.transcribe(file_path)
        return (result.get("text") or "").strip()
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Local whisper transcription failed: %s", e)

    raise RuntimeError("No transcription backend available. Enable OpenAI or install whisper.")
