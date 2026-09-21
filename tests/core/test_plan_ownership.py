import socket
import subprocess
import sys

import psutil
import pytest

from distr.core.planning.ownership import current_owner, owner_is_gone


def test_current_process_cannot_be_recovered():
    assert not owner_is_gone(current_owner())


def test_dead_process_is_confirmed_without_waiting_for_a_lease():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        owner = {"host": socket.gethostname(), "pid": process.pid,
                 "started": psutil.Process(process.pid).create_time()}
        assert not owner_is_gone(owner)
        process.terminate()
        process.wait(timeout=5)
        assert owner_is_gone(owner)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_reused_pid_does_not_hold_old_turn():
    owner = current_owner()
    owner["started"] -= 100
    assert owner_is_gone(owner)


@pytest.mark.parametrize("owner", [None, {}, {"pid": -1}, {"host": "another-host", "pid": 1, "started": 1}])
def test_unknown_or_remote_owner_is_not_reclaimed(owner):
    assert not owner_is_gone(owner)


def test_permission_failure_is_not_evidence_of_death(monkeypatch):
    owner = current_owner()
    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, "Process", denied)
    assert not owner_is_gone(owner)
