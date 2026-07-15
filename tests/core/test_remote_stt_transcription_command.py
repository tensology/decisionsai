import queue

from distr.core.agent.command_handler import _cmd_transcribe_file
from distr.core.agent.services.stt.vosk import VoskSTTService


class _DummyLogger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _DummySession:
    def __init__(self, stt_service=None):
        self.stt_service = stt_service
        self.event_queue = queue.Queue()
        self.logger = _DummyLogger()


def _read_event(session):
    event_name, payload = session.event_queue.get_nowait()
    assert event_name == "telegram_transcription_result"
    return payload


def test_transcribe_file_uses_active_stt_service_instance():
    class _STT:
        def __init__(self):
            self.called = 0
            self.last_path = None

        def transcribe_file(self, audio_file_path):
            self.called += 1
            self.last_path = audio_file_path
            return "hello from active stt"

    stt = _STT()
    session = _DummySession(stt_service=stt)

    _cmd_transcribe_file(
        session,
        {
            "audio_file_path": "/tmp/voice.wav",
            "request_id": "req-1",
            "input_type": "voice",
            "source_message_id": "telegram-101",
        },
    )

    assert stt.called == 1
    assert stt.last_path == "/tmp/voice.wav"

    payload = _read_event(session)
    assert payload["request_id"] == "req-1"
    assert payload["success"] is True
    assert payload["transcript"] == "hello from active stt"
    assert payload["input_type"] == "voice"
    assert payload["source_message_id"] == "telegram-101"


def test_transcribe_file_reports_missing_stt_service():
    session = _DummySession(stt_service=None)

    _cmd_transcribe_file(
        session,
        {"audio_file_path": "/tmp/voice.wav", "request_id": "req-2"},
    )

    payload = _read_event(session)
    assert payload["request_id"] == "req-2"
    assert payload["success"] is False
    assert payload["transcript"] is None
    assert "not available" in (payload["error"] or "").lower()


def test_transcribe_file_reports_provider_without_file_support():
    class _NoFileSTT:
        pass

    session = _DummySession(stt_service=_NoFileSTT())

    _cmd_transcribe_file(
        session,
        {"audio_file_path": "/tmp/voice.wav", "request_id": "req-3"},
    )

    payload = _read_event(session)
    assert payload["request_id"] == "req-3"
    assert payload["success"] is False
    assert "does not support file transcription" in (payload["error"] or "").lower()


def test_vosk_service_exposes_transcribe_file():
    assert callable(getattr(VoskSTTService, "transcribe_file", None))
