from pathlib import Path
import logging
import queue

from distr.app.events import EventHandlerMixin
from distr.core.integrations.bus import IntegrationMessageBus
from distr.core.integrations.telegram.messages import TelegramMessagesMixin
from distr.core.integrations.telegram.sender import TelegramSenderMixin, _audit_outbound_telegram_text


class ImmediateThread:
    def __init__(self, target, daemon=None, name=None):
        self.target = target

    def start(self):
        self.target()


class DummyTelegramManager:
    def __init__(self, connected=False):
        self.connected = connected
        self.sent = []
        self.stopped_typing = False

    def is_connected(self):
        return self.connected

    def send_to_telegram(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return True

    def _stop_typing_loop(self):
        self.stopped_typing = True


class DummyApp(EventHandlerMixin):
    def __init__(self, telegram_manager):
        self.telegram_manager = telegram_manager
        self.worker_calls = []

    def _send_to_telegram_worker(self, data):
        self.worker_calls.append(data)


class WorkerApp(EventHandlerMixin):
    def __init__(self, telegram_manager):
        self.telegram_manager = telegram_manager
        self.generated_tts = []
        self.cleaned = []

    def _try_route_integration_text_reply(self, text_to_send, data):
        return False

    def _telegram_generate_tts(self, text):
        self.generated_tts.append(text)
        return None

    def _telegram_cleanup_temp_files(self, audio_file, screenshot_file, analyzed_image_path):
        self.cleaned.append((audio_file, screenshot_file, analyzed_image_path))


class DummyTelegramSender(TelegramSenderMixin):
    def __init__(self):
        self.telegram_user_id = 12345
        self.chat_id = None
        self.app_user_id = None
        self._last_send_time = 0
        self._last_message_time = 0
        self._min_send_interval = 0
        self._dedup_window = 30
        self._recent_messages = {}
        self._message_queue = queue.Queue()

    def _stop_typing_loop(self):
        pass


def test_send_to_telegram_event_reaches_worker_when_socket_disconnected(monkeypatch):
    app = DummyApp(DummyTelegramManager(connected=False))
    monkeypatch.setattr("distr.app.events.threading.Thread", ImmediateThread)

    app._evt_send_to_telegram({
        "text": "answer",
        "provider": "kokoro",
        "is_done": False,
    })

    assert app.worker_calls == [{
        "text": "answer",
        "provider": "kokoro",
        "is_done": False,
    }]


def test_explicit_voice_note_keeps_prebuilt_audio_in_text_response_mode(monkeypatch, tmp_path):
    import distr.app.events as events

    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"RIFFfake")
    app = DummyApp(DummyTelegramManager(connected=False))

    monkeypatch.setattr(events, "load_settings_from_db", lambda: {})
    monkeypatch.setattr(events, "load_response_format_settings", lambda _settings: (False, True))

    text, audio_file, screenshot_file = app._telegram_prepare_llm_response(
        {"text": "", "input_type": "text", "is_voice_note": True},
        "",
        audio_path,
        None,
        str(audio_path),
        None,
        None,
    )

    assert text is None
    assert audio_file == audio_path
    assert screenshot_file is None


def test_saved_status_is_silent_and_never_generates_voice(monkeypatch):
    import distr.app.events as events
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.notification_routing import reset_notification_activity

    reset_engagement_ledger()
    reset_notification_activity()
    manager = DummyTelegramManager(connected=False)
    app = WorkerApp(manager)
    payload = {
        "text": "Screen Compliment saved successfully - I saved it in Decisions.",
        "provider": "kokoro",
        "is_done": False,
        "input_type": "voice",
    }
    monkeypatch.setattr(events, "load_settings_from_db", lambda: {})
    monkeypatch.setattr(events, "load_response_format_settings", lambda _settings: (False, True))

    app._send_to_telegram_worker(dict(payload))
    app._send_to_telegram_worker(dict(payload))

    assert manager.sent == []
    assert app.generated_tts == []


def test_screenshot_evidence_status_is_silent(monkeypatch):
    import distr.app.events as events
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.notification_routing import reset_notification_activity

    reset_engagement_ledger()
    reset_notification_activity()
    manager = DummyTelegramManager(connected=False)
    app = WorkerApp(manager)
    payload = {
        "text": "Screenshot captured and evidence stored in Decisions.",
        "provider": "kokoro",
        "is_done": False,
        "input_type": "voice",
    }
    monkeypatch.setattr(events, "load_settings_from_db", lambda: {})
    monkeypatch.setattr(events, "load_response_format_settings", lambda _settings: (False, True))

    app._send_to_telegram_worker(dict(payload))

    assert manager.sent == []
    assert app.generated_tts == []


def test_voice_note_tool_without_event_queue_does_not_emit_nonetype_error(monkeypatch):
    from distr.core import signals as signals_mod
    from distr.core.agent.tools.integrations.send_voice_note_to_telegram import (
        SendVoiceNoteToTelegramTool,
    )

    monkeypatch.setattr(signals_mod, "_agent_event_queue", None)

    result = SendVoiceNoteToTelegramTool(event_queue=None)._run("Hello")

    assert "delivery bridge is unavailable" in result
    assert "NoneType" not in result


def test_kokoro_descriptor_splits_telegram_voice_text_into_safe_chunks():
    from distr.core.agent.services.tts.kokoro_descriptor import _split_text_for_kokoro

    text = (
        "Workflow finished successfully: it sent the Telegram voice note you asked for, "
        "and the spoken update should stay natural instead of reading raw workflow logs. "
        + "This final sentence is deliberately long " * 30
    )

    chunks = _split_text_for_kokoro(text, max_chars=160)

    assert chunks
    assert all(chunk.strip() for chunk in chunks)
    assert all(len(chunk) <= 160 for chunk in chunks)
    assert "Telegram voice note" in " ".join(chunks)


def test_done_response_does_not_auto_attach_screenshot_without_explicit_artifact_intent(tmp_path):
    app = DummyApp(DummyTelegramManager(connected=False))

    text, audio_file, screenshot_file = app._telegram_prepare_done_response(
        {"text": "Done", "is_done": True},
        "Done",
        None,
        None,
        None,
        None,
        None,
    )

    assert text == "Done"
    assert audio_file is None
    assert screenshot_file is None


def test_file_send_event_skips_without_explicit_artifact_intent(tmp_path):
    manager = DummyTelegramManager(connected=False)
    app = DummyApp(manager)
    target = tmp_path / "report.pdf"
    target.write_bytes(b"pdf")

    app._evt_send_file_to_telegram({
        "file_path": str(target),
        "file_name": "report.pdf",
        "file_type": "document",
    })

    assert manager.sent == []


def test_file_send_event_sends_when_artifact_was_explicitly_requested(tmp_path):
    manager = DummyTelegramManager(connected=False)
    app = DummyApp(manager)
    target = tmp_path / "report.pdf"
    target.write_bytes(b"pdf")

    app._evt_send_file_to_telegram({
        "file_path": str(target),
        "file_name": "report.pdf",
        "file_type": "document",
        "explicit_artifact_intent": True,
    })

    assert manager.sent
    args, kwargs = manager.sent[0]
    assert kwargs["document_path"] == str(target)
    assert "report.pdf" in kwargs["text"]


def test_file_send_event_skips_empty_files(tmp_path):
    manager = DummyTelegramManager(connected=False)
    app = DummyApp(manager)
    target = tmp_path / "empty.md"
    target.write_bytes(b"")

    app._evt_send_file_to_telegram({
        "file_path": str(target),
        "file_name": "empty.md",
        "file_type": "document",
    })

    assert manager.sent == []


def test_telegram_outbound_text_removes_markdown_noise():
    text = (
        "Quick update: ## Quick Check-in\n"
        "- I found 4 thing(s) that may need your call.\n"
        "- Listening to: Telegram, WhatsApp.\n"
        "## Needs Your Call\n"
        "1. My Board has 4 backlog item(s)."
    )

    clean = _audit_outbound_telegram_text(text)

    assert clean.startswith("Quick check-in:")
    assert "##" not in clean
    assert "**" not in clean
    assert "- " not in clean


def test_telegram_outbound_text_simplifies_lifecycle_status():
    assert _audit_outbound_telegram_text("Heart says goodbye! DecisionsAI has shut down.") == "Goodbye."
    assert _audit_outbound_telegram_text("Hello there! Welcome back! What would you like to continue with?") == "I'm back online."


def test_duplicate_outbound_message_drop_is_not_warning_noise(caplog):
    sender = DummyTelegramSender()

    with caplog.at_level(logging.WARNING, logger="distr.core.integrations.telegram.sender"):
        assert sender.send_to_telegram("same message") is True
        assert sender.send_to_telegram("same message") is False

    assert "Duplicate message dropped" not in caplog.text


def test_direct_outbound_low_value_status_is_suppressed():
    sender = DummyTelegramSender()

    result = sender.send_to_telegram("Screenshot captured and evidence stored in Decisions.")

    assert result is False
    assert sender._message_queue.empty()


def test_voice_transcription_failure_notifies_telegram():
    manager = DummyTelegramManager(connected=False)
    app = DummyApp(manager)

    app._evt_telegram_transcription({
        "request_id": "voice-1",
        "success": False,
        "error": "Transcription returned None",
        "input_type": "voice",
    })

    assert manager.stopped_typing is True
    assert manager.sent
    args, kwargs = manager.sent[0]
    assert "couldn't transcribe" in args[0]
    assert kwargs == {}


def test_telegram_bus_preserves_input_type_metadata(tmp_path):
    seen = []
    bus = IntegrationMessageBus(mapping_path=Path(tmp_path) / "bus.json")
    bus.set_text_sink(lambda text, is_telegram, image_path, speak: seen.append(
        (text, is_telegram, image_path, speak)
    ))

    bus.deliver_telegram_user_input(text="hello", input_type="voice")

    assert seen == [("hello", True, None, {"speak": None, "input_type": "voice", "surface": "telegram"})]


class DummyTelegramMessages(TelegramMessagesMixin):
    def __init__(self):
        self._processed_message_ids = set()
        self._processed_message_hashes = set()
        self._max_processed_cache_size = 100
        self.chat_id = None
        self.telegram_user_id = None
        self.marked_read = []
        self.enqueued = []
        self.files = []
        self.sent = []
        self._pending_telegram_media_context = None
        self._telegram_batch_buffer = []
        self.message_received = type("Signal", (), {"emit": lambda _self, data: None})()

    def _get_chat_id(self):
        return self.chat_id

    def send_to_telegram(self, text):
        self.sent.append(text)
        return True

    def _update_stored_connection_data(self, **kwargs):
        pass

    def _track_telegram_group(self, *args, **kwargs):
        pass

    def _mark_message_as_read(self, message_id):
        self.marked_read.append(message_id)

    def _enqueue_telegram_batch(self, text, image_path=None, input_type="text"):
        self._telegram_batch_buffer.append((text, bool(image_path), image_path, input_type))
        self.enqueued.append((text, image_path, input_type))

    def _handle_voice_message(self, url, media_type, message_id=None):
        self.enqueued.append((url, None, media_type))

    def _handle_file_message(self, media, caption=None):
        self.files.append((media, caption))


def test_private_text_is_marked_read_on_receipt():
    manager = DummyTelegramMessages()

    manager._handle_telegram_message({"data": {
        "message_id": 101,
        "chat_id": 12345,
        "chat": {"type": "private"},
        "text": "hello",
    }})

    assert manager.marked_read == [101]
    assert manager.enqueued == [("hello", None, "text")]


def test_remote_command_uses_telegram_user_id_when_chat_id_not_ready():
    manager = DummyTelegramMessages()
    manager.telegram_user_id = 12345

    manager._handle_telegram_message({"data": {
        "message_id": 105,
        "chat_id": 12345,
        "chat": {"type": "private"},
        "text": "remote",
    }})

    assert manager.marked_read == [105]
    assert len(manager.sent) == 1
    assert "https://www.decisionsai.net/api/remote/?channel=" in manager.sent[0]
    assert manager.enqueued == []


def test_private_voice_is_marked_read_on_receipt_once():
    manager = DummyTelegramMessages()

    manager._handle_telegram_message({"data": {
        "message_id": 102,
        "chat_id": 12345,
        "chat": {"type": "private"},
        "media": {"type": "voice", "download_url": "https://example.test/voice.ogg"},
    }})

    assert manager.marked_read == [102]
    assert manager.enqueued == [("https://example.test/voice.ogg", None, "voice")]


def test_private_document_is_marked_read_even_when_media_path_returns_early():
    manager = DummyTelegramMessages()
    media = {
        "type": "document",
        "download_url": "https://example.test/file.pdf",
        "file_name": "file.pdf",
    }

    manager._handle_telegram_message({"data": {
        "message_id": 103,
        "chat_id": 12345,
        "chat": {"type": "private"},
        "media": media,
    }})

    assert manager.marked_read == [103]
    assert manager.files == [(media, None)]


def test_private_text_attaches_recent_silent_media_context():
    manager = DummyTelegramMessages()
    manager._pending_telegram_media_context = {
        "text": "[Telegram image saved to /tmp/example.jpg]",
        "image_path": "/tmp/example.jpg",
        "created_at": 1_000_000.0,
    }

    import distr.core.integrations.telegram.messages as telegram_messages
    original_time = telegram_messages.time.time
    telegram_messages.time.time = lambda: 1_000_030.0
    try:
        manager._handle_telegram_message({"data": {
            "message_id": 104,
            "chat_id": 12345,
            "chat": {"type": "private"},
            "text": "what is this?",
        }})
    finally:
        telegram_messages.time.time = original_time

    assert manager.marked_read == [104]
    assert manager.enqueued == [(
        "[Telegram image saved to /tmp/example.jpg]\nwhat is this?",
        "/tmp/example.jpg",
        "text",
    )]
    assert manager._pending_telegram_media_context is None
