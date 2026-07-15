from __future__ import annotations

import json
import threading


def test_harness_maintenance_runs_once_off_the_startup_thread(tmp_path, monkeypatch):
    from distr.core import harness_stack

    calls: list[str] = []
    release = threading.Event()

    def maintain():
        calls.append(threading.current_thread().name)
        release.wait(timeout=2)

    monkeypatch.delenv("DECISIONSAI_SKIP_HARNESS_STACK_SETUP", raising=False)
    monkeypatch.setattr(harness_stack, "ensure_harness_stack_setup_quiet", maintain)
    monkeypatch.setattr(harness_stack, "_maintenance_thread", None)

    first = harness_stack.schedule_harness_stack_setup(home=tmp_path, delay_seconds=0)
    second = harness_stack.schedule_harness_stack_setup(home=tmp_path, delay_seconds=0)
    assert first is not None
    assert second is first
    assert first is not threading.current_thread()
    release.set()
    first.join(timeout=2)

    state = json.loads((tmp_path / ".decisions" / "harness-maintenance.json").read_text())
    assert state["status"] == "completed"
    assert calls == ["decisions-harness-maintenance"]


def test_harness_maintenance_respects_startup_opt_out(tmp_path, monkeypatch):
    from distr.core import harness_stack

    monkeypatch.setenv("DECISIONSAI_SKIP_HARNESS_STACK_SETUP", "1")
    monkeypatch.setattr(harness_stack, "_maintenance_thread", None)
    assert harness_stack.schedule_harness_stack_setup(home=tmp_path, delay_seconds=0) is None
