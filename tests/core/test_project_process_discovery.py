from __future__ import annotations

import os
import psutil

from distr.gui.web.routes.settings.projects import (
    _discover_project_server_processes,
    _managed_process_tree_pids,
    _process_memory_bytes,
    _stop_discovered_project_process,
)


class _ListedProcess:
    def __init__(self, pid: int, ppid: int, command: list[str], cwd: str):
        self.info = {"pid": pid, "ppid": ppid, "cmdline": command}
        self._cwd = cwd

    def cwd(self) -> str:
        return self._cwd


def test_process_memory_bytes_reports_current_process_memory():
    assert _process_memory_bytes(os.getpid()) > 0


def test_project_process_discovery_returns_only_root_development_servers(monkeypatch, tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    processes = [
        _ListedProcess(101, 1, ["npm", "run", "dev"], str(project)),
        _ListedProcess(102, 101, ["node", str(project / "node_modules/.bin/vite")], str(project)),
        _ListedProcess(103, 1, ["python", "-m", "http.server", "8000"], str(outside)),
        _ListedProcess(104, 1, ["sleep", "100"], str(project)),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda _fields: processes)

    assert _discover_project_server_processes(str(project)) == [
        {
            "process_id": "discovered:101",
            "pid": 101,
            "ppid": 1,
            "command": "npm run dev",
            "cwd": str(project),
            "purpose": "project_server",
            "managed": False,
            "memory_bytes": 0,
        }
    ]
    assert _discover_project_server_processes(str(project), excluded_pids={101}) == [
        {
            "process_id": "discovered:102",
            "pid": 102,
            "ppid": 101,
            "command": f"node {project / 'node_modules/.bin/vite'}",
            "cwd": str(project),
            "purpose": "project_server",
            "managed": False,
            "memory_bytes": 0,
        }
    ]


def test_managed_terminal_process_tree_is_excluded_from_discovery(monkeypatch):
    class Child:
        def __init__(self, pid: int):
            self.pid = pid

    class ManagedProcess:
        def children(self, recursive: bool = False):
            assert recursive is True
            return [Child(202), Child(203)]

    monkeypatch.setattr(psutil, "Process", lambda pid: ManagedProcess())

    assert _managed_process_tree_pids({101}) == {101, 202, 203}


def test_discovered_process_stop_revalidates_project_and_stops_children(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()

    class Process:
        def __init__(self, pid: int, *, child: bool = False):
            self.pid = pid
            self.child = child
            self.terminated = False

        def cmdline(self):
            return ["npm", "run", "dev"]

        def cwd(self):
            return str(project)

        def children(self, recursive: bool = False):
            return [child] if not self.child and recursive else []

        def terminate(self):
            self.terminated = True

    child = Process(202, child=True)
    parent = Process(201)
    monkeypatch.setattr(psutil, "Process", lambda _pid: parent)
    monkeypatch.setattr(psutil, "wait_procs", lambda targets, timeout: (targets, []))

    assert _stop_discovered_project_process(201, str(project)) is True
    assert parent.terminated is True
    assert child.terminated is True
    assert _stop_discovered_project_process(201, str(tmp_path / "other")) is False
