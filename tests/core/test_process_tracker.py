from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from distr.core import process_tracker


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
