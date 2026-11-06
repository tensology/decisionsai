"""Agent restart-loop fix: startup grace, ready event, capped restarts with backoff, RAM tiers."""

import os
from types import SimpleNamespace

import pytest

from distr.app.agent_lifecycle import AgentLifecycleMixin
from distr.app.events import EventHandlerMixin
from distr.core import system_resources as sr

TICK = 30.0  # main.py health_check_timer interval


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _Proc:
    def __init__(self, alive=True, exitcode=None):
        self.alive = alive
        self.exitcode = exitcode

    def is_alive(self):
        return self.alive


class _App(AgentLifecycleMixin):
    """Fake app: injected clock, synchronous-but-recorded backoff scheduler."""

    def __init__(self, clock, ready_after=None, dies=False, answers_probes=True):
        self.clock = clock
        self.ready_after = ready_after  # seconds from start until 'agent_ready'; None = never
        self.dies = dies
        self.answers_probes = answers_probes
        self._quitting = False
        self.reloads = []
        self.backoffs = []
        self.pending = []  # (due_time, callback)
        self.errors = []
        self._start()

    def _start(self):
        self.agent_process = _Proc(alive=not self.dies, exitcode=-9 if self.dies else None)
        self._agent_lifecycle_state = "starting"
        self._agent_started_at = self.clock()
        self._agent_health_probe_sent_at = None
        self._last_agent_health_ack = None
        # The production start path reports its outcome to charge the automatic
        # retry budget. The earlier double omitted this callback entirely.
        self._on_agent_process_start_result(True)

    def reload_agent_session(self, skip_welcome=False, new_chat_id=None):
        manual = not getattr(self, "_agent_auto_restart_active", False)
        if manual:
            self._reset_agent_restart_budget()
        self.reloads.append((self.clock(), "manual" if manual else "auto"))
        self._start()

    def _schedule_agent_restart(self, delay, callback):
        self.backoffs.append(delay)
        self.pending.append((self.clock() + delay, callback))

    def _send_command_to_agent(self, command, params, ensure_alive=True):
        if command == "agent_health_probe" and self.answers_probes and self._agent_lifecycle_state == "ready":
            EventHandlerMixin._dispatch_agent_event(self, "agent_health_ack", {})

    def advance(self, seconds, step=1.0):
        end = self.clock.now + seconds
        next_tick = (int(self.clock.now // TICK) + 1) * TICK
        while self.clock.now < end:
            self.clock.now = min(end, self.clock.now + step)
            for due, cb in list(self.pending):
                if due <= self.clock.now:
                    self.pending.remove((due, cb))
                    cb()
            if (self.ready_after is not None and self._agent_lifecycle_state == "starting"
                    and not self.dies and self.clock.now - self._agent_started_at >= self.ready_after):
                EventHandlerMixin._dispatch_agent_event(self, "agent_ready", {"pid": 1})
            if self.clock.now >= next_tick:
                self.check_agent_health()
                next_tick += TICK


@pytest.fixture
def clock(monkeypatch, tmp_path):
    c = _Clock()
    monkeypatch.setattr("distr.app.agent_lifecycle.time.monotonic", c)
    monkeypatch.setattr("distr.app.events.time.monotonic", c)
    monkeypatch.setenv("DECISIONS_AGENT_RESTART_LOG", str(tmp_path / "agent_death.log"))
    errors = []
    monkeypatch.setattr("distr.app.agent_lifecycle.signal_manager",
                        SimpleNamespace(chat_stream_error=SimpleNamespace(emit=errors.append)))
    c.errors = errors
    c.log = tmp_path / "agent_death.log"
    return c


@pytest.mark.parametrize("startup_seconds", [90, 120, 250])
def test_slow_starting_agent_is_not_restarted(clock, startup_seconds):
    app = _App(clock, ready_after=startup_seconds)
    app.advance(600)
    assert app.reloads == []
    assert app._agent_lifecycle_state == "ready"


def test_never_ready_agent_is_capped_with_backoff_then_error(clock):
    app = _App(clock, ready_after=None)
    app.advance(30 * 60)  # before the optional 30-minute slow retry after the cap
    assert [kind for _, kind in app.reloads] == ["auto"] * 3
    assert app.backoffs == [0.0, 20.0, 60.0, 1800.0]
    assert app._agent_restart_halted is True
    assert len(clock.errors) == 1 and "automatic restarts were stopped" in clock.errors[0]
    lines = clock.log.read_text().splitlines()
    assert sum(" reason=startup_timeout attempt=" in l for l in lines) == 3
    assert sum("reason=cap_reached" in l for l in lines) == 1


def test_dead_agent_loop_is_capped(clock):
    app = _App(clock, dies=True)
    app.advance(600)
    assert len(app.reloads) == 3
    assert app._agent_restart_halted is True
    assert len(clock.errors) == 1
    text = clock.log.read_text()
    assert "reason=process_dead exitcode=-9 attempt=1/3 backoff_s=0" in text
    assert "attempt=3/3 backoff_s=60" in text
    assert "reason=cap_reached last_reason=process_dead exitcode=-9" in text


def test_ready_agent_that_stops_answering_is_still_restarted(clock):
    app = _App(clock, ready_after=5)
    app.advance(120)
    assert app.reloads == []
    app.answers_probes = False
    app.advance(120)
    assert len(app.reloads) == 1
    assert "reason=probe_timeout attempt=1/3 backoff_s=0" in clock.log.read_text()


def test_manual_retry_resets_counter(clock):
    app = _App(clock, dies=True)
    app.advance(600)
    assert app._agent_restart_halted is True
    app.reload_agent_session()  # user retry
    assert app._agent_restart_halted is False
    assert app._agent_auto_restart_times == []
    app.advance(600)
    assert [k for _, k in app.reloads].count("auto") == 6


def test_restart_window_is_rolling(clock):
    # Agent becomes ready each time, then hangs; failures ~7 min apart never hit 3 in 10 min.
    app = _App(clock, ready_after=5, answers_probes=False)
    app._AGENT_HEALTH_PROBE_TIMEOUT_SECONDS = 400.0
    app.advance(40 * 60)
    assert len(app.reloads) >= 4
    assert not getattr(app, "_agent_restart_halted", False)


def test_ready_agent_hanging_fast_is_capped(clock):
    app = _App(clock, ready_after=5, answers_probes=False)
    app.advance(600)
    assert len(app.reloads) == 3
    assert app._agent_restart_halted is True


def test_ready_event_switches_state():
    app = SimpleNamespace(_agent_lifecycle_state="starting", _agent_health_probe_sent_at=1.0,
                          _last_agent_health_ack=None)
    EventHandlerMixin._dispatch_agent_event(app, "agent_ready", {"pid": 42})
    assert app._agent_lifecycle_state == "ready"
    assert app._agent_health_probe_sent_at is None


def test_session_emits_ready_event():
    from distr.core.agent.session import AgentSession

    items = []
    fake = SimpleNamespace(event_queue=SimpleNamespace(put=lambda item, block=False: items.append(item)),
                           logger=SimpleNamespace(debug=lambda *a, **k: None))
    AgentSession._emit_agent_ready(fake)
    assert items and items[0][0] == "agent_ready" and items[0][1]["pid"] == os.getpid()


@pytest.mark.parametrize("ram,expected_ctx,expected_keep", [
    (8, 8192, "10m"), (16, 8192, -1), (24, 8192, -1), (36, 8192, -1), (64, 8192, -1),
])
def test_runtime_options_by_ram(ram, expected_ctx, expected_keep):
    assert sr.ollama_runtime_options({}, ram_gb=ram) == {"num_ctx": expected_ctx, "keep_alive": expected_keep}


def test_default_num_ctx_is_preserved_by_ram():
    big = 131072
    for ram in (8, 16, 36):
        assert sr.ollama_runtime_options({}, ram_gb=ram, default_num_ctx=big)["num_ctx"] == big


def test_explicit_user_settings_win():
    opts = sr.ollama_runtime_options({"ollama_num_ctx": "131072", "ollama_keep_alive": "-1"}, ram_gb=8)
    assert opts == {"num_ctx": 131072, "keep_alive": -1}
    assert sr.ollama_runtime_options({"ollama_keep_alive": "30m"}, ram_gb=64)["keep_alive"] == "30m"


def test_ram_tiers(caplog):
    assert sr.recommend_ollama_defaults(36)["conversational"] == "ornith:9b"
    assert sr.recommend_ollama_defaults(16)["conversational"] == "ornith:9b"
    with caplog.at_level("WARNING", logger="distr.core.system_resources"):
        assert sr.recommend_ollama_defaults(8)["conversational"] == "ornith:9b"
    assert "below the 16 GB minimum" in caplog.text
    assert sr.recommend_ollama_defaults(8)["vision"] == "qwen3-vl:235b-cloud"


def test_failed_process_start_still_consumes_restart_budget(clock):
    app = _App(clock, dies=True)
    app._agent_auto_restart_inflight = {'reason':'process_dead','attempt':1}
    app._on_agent_process_start_result(False)
    assert app._agent_auto_restart_times == [clock.now]
    assert app._agent_start_failed and not app._agent_ready_since_restart
    app.agent_process = None
    app.check_agent_health()
    assert app.backoffs == [20.0]


def test_only_one_slow_retry_cycle_is_scheduled(clock):
    app = _App(clock, dies=True)
    app.advance(600)
    assert len(app.reloads) == 3 and app._agent_restart_halted
    app.advance(7200)
    # Slow retry opens one fresh capped cycle, then remains halted permanently.
    assert len(app.reloads) == 6 and app._agent_restart_halted
    assert app.backoffs.count(1800.0) == 1
    assert app._agent_slow_retry_used


@pytest.fixture(scope='module')
def qt_app():
    from PyQt6 import QtCore
    assert not getattr(QtCore, '_decisions_stub', False), 'These checks require real Qt'
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    yield app


def drain_qt(qt_app, predicate, timeout=1.0):
    import threading
    import time
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        qt_app.processEvents()
        if predicate():
            return True
        threading.Event().wait(0.002)
    return predicate()


@pytest.mark.parametrize('delay', [0.0, 0.01])
def test_real_scheduler_hops_worker_callbacks_to_qt_main_thread(qt_app, delay):
    import threading
    from PyQt6.QtCore import QThread
    called = []
    app = AgentLifecycleMixin()
    worker = threading.Thread(target=lambda: app._schedule_agent_restart(
        delay, lambda: called.append(QThread.currentThread() == qt_app.thread())))
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert drain_qt(qt_app, lambda: bool(called))
    assert called == [True]


def test_actual_manual_reload_cancels_real_qtimer_backoff(clock, qt_app, monkeypatch):
    import threading
    import time
    from distr.app import agent_lifecycle as lifecycle

    app = _App(clock, dies=True)
    app._reload_lock = threading.RLock()
    app._reloading_agent = False
    app._agent_auto_restart_times = [0.0]
    app._AGENT_RESTART_BACKOFF_SECONDS = (0.0, 0.03, 0.06)
    app._schedule_agent_restart = AgentLifecycleMixin._schedule_agent_restart.__get__(app)
    starts = []
    timers = []
    class DeferredStart:
        def __init__(self, delay, callback):
            self.callback = callback
            timers.append(self)
        def start(self):
            pass
    monkeypatch.setattr(lifecycle.threading, 'Timer', DeferredStart)
    monkeypatch.setattr(lifecycle, 'load_settings_from_db', lambda: {})
    app.start_agent_session = lambda **kwargs: starts.append(kwargs)
    app._request_agent_auto_restart('process_dead', now=0.0)
    assert app._agent_auto_restart_pending
    # Exercise production manual reload, not merely its generation helper.
    AgentLifecycleMixin.reload_agent_session(app)
    generation = app._agent_restart_generation
    end = time.perf_counter() + 0.1
    drain_qt(qt_app, lambda: time.perf_counter() >= end, timeout=0.3)
    assert app.reloads == []  # stale automatic callback did not run
    assert app._agent_restart_generation == generation
    assert not app._agent_auto_restart_pending
    assert app._agent_auto_restart_inflight is None
    assert len(timers) == 1
    timers[0].callback()
    assert len(starts) == 1


@pytest.mark.parametrize('delay', [0.0, 0.01])
def test_registered_qt_slot_candidate_keeps_worker_callbacks_on_main(qt_app, monkeypatch, delay):
    """Validate the proposed decorator without modifying lifecycle product code."""
    import threading
    from PyQt6.QtCore import QThread, pyqtSlot
    from distr.app import agent_lifecycle as lifecycle

    class RegisteredInvoker(lifecycle._MainThreadInvoker):
        @pyqtSlot(object)
        def _run(self, fn):
            super()._run(fn)
    monkeypatch.setattr(lifecycle, '_MainThreadInvoker', RegisteredInvoker)
    monkeypatch.setattr(lifecycle, '_INVOKER', None)
    called = []
    app = AgentLifecycleMixin()
    worker = threading.Thread(target=lambda: app._schedule_agent_restart(
        delay, lambda: called.append(QThread.currentThread() == qt_app.thread())))
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert drain_qt(qt_app, lambda: bool(called))
    assert called == [True]
