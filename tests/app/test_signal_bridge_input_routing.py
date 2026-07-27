from __future__ import annotations

from types import SimpleNamespace
import inspect

from distr.app.signals import SignalBridgeMixin


class _Signal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def disconnect(self):
        self._callbacks.clear()

    def emit(self, *args, **kwargs):
        for callback in list(self._callbacks):
            callback(*args, **kwargs)


def _fake_signal_manager():
    names = [
        "push_to_talk_start",
        "push_to_talk_stop",
        "dictation_hotkey_pressed",
        "dictation_hotkey_released",
        "ticket_dictation_hotkey_pressed",
        "ticket_dictation_hotkey_released",
        "voice_set_is_listening",
        "hands_free_mode_changed",
        "playback_speed_changed",
        "speech_volume_changed",
        "vad_threshold_changed",
        "elevenlabs_voice_settings_changed",
        "interrupt_tts",
        "send_text_input",
        "set_speaker_enabled",
        "current_chat_changed",
        "model_hot_reload",
        "voice_hot_reload",
        "speak_text_directly",
        "play_action_by_name",
        "play_recording_file",
        "web_send_to_agent_requested",
        "web_create_chat_emits_requested",
        "web_load_chat_in_agent_requested",
        "web_load_chat_and_process_requested",
        "workflow_finished",
        "chat_updated",
        "chat_message_added",
        "chat_stream_started",
        "chat_stream_token",
        "chat_stream_finished",
        "chat_stream_error",
        "transcription_progress",
        "tool_executed",
        "workflow_event",
    ]
    return SimpleNamespace(**{name: _Signal() for name in names})


class _DummyBus:
    def set_text_sink(self, sink):
        self.sink = sink

    def set_chat_id_provider(self, provider):
        self.provider = provider


class _Host(SignalBridgeMixin):
    def __init__(self):
        self.sent_commands = []
        self.current_playback_speed = 1.0
        self.chat_manager = SimpleNamespace(
            get_current_chat=lambda: 99,
            current_chat_changed=_Signal(),
        )
        self._suppress_current_chat_relay = False
        self.player_window = None
        self.action_playback_service = None

    def _send_command_to_agent(self, command, params):
        self.sent_commands.append((command, params))

    def _on_interrupt_tts(self):
        return None

    def _workflow_report_is_automation(self, _summary):
        return False

    def _workflow_report_run_metadata(self, _summary):
        return {}


def test_send_text_input_forwards_chat_id_from_integration_metadata(monkeypatch):
    import distr.app.signals as signals_module

    fake_signal_manager = _fake_signal_manager()
    monkeypatch.setattr(signals_module, "signal_manager", fake_signal_manager)
    monkeypatch.setattr(
        "distr.core.notification_routing.record_surface_activity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "distr.core.integrations.bus.get_integration_message_bus",
        lambda: _DummyBus(),
    )

    host = _Host()
    host._bridge_signals_to_agent()

    fake_signal_manager.send_text_input.emit(
        "hello from telegram",
        True,
        None,
        {"speak": False, "surface": "telegram", "input_type": "voice", "chat_id": 42},
    )

    assert host.sent_commands[-1] == (
        "process_text_input",
        {
            "text": "hello from telegram",
            "is_telegram": True,
            "uploaded_image_path": None,
            "speak": False,
            "telegram_input_type": "voice",
            "chat_id": 42,
        },
    )


def test_web_create_chat_initial_message_skips_second_persist(monkeypatch):
    import distr.app.signals as signals_module

    fake_signal_manager = _fake_signal_manager()
    monkeypatch.setattr(signals_module, "signal_manager", fake_signal_manager)
    monkeypatch.setattr(
        "distr.core.notification_routing.record_surface_activity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "distr.core.integrations.bus.get_integration_message_bus",
        lambda: _DummyBus(),
    )

    host = _Host()
    host._bridge_signals_to_agent()

    fake_signal_manager.web_create_chat_emits_requested.emit(
        707,
        "Are you ready to help me?",
        True,
        "openai",
        "gpt-5.2",
        "pixazo",
        "custom_14",
    )

    assert host.sent_commands[-1] == (
        "current_chat_changed",
        {
            "chat_id": 707,
            "initial_message": "Are you ready to help me?",
            "initial_speak": True,
            "skip_user_persist": True,
        },
    )


def test_web_send_preserves_durable_intake_identity(monkeypatch):
    import distr.app.signals as signals_module

    fake_signal_manager = _fake_signal_manager()
    monkeypatch.setattr(signals_module, "signal_manager", fake_signal_manager)
    monkeypatch.setattr(
        "distr.core.notification_routing.record_surface_activity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "distr.core.integrations.bus.get_integration_message_bus",
        lambda: _DummyBus(),
    )

    captured = []

    class _Decision:
        handled = False

    class _IntakeService:
        def ingest(self, intake):
            captured.append(intake)
            return _Decision()

    monkeypatch.setattr(
        "distr.core.work_intake.get_work_intake_service",
        lambda: _IntakeService(),
    )

    host = _Host()
    host._bridge_signals_to_agent()

    fake_signal_manager.web_send_to_agent_requested.emit(
        808,
        "Research this artist and summarize the evidence.",
        False,
        "openai",
        "gpt-5.2",
        {
            "work_intake": {
                "source_message_id": "qualification:research:808",
                "requested_outcome": "A cited research brief",
                "metadata": {"qualification_campaign_id": "campaign-808"},
            }
        },
    )

    assert len(captured) == 1
    assert captured[0].source == "web"
    assert captured[0].source_thread_id == "808"
    assert captured[0].source_message_id == "qualification:research:808"
    assert captured[0].requested_outcome == "A cited research brief"
    assert captured[0].metadata == {
        "chat_id": 808,
        "qualification_campaign_id": "campaign-808",
    }
    assert host.sent_commands[-1] == (
        "process_text_input",
        {
            "text": "Research this artist and summarize the evidence.",
            "speak": False,
            "chat_id": 808,
            "work_intake_uid": captured[0].intake_uid,
        },
    )


def test_web_chat_events_are_queued_for_ordered_delivery():
    src = inspect.getsource(SignalBridgeMixin._bridge_signals_to_agent)

    assert "self._web_chat_event_queue = Queue(maxsize=1000)" in src
    assert "name=\"web-chat-event-bridge\"" in src
    assert "self._web_chat_event_queue.put_nowait(item)" in src
    assert "threading.Thread(target=_do_post" not in src
