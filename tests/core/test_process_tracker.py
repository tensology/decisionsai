from __future__ import annotations

import os
import signal
import sys
from types import SimpleNamespace

import pytest

from distr.core import process_tracker


def test_close_multiprocessing_resources_closes_queues_and_managers():
    calls: list[str] = []

    queue = SimpleNamespace(
        close=lambda: calls.append("queue.close"),
        join_thread=lambda: calls.append("queue.join_thread"),
    )
    manager = SimpleNamespace(shutdown=lambda: calls.append("manager.shutdown"))

    process_tracker.close_multiprocessing_resources(
        queues=[None, queue],
        managers=[None, manager],
    )

    assert calls == ["queue.close", "queue.join_thread", "manager.shutdown"]


def test_run_multiprocessing_finalizers_invokes_registry(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "multiprocessing.util._run_finalizers",
        lambda: calls.append("finalized"),
    )

    process_tracker.run_multiprocessing_finalizers()

    assert calls == ["finalized"]


def test_signal_handler_routes_through_application_shutdown(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(process_tracker, "_shutdown_callback", lambda: calls.append("quit"))
    monkeypatch.setattr(
        process_tracker,
        "kill_tracked_pids",
        lambda: pytest.fail("application shutdown owns child cleanup"),
    )

    process_tracker._signal_handler(signal.SIGTERM, None)

    assert calls == ["quit"]


def test_signal_handler_fallback_cleans_up_without_keyboard_interrupt(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(process_tracker, "_shutdown_callback", None)
    monkeypatch.setattr(process_tracker, "kill_tracked_pids", lambda: calls.append("cleanup"))
    monkeypatch.setattr(process_tracker, "_get_pid_file", lambda: str(tmp_path / "missing"))

    with pytest.raises(SystemExit) as exc_info:
        process_tracker._signal_handler(signal.SIGTERM, None)

    assert exc_info.value.code == 128 + signal.SIGTERM
    assert calls == ["cleanup"]


def test_rogue_sweep_catches_frozen_multiprocessing_helpers(monkeypatch):
    current_pid = os.getpid()
    processes = [
        SimpleNamespace(info={
            "pid": current_pid,
            "name": "DecisionsAI",
            "cmdline": ["/Applications/DecisionsAI.app/Contents/MacOS/DecisionsAI"],
        }),
        SimpleNamespace(info={
            "pid": 8101,
            "name": "DecisionsAI",
            "cmdline": [
                "/Applications/DecisionsAI.app/Contents/MacOS/DecisionsAI",
                "--multiprocessing-fork",
                "tracker_fd=19",
            ],
        }),
        SimpleNamespace(info={
            "pid": 8102,
            "name": "DecisionsAI",
            "cmdline": [
                "/Applications/DecisionsAI.app/Contents/MacOS/DecisionsAI",
                "-c",
                "from multiprocessing.resource_tracker import main;main(18)",
            ],
        }),
        SimpleNamespace(info={
            "pid": 8103,
            "name": "DecisionsAI Helper",
            "cmdline": ["/Applications/DecisionsAI Helper"],
        }),
        SimpleNamespace(info={
            "pid": 8104,
            "name": "Python",
            "cmdline": ["python", "--multiprocessing-fork"],
        }),
    ]
    fake_psutil = SimpleNamespace(process_iter=lambda _fields: processes)
    killed: list[tuple[set[int], float]] = []
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(
        process_tracker,
        "kill_tracked_pids",
        lambda pids, timeout: killed.append((set(pids), timeout)),
    )

    process_tracker.kill_rogue_decisions_processes(timeout=0.25)

    assert killed == [({8101, 8102}, 0.25)]
