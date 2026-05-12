from pathlib import Path

from distr.app.events import EventHandlerMixin
from distr.core.integrations.bus import IntegrationMessageBus
from distr.core.integrations.telegram.messages import TelegramMessagesMixin


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


def test_file_send_event_queues_when_socket_disconnected(tmp_path):
    manager = DummyTelegramManager(connected=False)
    app = DummyApp(manager)
    target = tmp_path / "report.pdf"
    target.write_bytes(b"pdf")

    app._evt_send_file_to_telegram({
        "file_path": str(target),
        "file_name": "report.pdf",
        "file_type": "document",
    })

    assert manager.sent
    args, kwargs = manager.sent[0]
    assert kwargs["document_path"] == str(target)
    assert "report.pdf" in kwargs["text"]


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

    assert seen == [("hello", True, None, {"speak": None, "input_type": "voice"})]


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
        self._pending_telegram_media_context = None
        self._telegram_batch_buffer = []
        self.message_received = type("Signal", (), {"emit": lambda _self, data: None})()

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
