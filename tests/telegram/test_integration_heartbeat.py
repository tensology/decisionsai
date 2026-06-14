import sys

from distr.core.integrations.base import IntegrationReconnectMixin


class _FakeSignal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback

    def emit(self):
        if self.callback:
            self.callback()


class _FakeTimer:
    instances = []

    def __init__(self):
        self.timeout = _FakeSignal()
        self.single_shot = False
        self.active = False
        self.started_with = []
        self.stop_count = 0
        _FakeTimer.instances.append(self)

    def setSingleShot(self, value):
        self.single_shot = bool(value)

    def start(self, interval):
        self.active = True
        self.started_with.append(interval)

    def stop(self):
        self.active = False
        self.stop_count += 1

    def isActive(self):
        return self.active


class _FakeSocket:
    def __init__(self):
        self.closed = False
        self.aborted = False

    def close(self):
        self.closed = True

    def abort(self):
        self.aborted = True


class _HeartbeatHarness(IntegrationReconnectMixin):
    def __init__(self):
        self.connected = True
        self.sent = []
        self.socket = _FakeSocket()
        self._init_reconnect_state(initial_delay_ms=100, max_delay_ms=1_000)
        self._reconnect_timer = _FakeTimer()

    def is_connected(self):
        return self.connected

    def _send_websocket_message(self, payload):
        self.sent.append(payload)
        return True


def _install_fake_qtimer(monkeypatch):
    _FakeTimer.instances = []
    qtcore = sys.modules["PyQt6.QtCore"]
    monkeypatch.setattr(qtcore, "QTimer", _FakeTimer)


def test_socket_heartbeat_ping_arms_timeout_and_inbound_frame_clears_it(monkeypatch):
    _install_fake_qtimer(monkeypatch)
    harness = _HeartbeatHarness()

    harness._init_socket_heartbeat_state(
        "Harness",
        interval_ms=321,
        timeout_ms=45,
    )
    harness._start_socket_heartbeat()
    harness._socket_heartbeat_tick()

    assert harness.sent == [{"type": "ping"}]
    assert harness._socket_heartbeat_waiting_for_pong is True
    assert harness._socket_heartbeat_timer.started_with == [321]
    assert harness._socket_heartbeat_timeout_timer.started_with == [45]

    harness._mark_socket_heartbeat_seen()

    assert harness._socket_heartbeat_waiting_for_pong is False
    assert harness._socket_heartbeat_timeout_timer.active is False


def test_socket_heartbeat_timeout_aborts_stale_socket_and_schedules_reconnect(monkeypatch):
    _install_fake_qtimer(monkeypatch)
    harness = _HeartbeatHarness()
    harness._init_socket_heartbeat_state("Harness", interval_ms=321, timeout_ms=45)

    harness._socket_heartbeat_tick()
    harness._socket_heartbeat_timeout()

    assert harness.socket.aborted is True
    assert harness.socket.closed is False
    assert harness._reconnect_timer.started_with == [200]
    assert harness._socket_heartbeat_waiting_for_pong is False


def test_socket_heartbeat_tick_schedules_reconnect_when_socket_is_not_connected(monkeypatch):
    _install_fake_qtimer(monkeypatch)
    harness = _HeartbeatHarness()
    harness.connected = False
    harness._init_socket_heartbeat_state("Harness", interval_ms=321, timeout_ms=45)

    harness._socket_heartbeat_tick()

    assert harness.sent == []
    assert harness._reconnect_timer.started_with == [200]
