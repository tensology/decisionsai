from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def test_cursor_ide_and_codex_ide_are_distinct_backends():
    from distr.core.project_cli_backends import list_backends, normalize_backend_id

    backend_ids = {backend.id for backend in list_backends()}
    assert "cursor_ide" in backend_ids
    assert "codex_ide" in backend_ids
    assert normalize_backend_id("cursor_ide") == "cursor_ide"
    assert normalize_backend_id("codex_ide") == "codex_ide"
    assert normalize_backend_id("cursor_cli") == "cursor"
    assert normalize_backend_id("vscode") == "cursor_ide"


def test_codex_ide_opens_codex_app_instead_of_cursor(monkeypatch):
    from distr.core.project_cli_backends.ide_handoff import open_ide_project

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with (
        patch("distr.core.project_cli_backends.ide_handoff.os.path.isdir", return_value=True),
        patch(
            "distr.core.project_cli_backends.ide_handoff._ide_open_command",
            return_value="/Applications/ChatGPT.app/Contents/Resources/codex",
        ),
        patch("distr.core.project_cli_backends.ide_handoff.subprocess.Popen") as popen,
    ):
        opened = open_ide_project("/tmp/demo-project", backend_id="codex_ide")

    assert opened is True
    assert popen.call_args.args[0] == [
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "app",
        "/tmp/demo-project",
    ]


def test_codex_ide_missing_message_does_not_tell_user_to_install_cursor():
    from distr.core.project_cli_backends.registry import CodexIdeBackend

    with patch(
        "distr.core.project_cli_backends.ide_handoff._ide_open_command",
        return_value=None,
    ):
        status = CodexIdeBackend().check_availability()

    assert status.ready is False
    assert "Codex CLI" in status.message
    assert "Cursor" not in status.message


def test_ide_handoff_writes_ticket_packet_and_starts_harness(tmp_path):
    from distr.core.project_cli_backends.base import ProjectTask
    from distr.core.project_cli_backends.registry import CursorIdeBackend

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    task = ProjectTask(
        project_id=1,
        project_name="Demo",
        folder=str(project_dir),
        instruction="Implement the ticket in the IDE.",
        workflow_id=10,
        run_id=20,
        step_id=30,
        ticket_id=124,
        execution_session_id=99,
    )
    setattr(task, "handoff_event_id", 123)
    setattr(task, "ticket_title", "Session Report feedback")

    async def run():
        with (
            patch(
                "distr.core.project_cli_backends.ide_handoff._ide_open_command",
                return_value="/usr/bin/true",
            ),
            patch("distr.core.project_cli_backends.ide_handoff.open_ide_project", return_value=True),
            patch(
                "distr.core.project_cli_backends.ide_handoff.start_cursor_harness_agent",
                return_value={"started": True, "agent": "/usr/bin/cursor-agent"},
            ) as harness,
        ):
            result = await CursorIdeBackend().send_task(task)
        harness.assert_called_once()
        return result

    result = asyncio.run(run())
    packets = list((project_dir / ".tickets").glob("ticket_124_*.md"))
    assert result.success is True
    assert result.waits_for_human is True
    assert len(packets) == 1
    body = packets[0].read_text(encoding="utf-8")
    assert "Session Report feedback" in body
    assert "DecisionsAI Work Packet" not in body
    assert "auto_continue_on_pickup: true" in body
    assert "Iteration protocol" in body
    assert "Implement the ticket in the IDE." in body
    assert "<!-- decisions-ide-meta:" in body
    assert "Started the Cursor harness" in (result.output or "")


def test_proactive_ide_packet_names_the_exact_callback_session(tmp_path):
    import shlex

    from distr.core.project_cli_backends.base import ProjectTask
    from distr.core.project_cli_backends.ide_handoff import build_ide_callback_meta, write_ide_work_packet

    folder = tmp_path / "project with $name"
    folder.mkdir()
    task = ProjectTask(project_id=3, project_name="Demo", folder=str(folder), instruction="Inspect the checkout.",
                       execution_session_id=19, origin="proactive_orchestrator")
    meta = build_ide_callback_meta(task, backend_id="codex_ide")
    meta["ticket_id"] = 7
    packet = Path(write_ide_work_packet(task, backend_id="codex_ide", meta=meta)).read_text()
    commands = [shlex.split(line.removeprefix("Reporter: ")) for line in packet.splitlines()
                if line.startswith(("python3 ", "Reporter: python3 "))]
    assert meta["callback_payload_type"] == "ide_session_event"
    assert len(commands) == 2
    for command in commands:
        assert command[command.index("--cwd") + 1] == str(folder)
        assert command[command.index("--callback-url") + 1] == meta["bridge_url"]
        assert meta["bridge_url"].endswith("/api/ide/sessions/event")
        assert command[command.index("--execution-session-id") + 1] == "19"
        assert command[command.index("--project-id") + 1] == "3"
        assert command[command.index("--ticket-id") + 1] == "7"


def test_run_project_task_sets_ide_handoff_pending(tmp_path):
    from distr.core.project_cli_backends.registry import run_project_task
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    project = SimpleNamespace(id=1, name="Demo", folder_location=str(project_dir), coding_backend="cursor_ide", coding_backend_model="auto")

    with get_session() as db:
        wf = AutoWorkflow(name="IDE test", workflow_type="manual", status="active")
        db.add(wf)
        db.flush()
        step = AutoWorkflowStep(workflow_id=wf.id, position=0, name="IDE", action_type="send_to_project_cli", step_type="send_to_project_cli", instruction="Do it", status="running")
        db.add(step)
        db.flush()
        run = AutoWorkflowRun(workflow_id=wf.id, status="running", current_step_id=step.id, run_data="{}")
        db.add(run)
        db.commit()
        wf_id = int(wf.id)
        run_id = int(run.id)
        step_id = int(step.id)

    async def dispatch():
        with (
            patch(
                "distr.core.project_cli_backends.ide_handoff._ide_open_command",
                return_value="/usr/bin/true",
            ),
            patch("distr.core.project_cli_backends.ide_handoff.open_ide_project", return_value=True),
            patch(
                "distr.core.project_cli_backends.ide_handoff.start_cursor_harness_agent",
                return_value={"started": False, "reason": "test_mode"},
            ),
        ):
            return await run_project_task(
                project,
                "IDE proof instruction",
                workflow_id=wf_id,
                run_id=run_id,
                step_id=step_id,
                backend_id_override="cursor_ide",
                origin="test",
            )

    result = asyncio.run(dispatch())
    assert result.success is True
    assert result.waits_for_human is True

    with get_session() as db:
        run_row = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
        run_data = json.loads(run_row.run_data or "{}")
        assert run_data.get("ide_handoff_pending") is True
        assert run_data.get("latest_ide_handoff", {}).get("backend_id") == "cursor_ide"
