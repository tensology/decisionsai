"""Pipeline-bound user requests wait for initialization, not just process liveness."""
from types import SimpleNamespace

import pytest

from distr.app.agent_lifecycle import AgentLifecycleMixin


class Queue:
    def __init__(self):
        self.items = []

    def put(self, value, block=False):
        self.items.append(value)


def app():
    instance = AgentLifecycleMixin()
    instance._quitting = False
    instance._agent_lifecycle_state = "starting"
    instance.agent_process = SimpleNamespace(is_alive=lambda: True)
    instance.agent_command_queue = Queue()
    return instance


@pytest.mark.parametrize("command", ["speak_text_directly", "process_text_input"])
def test_pipeline_request_waits_then_delivers_once(monkeypatch, command):
    timers = []
    monkeypatch.setattr("distr.app.agent_lifecycle._run_on_main_thread", lambda fn: fn())
    monkeypatch.setattr("distr.app.agent_lifecycle.QTimer.singleShot", lambda delay, fn: timers.append(fn))
    instance = app()
    params = {"text": "Hello", "is_telegram": command == "process_text_input"}
    instance._send_command_to_agent(command, params)
    assert instance.agent_command_queue.items == []
    assert instance._pending_agent_commands == [(command, params)]
    instance._agent_lifecycle_state = "ready"
    timers.pop(0)()
    assert instance.agent_command_queue.items == []
    timers.pop(0)()
    assert instance.agent_command_queue.items == [(command, params)]
    instance._check_agent_ready_and_send_pending()
    assert instance.agent_command_queue.items == [(command, params)]


def test_multiple_startup_requests_share_one_poll_and_keep_order(monkeypatch):
    timers = []
    monkeypatch.setattr("distr.app.agent_lifecycle._run_on_main_thread", lambda fn: fn())
    monkeypatch.setattr("distr.app.agent_lifecycle.QTimer.singleShot", lambda delay, fn: timers.append(fn))
    instance = app()
    instance._send_command_to_agent("speak_text_directly", {"text": "First"})
    instance._send_command_to_agent("process_text_input", {"text": "Second", "is_telegram": True})
    assert len(timers) == 1
    instance._agent_lifecycle_state = "ready"
    timers.pop(0)()
    for callback in list(timers):
        callback()
    assert [params["text"] for _, params in instance.agent_command_queue.items] == ["First", "Second"]


def test_ready_speech_and_initialization_controls_are_not_delayed():
    instance = app()
    instance._send_command_to_agent("set_hands_free", {"enabled": False})
    instance._agent_lifecycle_state = "ready"
    instance._send_command_to_agent("speak_text_directly", {"text": "Ready"})
    assert [command for command, _ in instance.agent_command_queue.items] == ["set_hands_free", "speak_text_directly"]


def test_start_does_not_announce_ready_before_pipeline_initialization():
    from distr.core.agent.session import AgentSession

    events = []
    async def pipeline():
        assert events == []
        # The pipeline-started event owns readiness, not command-thread startup.
        events.append("pipeline started")

    session = SimpleNamespace(
        running=False, command_queue=None,
        logger=SimpleNamespace(debug=lambda *a: None, error=lambda *a, **k: None),
        _emit_agent_ready=lambda: events.append("premature readiness"),
        _run_pipeline=pipeline, stop=lambda: None,
    )
    AgentSession.start(session)
    assert events == ["pipeline started"]


def test_speech_event_is_retained_before_signal_wiring_or_worker_creation(monkeypatch):
    from distr.app.events import EventHandlerMixin

    timers = []
    monkeypatch.setattr("distr.app.agent_lifecycle._run_on_main_thread", lambda fn: fn())
    monkeypatch.setattr("distr.app.agent_lifecycle.QTimer.singleShot", lambda delay, fn: timers.append(fn))
    instance = app()
    instance.agent_process = None
    EventHandlerMixin._evt_actions(instance, "speak_text_directly", {"text": "Early read"})
    assert instance._pending_agent_commands == [("speak_text_directly", {"text": "Early read"})]
    assert instance.agent_command_queue.items == []
    instance.agent_process = SimpleNamespace(is_alive=lambda: True)
    instance._agent_lifecycle_state = "ready"
    timers.pop(0)()
    timers.pop(0)()
    assert instance.agent_command_queue.items == [("speak_text_directly", {"text": "Early read"})]
