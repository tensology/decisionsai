from types import SimpleNamespace

from distr.core.integrations.telegram.project_control import (
    handle_project_control_message,
    parse_project_control_command,
)


class _ImmediateThread:
    def __init__(self, target, daemon=True):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


class _Manager:
    def __init__(self):
        self.sent = []

    def send_to_telegram(self, text):
        self.sent.append(text)


def test_parse_project_control_commands():
    codex = parse_project_control_command("codex fix the failing tests")
    cursor = parse_project_control_command("tell cursor to update the settings panel")
    cursor_cli = parse_project_control_command("/cursor-cli run the migration")
    status = parse_project_control_command("where am I with the workload?")

    assert codex.kind == "dispatch"
    assert codex.backend_id == "codex"
    assert codex.instruction == "fix the failing tests"
    assert cursor.backend_id == "cursor_ide"
    assert cursor.instruction == "update the settings panel"
    assert cursor_cli.backend_id == "cursor"
    assert status.kind == "status"


def test_handle_status_command_sends_workload(monkeypatch):
    monkeypatch.setattr(
        "distr.core.integrations.telegram.project_control.build_project_workload_status",
        lambda: "Current project workload:\n- active_project: #1 DecisionsAI",
    )
    manager = _Manager()

    assert handle_project_control_message(manager, "/workload") is True

    assert manager.sent == ["Current project workload:\n- active_project: #1 DecisionsAI"]


def test_handle_cursor_command_dispatches_to_backend(monkeypatch):
    calls = []
    project = SimpleNamespace(id=7, name="DecisionsAI", folder_location="/repo")

    async def fake_dispatch(project_id, command):
        calls.append((project_id, command.backend_id, command.instruction))
        return SimpleNamespace(success=True, engine="ide_ticket", output="packet created", error="")

    monkeypatch.setattr(
        "distr.core.integrations.telegram.project_control._resolve_active_project",
        lambda: project,
    )
    monkeypatch.setattr(
        "distr.core.integrations.telegram.project_control._dispatch_project_task",
        fake_dispatch,
    )
    manager = _Manager()

    assert handle_project_control_message(
        manager,
        "cursor make the workload panel readable",
        start_thread=_ImmediateThread,
    ) is True

    assert calls == [(7, "cursor_ide", "make the workload panel readable")]
    assert manager.sent[0] == "Sending that to Cursor for DecisionsAI."
    assert "work packet" in manager.sent[-1]
