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
    natural_codex = parse_project_control_command(
        "Can you go into Codex and instruct Codex in the player1sport.com project to basically do a security audit?"
    )
    natural_cursor = parse_project_control_command(
        "Can you send a message to cursor in the Website project? Create a new chat that asks it to audit auth."
    )
    status = parse_project_control_command("where am I with the workload?")

    assert codex.kind == "dispatch"
    assert codex.backend_id == "codex"
    assert codex.instruction == "fix the failing tests"
    assert cursor.backend_id == "cursor"
    assert cursor.instruction == "update the settings panel"
    assert cursor_cli.backend_id == "cursor"
    assert natural_codex.backend_id == "codex"
    assert natural_codex.project_hint == "player1sport.com"
    assert natural_codex.instruction == "do a security audit?"
    assert natural_cursor.backend_id == "cursor"
    assert natural_cursor.project_hint == "Website"
    assert natural_cursor.instruction == "audit auth."
    assert status.kind == "status"


def test_parse_project_control_leaves_ide_planning_conversational():
    codex = parse_project_control_command(
        "Can we talk through what Codex should do in the Website project before sending anything?"
    )
    cursor = parse_project_control_command(
        "Can we plan what Cursor should do in the Website project before dispatch?"
    )

    assert codex is None
    assert cursor is None


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
    bridge_events = []
    project = SimpleNamespace(id=7, name="DecisionsAI", folder_location="/repo")

    async def fake_dispatch(project_id, command):
        calls.append((project_id, command.backend_id, command.instruction))
        return SimpleNamespace(success=True, engine="cursor", output="Status: completed", error="")

    def fake_record_ide_event(**kwargs):
        bridge_events.append(kwargs)
        return {"chat_id": 99, "session": {"id": 123, "status": kwargs.get("status") or "running"}}

    monkeypatch.setattr(
        "distr.core.integrations.telegram.project_control._resolve_project",
        lambda hint="": project,
    )
    monkeypatch.setattr(
        "distr.core.integrations.telegram.project_control._dispatch_project_task",
        fake_dispatch,
    )
    monkeypatch.setattr("distr.core.ide_bridge.record_ide_event", fake_record_ide_event)
    manager = _Manager()

    assert handle_project_control_message(
        manager,
        "cursor make the workload panel readable",
        start_thread=_ImmediateThread,
    ) is True

    assert calls == [(7, "cursor", "make the workload panel readable")]
    assert manager.sent[0] == "Sending that to Cursor CLI for DecisionsAI."
    assert "Status: completed" in manager.sent[-1]
    assert bridge_events[0]["event_type"] == "cursor_prompt_submitted"
    assert bridge_events[0]["input_text"] == "make the workload panel readable"
    assert bridge_events[-1]["event_type"] == "cursor_completed"
    assert bridge_events[-1]["session_id"] == 123


def test_handle_codex_natural_project_hint_dispatches_named_project(monkeypatch):
    calls = []
    project = SimpleNamespace(id=11, name="player1sport.com", folder_location="/repo/player1sport.com")

    async def fake_dispatch(project_id, command):
        calls.append((project_id, command.backend_id, command.project_hint, command.instruction))
        return SimpleNamespace(success=True, engine="codex", output="Status: completed", error="")

    monkeypatch.setattr(
        "distr.core.integrations.telegram.project_control._resolve_project",
        lambda hint="": project if hint == "player1sport.com" else None,
    )
    monkeypatch.setattr(
        "distr.core.integrations.telegram.project_control._dispatch_project_task",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "distr.core.ide_bridge.record_ide_event",
        lambda **kwargs: {"chat_id": 100, "session": {"id": 456}},
    )
    manager = _Manager()

    assert handle_project_control_message(
        manager,
        "Can you go into Codex and instruct Codex in the player1sport.com project to basically do a security audit?",
        start_thread=_ImmediateThread,
    ) is True

    assert calls == [(11, "codex", "player1sport.com", "do a security audit?")]
    assert manager.sent[0] == "Sending that to Codex for player1sport.com."
    assert "Status: completed" in manager.sent[-1]
