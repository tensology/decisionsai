from types import SimpleNamespace

from distr.app.agent_lifecycle import AgentLifecycleMixin
from distr.app.events import EventHandlerMixin
from distr.core.agent import command_handler


class _Queue:
    def __init__(self):
        self.items = []

    def put(self, item, **kwargs):
        self.items.append(item)


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def test_agent_health_probe_acknowledges_command_worker():
    queue = _Queue()
    session = SimpleNamespace(event_queue=queue, logger=_Logger())

    command_handler._cmd_agent_health_probe(session, {"probe_id": "probe-1"})

    assert queue.items == [
        (
            "agent_health_ack",
            {"probe_id": "probe-1", "timestamp": queue.items[0][1]["timestamp"]},
        )
    ]


def test_agent_health_event_records_acknowledgement():
    app = SimpleNamespace(
        _last_agent_health_ack=None,
        _agent_health_probe_sent_at=123.0,
    )

    EventHandlerMixin._dispatch_agent_event(app, "agent_health_ack", {})

    assert app._last_agent_health_ack is not None
    assert app._agent_health_probe_sent_at is None


def test_live_but_unresponsive_agent_is_reloaded(monkeypatch):
    class _Process:
        exitcode = None

        @staticmethod
        def is_alive():
            return True

    class _App(AgentLifecycleMixin):
        pass

    app = _App()
    app.__dict__.update(
        agent_process=_Process(),
        _quitting=False,
        _agent_health_probe_sent_at=0.0,
        _last_agent_health_ack=None,
        reload_agent_session=lambda **kwargs: setattr(app, "reloaded", kwargs),
        _send_command_to_agent=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "distr.app.agent_lifecycle.time.monotonic",
        lambda: AgentLifecycleMixin._AGENT_HEALTH_PROBE_TIMEOUT_SECONDS + 1,
    )

    AgentLifecycleMixin.check_agent_health(app)

    assert app.reloaded == {"skip_welcome": False}


def test_live_agent_gets_a_probe_before_timeout(monkeypatch):
    class _Process:
        exitcode = None

        @staticmethod
        def is_alive():
            return True

    sent = []
    app = SimpleNamespace(
        agent_process=_Process(),
        _quitting=False,
        _agent_health_probe_sent_at=None,
        _last_agent_health_ack=None,
        _send_command_to_agent=lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    monkeypatch.setattr("distr.app.agent_lifecycle.time.monotonic", lambda: 10.0)

    AgentLifecycleMixin.check_agent_health(app)

    assert sent == [
        (("agent_health_probe", {"probe_id": sent[0][0][1]["probe_id"]}), {"ensure_alive": False})
    ]
    assert app._agent_health_probe_sent_at == 10.0
