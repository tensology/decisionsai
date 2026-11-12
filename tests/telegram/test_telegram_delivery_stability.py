from pathlib import Path
import inspect
import logging
import queue
import time
from types import SimpleNamespace

import pytest

from distr.app.events import EventHandlerMixin
from distr.core.integrations.bus import IntegrationMessageBus
from distr.core.integrations.telegram.messages import (
    TelegramMessagesMixin,
    _canonical_batch_source_message_id,
)
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
        self._outbound_retry_message = None
        self.connected = True
        self.websocket_attempts = []

    def _stop_typing_loop(self):
        pass

    def is_connected(self):
        return self.connected

    def _send_websocket_message(self, message):
        self.websocket_attempts.append(message)
        return self.connected


def test_reviewed_private_reply_cannot_queue_for_a_replaced_owner():
    sender = DummyTelegramSender()
    assert sender.send_to_telegram('Reviewed result', expected_chat_id='11111') is False
    assert sender._message_queue.empty()
    assert sender.send_to_telegram('Reviewed result', expected_chat_id='12345') is True
    sender.telegram_user_id = 11111
    sender._process_message_queue()
    assert sender.websocket_attempts == []


def test_telegram_delivery_worker_has_no_artificial_post_send_delay():
    source = inspect.getsource(EventHandlerMixin._send_to_telegram_worker)

    assert "time.sleep(2)" not in source


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


def test_whatsapp_origin_response_does_not_reach_telegram_worker(monkeypatch):
    app = DummyApp(DummyTelegramManager(connected=True))
    monkeypatch.setattr("distr.app.events.threading.Thread", ImmediateThread)

    app._evt_send_to_telegram({
        "text": "Model request failed while trying to generate a response.",
        "provider": "fishaudio",
        "is_done": False,
        "origin_surface": "whatsapp",
    })

    assert app.worker_calls == []


def test_whatsapp_origin_response_does_not_use_pending_remote_context(monkeypatch):
    app = DummyApp(DummyTelegramManager(connected=True))
    remote_calls = []
    monkeypatch.setattr("distr.app.events.threading.Thread", ImmediateThread)
    monkeypatch.setattr(
        "distr.core.integrations.telegram.remote_tts_delivery.resolve_remote_delivery_context",
        lambda manager, data, consume_pending=False: {
            "request_id": "remote-test",
            "source_command": "notification",
            "mode": "proactive",
        },
    )
    monkeypatch.setattr(
        app,
        "_send_to_remote_worker",
        lambda data, remote_ctx: remote_calls.append((data, remote_ctx)),
    )

    app._evt_send_to_telegram({
        "text": "Unrelated WhatsApp response.",
        "provider": "fishaudio",
        "origin_surface": "whatsapp",
    })

    assert remote_calls == []
    assert app.worker_calls == []


def test_explicit_whatsapp_notification_can_force_telegram_delivery(monkeypatch):
    app = DummyApp(DummyTelegramManager(connected=True))
    monkeypatch.setattr("distr.app.events.threading.Thread", ImmediateThread)

    event = {
        "text": "Project-linked WhatsApp intake received.",
        "provider": "",
        "origin_surface": "whatsapp",
        "explicit_notification_intent": True,
        "force_telegram_delivery": True,
    }
    app._evt_send_to_telegram(event)

    assert app.worker_calls == [event]


def test_explicit_workflow_progress_reaches_telegram_without_voice_provider(monkeypatch):
    app = DummyApp(DummyTelegramManager(connected=True))
    monkeypatch.setattr("distr.app.events.threading.Thread", ImmediateThread)

    event = {
        "text": "Pizza House: Step 2 has started.",
        "provider": "",
        "explicit_notification_intent": True,
        "engagement_kind": "workflow_progress",
        "run_id": 96,
        "step_id": 1926,
    }
    app._evt_send_to_telegram(event)

    assert app.worker_calls == [event]


def test_automation_send_to_telegram_bypasses_remote_delivery(monkeypatch):
    app = DummyApp(DummyTelegramManager(connected=True))
    remote_calls = []
    monkeypatch.setattr("distr.app.events.threading.Thread", ImmediateThread)
    monkeypatch.setattr(
        "distr.core.integrations.telegram.remote_tts_delivery.resolve_remote_delivery_context",
        lambda manager, data, consume_pending=False: {
            "request_id": "remote-test",
            "source_command": "notification",
            "mode": "proactive",
        },
    )
    monkeypatch.setattr(
        app,
        "_send_to_remote_worker",
        lambda data, remote_ctx: remote_calls.append((data, remote_ctx)),
    )

    app._evt_send_to_telegram({
        "text": "My Board: finish taxes today.",
        "provider": "tool",
        "is_done": True,
        "input_type": "automation",
        "force_telegram_delivery": True,
    })

    assert remote_calls == []
    assert app.worker_calls and app.worker_calls[0]["force_telegram_delivery"] is True


def test_telegram_voice_reply_stays_in_chat_when_remote_open(monkeypatch):
    app = DummyApp(DummyTelegramManager(connected=True))
    remote_calls = []
    monkeypatch.setattr("distr.app.events.threading.Thread", ImmediateThread)
    monkeypatch.setattr(
        "distr.core.integrations.telegram.remote_tts_delivery.resolve_remote_delivery_context",
        lambda manager, data, consume_pending=False: {
            "request_id": "remote-test",
            "source_command": "notification",
            "mode": "proactive",
        },
    )
    monkeypatch.setattr(
        app,
        "_send_to_remote_worker",
        lambda data, remote_ctx: remote_calls.append((data, remote_ctx)),
    )

    app._evt_send_to_telegram({
        "text": "Yes, I can help with that.",
        "provider": "kokoro",
        "is_done": False,
        "input_type": "voice",
    })

    assert remote_calls == []
    assert app.worker_calls and app.worker_calls[0]["input_type"] == "voice"


def test_remote_voice_reply_uses_remote_instead_of_telegram(monkeypatch):
    app = DummyApp(DummyTelegramManager(connected=True))
    remote_calls = []
    monkeypatch.setattr("distr.app.events.threading.Thread", ImmediateThread)
    monkeypatch.setattr(
        "distr.core.integrations.telegram.remote_tts_delivery.resolve_remote_delivery_context",
        lambda manager, data, consume_pending=False: {
            "request_id": "remote-voice-1",
            "source_command": "voice_transcribe",
            "mode": "command",
        },
    )
    monkeypatch.setattr(
        app,
        "_send_to_remote_worker",
        lambda data, remote_ctx: remote_calls.append((data, remote_ctx)),
    )

    app._evt_send_to_telegram({
        "text": "Remote voice answer.",
        "provider": "kokoro",
        "input_type": "voice",
        "origin_surface": "remote",
        "origin_request_id": "remote-voice-1",
    })

    assert len(remote_calls) == 1
    assert remote_calls[0][1]["request_id"] == "remote-voice-1"
    assert app.worker_calls == []


def test_remote_voice_reply_still_uses_remote_when_no_telegram_input_type(monkeypatch):
    app = DummyApp(DummyTelegramManager(connected=True))
    remote_calls = []
    monkeypatch.setattr("distr.app.events.threading.Thread", ImmediateThread)
    monkeypatch.setattr(
        "distr.core.integrations.telegram.remote_tts_delivery.resolve_remote_delivery_context",
        lambda manager, data, consume_pending=False: {
            "request_id": "voice_14_test",
            "source_command": "voice_transcribe",
            "mode": "command",
        },
    )
    monkeypatch.setattr(
        app,
        "_send_to_remote_worker",
        lambda data, remote_ctx: remote_calls.append((data, remote_ctx)),
    )

    app._evt_send_to_telegram({
        "text": "Here is what I see on screen.",
        "provider": "kokoro",
        "is_done": False,
        "input_type": None,
    })

    assert len(remote_calls) == 1
    assert remote_calls[0][1]["request_id"] == "voice_14_test"
    assert app.worker_calls == []


def test_automation_delivery_payload_requests_telegram(monkeypatch):
    queued = []
    monkeypatch.setattr(
        "distr.core.automation_subagent._telegram_connected",
        lambda: True,
    )
    monkeypatch.setattr(
        "distr.core.automation_subagent._put_main_event",
        lambda event, data: queued.append((event, data)) or True,
    )
    from distr.core.automation_subagent import _deliver_automation_speech

    channel, detail = _deliver_automation_speech(
        "Finish Player1Sport today.",
        automation_name="Daily plan",
        manual=True,
        origin_surface="telegram",
    )

    assert channel == "telegram"
    assert "Telegram" in detail
    assert queued[0][0] == "send_to_telegram"
    assert queued[0][1]["force_telegram_delivery"] is True
    assert queued[0][1]["input_type"] == "automation"

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


def test_interactive_prompt_is_visible_text_with_buttons_not_voice(monkeypatch):
    import distr.app.events as events
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.notification_routing import reset_notification_activity

    reset_engagement_ledger()
    reset_notification_activity()
    manager = DummyTelegramManager(connected=True)
    app = WorkerApp(manager)
    markup = {
        "inline_keyboard": [[
            {"text": "Approve", "callback_data": "wf:opaque:approve"},
            {"text": "Stop", "callback_data": "wf:opaque:stop"},
        ]]
    }
    payload = {
        "text": "The control check is ready. Tap Approve to begin.",
        "provider": "kokoro",
        "is_done": False,
        "input_type": "voice",
        "explicit_notification_intent": True,
        "requires_response": True,
        "engagement_kind": "workflow_progress",
        "state_fingerprint": "interactive-prompt-test",
        "reply_markup": markup,
        "allow_voice": True,
    }
    monkeypatch.setattr(events, "load_settings_from_db", lambda: {})
    monkeypatch.setattr(
        events,
        "load_response_format_settings",
        lambda _settings: (False, True),
    )

    app._send_to_telegram_worker(payload)

    assert app.generated_tts == []
    assert len(manager.sent) == 1
    _, kwargs = manager.sent[0]
    assert kwargs["text"] == payload["text"]
    assert kwargs["audio_file_path"] is None
    assert kwargs["reply_markup"] == markup


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


def test_sender_preserves_inline_workflow_keyboard():
    sender = DummyTelegramSender()
    markup = {"inline_keyboard": [[{"text": "Approve", "callback_data": "wf:opaque:approve"}]]}

    assert sender.send_to_telegram("Needs approval", reply_markup=markup) is True

    payload = sender._message_queue.get_nowait()
    assert payload["reply_markup"] == markup


def test_sender_correlates_durable_workflow_keyboard_delivery():
    sender = DummyTelegramSender()

    assert sender.send_to_telegram(
        "Needs approval",
        reply_markup={"inline_keyboard": []},
        interaction_token="opaque-token",
    ) is True

    payload = sender._message_queue.get_nowait()
    assert payload["client_message_id"] == "opaque-token"


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


@pytest.mark.parametrize(("transcript", "literal"), [
    ("yes, approve it", False),
    ("Do not stop the receipt check. Keep option 2 in the report.", True),
])
def test_voice_transcription_resolves_single_workflow_interaction(monkeypatch, transcript, literal):
    import json
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from distr.core.db import engine
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, AutoWorkflowRun
    from distr.core.workflow.interactions import create_workflow_interaction, pending_interactions

    pending_interactions()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM workflow_interactions"))
    # A checkpoint must belong to a real current waiting run, rather than
    # relying on historical orphan IDs that cannot be safely resumed.
    with Session(engine) as db:
        db.merge(AutoWorkflow(id=10, name='Scoped voice approval'))
        db.merge(AutoWorkflowStep(id=30, workflow_id=10, name='Approve', status='waiting'))
        db.merge(AutoWorkflowRun(id=20, workflow_id=10, current_step_id=30, status='waiting',
                                 run_data=json.dumps({'waiting_kind': 'approval'})))
        db.commit()
    create_workflow_interaction(
        workflow_id=10, run_id=20, step_id=30, kind="approval", telegram_chat_id=123
    )
    resumed = []
    # The voice boundary deliberately applies checkpoints in the background.
    # Make only this test's worker deterministic before asserting its outcome.
    class ImmediateWorkflowThread:
        def __init__(self, target, kwargs=None, **_options):
            self.target, self.kwargs = target, kwargs or {}
        def start(self):
            self.target(**self.kwargs)
    monkeypatch.setattr('distr.core.workflow.interactions.threading.Thread', ImmediateWorkflowThread)
    def resume_fixture(run_id, feedback, *, expected_checkpoint, literal_feedback=False):
        assert expected_checkpoint['owner']['id'] == run_id
        assert expected_checkpoint['checkpoint']['step_id'] == 30
        resumed.append((run_id, feedback, literal_feedback))
        return {'success': True}
    monkeypatch.setattr('distr.core.workflow.dispatcher.continue_waiting_step', resume_fixture)
    manager = DummyTelegramManager(connected=True)
    manager.telegram_user_id = 123
    app = DummyApp(manager)

    app._evt_telegram_transcription({
        "request_id": "voice-workflow-1",
        "success": True,
        "transcript": transcript,
        "input_type": "voice",
    })

    assert resumed == [(20, transcript, literal)]
    assert any(
        "queued your decision for run #20" in (kwargs.get("text") or (args[0] if args else ""))
        for args, kwargs in manager.sent
    )
    assert pending_interactions(chat_id=123) == []

def test_telegram_bus_preserves_input_type_metadata(tmp_path):
    seen = []
    bus = IntegrationMessageBus(mapping_path=Path(tmp_path) / "bus.json")
    bus.set_text_sink(lambda text, is_telegram, image_path, speak: seen.append(
        (text, is_telegram, image_path, speak)
    ))

    bus.deliver_telegram_user_input(text="hello", input_type="voice")

    assert seen == [("hello", True, None, {"speak": None, "input_type": "voice", "surface": "telegram"})]


def test_sender_rate_limit_defers_message_instead_of_dropping(monkeypatch):
    sender = DummyTelegramSender()
    sender._min_send_interval = 10
    sender._last_send_time = time.time()

    result = sender.send_to_telegram("A real response that arrived during a burst.")

    assert result is True
    assert sender._message_queue.qsize() == 1
    queued = sender._message_queue.get_nowait()
    assert queued["text"] == "A real response that arrived during a burst."
    assert queued.get("_not_before", 0) >= sender._last_send_time + sender._min_send_interval


def test_sender_retains_message_when_socket_drops_during_queue_processing():
    sender = DummyTelegramSender()
    sender.send_to_telegram("retain this response")
    sender.connected = True
    sender._send_websocket_message = lambda _message: False

    sender._process_message_queue()

    assert sender._outbound_retry_message is not None
    assert sender._outbound_retry_message["text"] == "retain this response"

    delivered = []
    sender._send_websocket_message = lambda message: delivered.append(message) or True
    sender._process_message_queue()

    assert [message["text"] for message in delivered] == ["retain this response"]
    assert sender._outbound_retry_message is None


def test_sender_rejects_full_outbound_queue_without_blocking_or_poisoning_dedup():
    sender = DummyTelegramSender()
    sender._message_queue = queue.Queue(maxsize=1)
    assert sender.send_to_telegram("first") is True

    assert sender.send_to_telegram("second") is False

    sender._message_queue.get_nowait()
    assert sender.send_to_telegram("second") is True


class DummyTelegramMessages(TelegramMessagesMixin):
    def __init__(self):
        self._processed_message_ids = set()
        self._processed_message_hashes = set()
        self._max_processed_cache_size = 100
        self.chat_id = None
        self.telegram_user_id = None
        self.marked_read = []
        self.enqueued = []
        self.source_message_ids = []
        self.files = []
        self.sent = []
        self._pending_telegram_files = []
        self._telegram_file_downloads = set()
        self._telegram_file_wait_deadline = None
        self._telegram_batch_buffer = []
        self._telegram_batch_thread_id = None
        self._telegram_batch_timer = SimpleNamespace(start=lambda _delay: None)
        self._TELEGRAM_BATCH_DELAY_MS = 3000
        self.message_received = type("Signal", (), {"emit": lambda _self, data: None})()

    def _get_chat_id(self):
        return self.chat_id

    def send_to_telegram(self, text):
        self.sent.append(text)
        return True

    def _stop_typing_loop(self):
        pass

    def _start_typing_loop(self, _action):
        pass

    def _update_stored_connection_data(self, **kwargs):
        pass

    def _track_telegram_group(self, *args, **kwargs):
        pass

    def _mark_message_as_read(self, message_id, *, chat_id=None):
        self.marked_read.append(message_id)

    def _enqueue_telegram_batch(
        self, text, image_path=None, input_type="text", source_message_id=None, downloaded_file=None
    ):
        super()._enqueue_telegram_batch(text, image_path, input_type, source_message_id, downloaded_file)
        self.enqueued.append((text, image_path, input_type))
        self.source_message_ids.append(source_message_id)

    def _handle_voice_message(self, url, media_type, message_id=None):
        self.enqueued.append((url, None, media_type))
        self.source_message_ids.append(message_id)

    def _handle_file_message(self, media, caption=None, message_id=None):
        self.files.append((media, caption))
        self.source_message_ids.append(message_id)


def test_relay_callback_is_recorded_as_telegram_callback(monkeypatch):
    manager = DummyTelegramMessages()
    seen = {}

    def handle(value, **kwargs):
        seen.update(value=value, **kwargs)
        return {"success": True, "run_id": 92, "action": "approve"}

    monkeypatch.setattr(
        "distr.core.workflow.interactions.handle_telegram_workflow_reply",
        handle,
    )
    monkeypatch.setattr(
        "distr.core.workflow.interactions.workflow_reply_message",
        lambda result: "accepted",
    )

    manager._handle_telegram_message({"data": {
        "message_id": "callback-1",
        "chat_id": 12345,
        "chat": {"type": "private"},
        "from": {"id": 12345},
        "callback_data": "wf:opaque:approve",
    }})

    assert seen["value"] == "wf:opaque:approve"
    assert seen["source"] == "telegram_callback"
    assert manager.sent == ["accepted"]


def test_remote_command_bypasses_pending_workflow_and_intake_handlers(monkeypatch):
    manager = DummyTelegramMessages()
    consumed = []
    def must_not_consume(*args, **kwargs):
        consumed.append(args)
        raise AssertionError('Remote is a global control command, not a workflow answer')
    monkeypatch.setattr('distr.core.workflow.interactions.handle_telegram_workflow_reply', must_not_consume)
    monkeypatch.setattr('distr.core.kanban.whatsapp_intake_approval.handle_telegram_reply', must_not_consume)
    for index, command in enumerate(('remote', ' REMOTE ', 'please remote link')):
        manager._handle_telegram_message({'data': {
            'message_id': f'remote-{index}', 'chat_id': 12345,
            'chat': {'type': 'private'}, 'from': {'id': 12345}, 'text': command,
        }})
    assert len(manager.sent) == 3
    assert all('https://decisions.tensology.com/api/remote/?channel=' in text for text in manager.sent)
    assert manager.enqueued == []
    assert consumed == []


def test_duplicate_callback_does_not_emit_false_failure(monkeypatch):
    manager = DummyTelegramMessages()
    monkeypatch.setattr(
        "distr.core.workflow.interactions.handle_telegram_workflow_reply",
        lambda *_args, **_kwargs: {
            "success": True,
            "run_id": 92,
            "action": "approve",
            "idempotent": True,
        },
    )

    manager._handle_telegram_message({"data": {
        "message_id": "callback-duplicate",
        "chat_id": 12345,
        "chat": {"type": "private"},
        "from": {"id": 12345},
        "callback_data": "wf:opaque:approve",
    }})

    assert manager.sent == []
    assert manager.enqueued == []


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
    assert manager.source_message_ids == [101]


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
    assert "https://decisions.tensology.com/api/remote/?channel=" in manager.sent[0]
    assert manager.enqueued == []


def test_remote_word_inside_normal_sentence_is_not_hijacked():
    manager = DummyTelegramMessages()
    manager.telegram_user_id = 12345

    manager._handle_telegram_message({"data": {
        "message_id": 106,
        "chat_id": 12345,
        "chat": {"type": "private"},
        "text": "can we talk about remote work tomorrow",
    }})

    assert manager.sent == []
    assert manager.enqueued == [("can we talk about remote work tomorrow", None, "text")]
    assert manager.source_message_ids == [106]


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
    assert manager.source_message_ids == [102]


def test_relay_transcribed_voice_does_not_start_duplicate_local_transcription():
    manager = DummyTelegramMessages()

    manager._handle_telegram_message({"data": {
        "message_id": 107,
        "chat_id": 12345,
        "chat": {"type": "private"},
        "text": "Continue .",
        "media": {"type": "voice", "download_url": "https://example.test/voice.ogg"},
    }})

    assert manager.marked_read == [107]
    assert manager.enqueued == [("Continue .", None, "voice")]
    assert manager.source_message_ids == [107]


@pytest.mark.parametrize("relay_text", [None, "Create a ticket: fix checkout"])
def test_private_document_routes_once_even_when_relay_puts_caption_in_text(relay_text):
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
        "text": relay_text,
    }})

    assert manager.marked_read == [103]
    assert manager.files == [(media, relay_text)]
    assert manager.source_message_ids == [103]
    assert not manager.enqueued


def test_group_photo_is_stored_without_entering_private_intake(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock

    manager = DummyTelegramMessages()
    manager.telegram_user_id = 12345
    manager._group_message_queue = queue.Queue()
    manager._group_messages_storage = []
    manager._max_stored_group_messages = 10
    database = Mock()
    database.query.return_value.filter_by.return_value.first.return_value = None
    monkeypatch.setattr("distr.core.db.get_session", lambda: nullcontext(database))
    media = {"type": "photo", "download_url": "https://example.invalid/photo"}

    manager._handle_telegram_message({"data": {
        "message_id": 103, "chat_id": -12345, "chat": {"type": "group"},
        "media": media, "text": "Photo from the project group",
    }})

    assert manager._group_message_queue.get_nowait()["media"] == media
    database.add.assert_called_once()
    assert not manager.files
    assert not manager.enqueued
    assert manager.telegram_user_id == 12345


def test_private_text_attaches_recent_silent_media_context(monkeypatch):
    from distr.core.integrations.telegram.messages import _DownloadedTelegramFile

    manager = DummyTelegramMessages()
    manager.telegram_user_id = 12345
    monkeypatch.setattr(time, "monotonic", lambda: 1_000_030.0)
    manager._pending_telegram_files = [
        _DownloadedTelegramFile("12345", "101", "/tmp/example.jpg", "image", "example.jpg", "image/jpeg", "", 1_000_000.0),
        _DownloadedTelegramFile("12345", "102", "/tmp/expired.jpg", "image", "expired.jpg", "image/jpeg", "", 999_900.0),
    ]

    manager._handle_telegram_message({"data": {
        "message_id": 104,
        "chat_id": 12345,
        "chat": {"type": "private"},
        "text": "what is this?",
    }})

    assert manager.marked_read == [104]
    assert manager.enqueued == [("", "/tmp/example.jpg", "text"), ("what is this?", None, "text")]
    assert not manager._pending_telegram_files
    assert manager.source_message_ids == ["101", 104]


def test_batch_source_message_id_is_stable_for_single_and_multi_message_bursts():
    assert _canonical_batch_source_message_id([101]) == "101"
    first = _canonical_batch_source_message_id([101, 102, 101])
    second = _canonical_batch_source_message_id([101, 102])
    assert first == second
    assert first.startswith("batch:")


def test_flushed_ticket_request_preserves_durable_telegram_source_identity(monkeypatch):
    manager = DummyTelegramMessages()
    manager.telegram_user_id = 12345
    manager._telegram_batch_thread_id = 12345
    manager._telegram_batch_buffer = [
        ("Create a ticket: fix checkout", False, None, "text", 201),
        ("It fails on mobile", True, "/tmp/mobile.jpg", "text", 202),
        ("Here is the error", True, "/tmp/error.jpg", "text", 203),
        ("Same screenshot again", True, "/tmp/mobile.jpg", "text", 204),
    ]
    captured = {}

    class _Service:
        def ingest(self, intake):
            captured["intake"] = intake
            return SimpleNamespace(handled=True, response_text="Ticket created")

    monkeypatch.setattr(
        "distr.core.work_intake.get_work_intake_service", lambda: _Service()
    )

    manager._flush_telegram_batch()

    intake = captured["intake"]
    assert intake.source_message_id == _canonical_batch_source_message_id([201, 202, 203, 204])
    assert intake.metadata["source_message_ids"] == ["201", "202", "203", "204"]
    assert [(attachment.kind, attachment.path) for attachment in intake.attachments] == [
        ("image", "/tmp/mobile.jpg"),
        ("image", "/tmp/error.jpg"),
    ]
    assert intake.source_thread_id == "12345"
    assert manager.sent == ["Ticket created"]


def _qt_telegram_messages():
    from PyQt6.QtCore import QCoreApplication, QObject

    class QtMessages(DummyTelegramMessages, QObject):
        def __init__(self):
            QObject.__init__(self)
            DummyTelegramMessages.__init__(self)

    app = QCoreApplication.instance() or QCoreApplication([])
    manager = QtMessages()
    manager.telegram_user_id = 12345
    return app, manager


def _download_test_file(manager, media, message_id, received_at):
    from distr.core.integrations.telegram.messages import _DownloadedTelegramFile

    file = _DownloadedTelegramFile(
        str(manager.telegram_user_id), str(message_id), "",
        "image" if media["type"] == "photo" else media["type"], media["file_name"], "", "", received_at,
    )
    manager._telegram_file_downloads.add(file)
    return media, file


def test_same_second_downloads_keep_each_original_file(monkeypatch, tmp_path):
    from datetime import datetime
    from distr.core.integrations.telegram import messages

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 11, 12, 30, 0, tzinfo=tz)

    responses = iter([b"first attachment", b"second attachment"])
    monkeypatch.setattr(messages, "datetime", FrozenDatetime)
    monkeypatch.setattr(messages.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        messages.requests, "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200, content=next(responses)),
    )
    app, manager = _qt_telegram_messages()
    for message_id in (601, 602):
        media = {"download_url": "https://example.invalid/file", "type": "photo",
                 "file_name": "checkout.jpg", "mime_type": "image/jpeg"}
        manager._download_and_forward_file(*_download_test_file(manager, media, message_id, time.monotonic()))
    app.processEvents()
    paths = [Path(file.path) for file in manager._pending_telegram_files]

    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert [path.read_bytes() for path in paths] == [b"first attachment", b"second attachment"]


@pytest.mark.parametrize("followup_while_downloading", [False, True])
def test_uncaptioned_photo_and_document_reach_one_followup(monkeypatch, tmp_path, followup_while_downloading):
    from concurrent.futures import ThreadPoolExecutor
    from distr.core.integrations.telegram import messages

    app, manager = _qt_telegram_messages()
    monkeypatch.setattr(messages.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(messages.requests, "get", lambda *args, **kwargs:
                        SimpleNamespace(status_code=200, content=b"test attachment"))
    ingested = []
    monkeypatch.setattr("distr.core.work_intake.get_work_intake_service",
                        lambda: SimpleNamespace(ingest=lambda intake: ingested.append(intake) or
                                                SimpleNamespace(handled=True, response_text="Ticket created")))

    def follow_up():
        app.processEvents()
        assert not manager.enqueued
        manager._handle_telegram_message({"data": {
            "message_id": 603, "chat_id": 12345, "chat": {"type": "private"},
            "text": "Create a ticket: fix checkout",
        }})

    received_at = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as worker:
        for msg_id, kind, name in [(601, "photo", "checkout.jpg"), (602, "document", "steps.pdf")]:
            if msg_id == 602 and followup_while_downloading:
                follow_up()
            media = {"download_url": "https://example.invalid/file", "type": kind, "file_name": name}
            args = _download_test_file(manager, media, msg_id, received_at)
            worker.submit(manager._download_and_forward_file, *args).result(timeout=5)
    if not followup_while_downloading:
        follow_up()
    app.processEvents()
    manager._flush_telegram_batch()

    assert len(ingested) == 1
    intake = ingested[0]
    assert [attachment.kind for attachment in intake.attachments] == ["image", "document"]
    assert [Path(attachment.path).suffix for attachment in intake.attachments] == [".jpg", ".pdf"]
    assert sorted(intake.metadata["source_message_ids"]) == ["601", "602", "603"]
    assert intake.source_message_id == _canonical_batch_source_message_id(intake.metadata["source_message_ids"])
    assert intake.source_thread_id == "12345"
    assert intake.text == "Create a ticket: fix checkout"


def test_local_voice_followup_consumes_pending_attachment(monkeypatch):
    from distr.core.integrations.telegram.messages import _DownloadedTelegramFile

    manager = DummyTelegramMessages()
    manager.telegram_user_id = 12345
    manager._pending_telegram_files = [
        _DownloadedTelegramFile("12345", "601", "/tmp/steps.pdf", "document", "steps.pdf",
                                "application/pdf", "", time.monotonic()),
    ]
    monkeypatch.setattr("distr.core.workflow.interactions.handle_telegram_workflow_reply", lambda *args, **kwargs: None)
    monkeypatch.setattr("distr.core.kanban.whatsapp_intake_approval.handle_telegram_reply", lambda *args, **kwargs: None)
    monkeypatch.setattr("distr.core.kanban.whatsapp_work_lifecycle.handle_telegram_reply", lambda *args, **kwargs: None)
    ingested = []
    monkeypatch.setattr("distr.core.work_intake.get_work_intake_service",
                        lambda: SimpleNamespace(ingest=lambda intake: ingested.append(intake) or
                                                SimpleNamespace(handled=True, response_text="Ticket created")))
    handler = SimpleNamespace(telegram_manager=manager)
    EventHandlerMixin._evt_telegram_transcription(handler, {
        "request_id": "local-voice", "success": True, "transcript": "Create a ticket: fix checkout",
        "source_message_id": "602", "input_type": "voice",
    })

    assert manager.source_message_ids == ["601", "602"]
    assert manager.enqueued[-1] == ("Create a ticket: fix checkout", None, "voice")
    assert not manager._pending_telegram_files
    manager._flush_telegram_batch()
    assert len(ingested) == 1
    assert ingested[0].transcript == "Create a ticket: fix checkout"
    assert [attachment.name for attachment in ingested[0].attachments] == ["steps.pdf"]


@pytest.mark.parametrize("outcome", ["ready", "http_error", "timer_first", "completion_first"])
def test_request_waits_for_download_and_does_not_reassign_late_file(monkeypatch, tmp_path, outcome):
    from distr.core.integrations.telegram import messages

    app, manager = _qt_telegram_messages()
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(messages.Path, "home", lambda: tmp_path)
    status_code = 503 if outcome == "http_error" else 200
    monkeypatch.setattr(messages.requests, "get", lambda *args, **kwargs:
                        SimpleNamespace(status_code=status_code, content=b"test attachment"))
    downloads = []
    monkeypatch.setattr(messages, "threading", SimpleNamespace(Thread=lambda target, args, **kwargs:
                        SimpleNamespace(start=lambda: downloads.append(lambda: target(*args)))))
    ingested = []
    monkeypatch.setattr("distr.core.work_intake.get_work_intake_service",
                        lambda: SimpleNamespace(ingest=lambda intake: ingested.append(intake) or
                                                SimpleNamespace(handled=True, response_text="Ticket created")))
    TelegramMessagesMixin._handle_file_message(
        manager, {"type": "document", "file_name": "steps.pdf", "download_url": "https://example.invalid/file"},
        message_id=601,
    )
    manager._enqueue_telegram_batch("Create a ticket: fix checkout", source_message_id="602")
    clock[0] = 103.0
    manager._flush_telegram_batch()
    assert not ingested

    if outcome in {"timer_first", "completion_first"}:
        clock[0] = 120.0
    if outcome == "timer_first":
        manager._flush_telegram_batch()
        assert len(ingested) == 1
    downloads[0]()
    app.processEvents()
    assert len(ingested) == 1
    assert not manager._pending_telegram_files
    assert not manager._telegram_file_downloads
    assert set(ingested[0].metadata["source_message_ids"]) == {"601", "602"}
    if outcome == "ready":
        assert [attachment.name for attachment in ingested[0].attachments] == ["steps.pdf"]
        assert manager.sent == ["Ticket created"]
    else:
        assert not ingested[0].attachments
        assert "steps.pdf" in manager.sent[0]


def test_failed_batch_handoff_notifies_user_instead_of_logging_false_success(monkeypatch):
    manager = DummyTelegramMessages()
    manager.telegram_user_id = 12345
    manager._telegram_batch_thread_id = 12345
    manager._telegram_batch_buffer = [
        ("Read the latest email", False, None, "text", 301),
    ]

    class _IntakeService:
        def ingest(self, _intake):
            return SimpleNamespace(handled=False, response_text="")

    class _Bus:
        def deliver_telegram_user_input(self, **_kwargs):
            return False

    monkeypatch.setattr(
        "distr.core.work_intake.get_work_intake_service", lambda: _IntakeService()
    )
    monkeypatch.setattr(
        "distr.core.integrations.bus.get_integration_message_bus", lambda: _Bus()
    )

    manager._flush_telegram_batch()

    assert manager.sent == [
        "I received that, but could not hand it to the agent. Please try again."
    ]
    assert manager._pending_work_intake_uid is None
    assert manager._pending_work_intake_thread_id is None
