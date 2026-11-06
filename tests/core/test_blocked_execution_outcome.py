import json

import pytest

from distr.core.project_cli_backends.base import BackendTaskResult
from distr.core.project_cli_backends.contracts import normalize_execution_result


@pytest.mark.parametrize("output", ["Status: needs_input", "**Status:** failed", '{"status":"blocked"}',
    "Fixed and deployed.\n**Blocked on one human step only:** save the current password."])
def test_worker_blocker_overrides_successful_cli_exit(output):
    result = normalize_execution_result(BackendTaskResult(success=True, backend_id="pi", engine="pi", output=output))
    assert result.success is False
    assert result.status in {"blocked", "failed"}
    assert result.waits_for_human == (result.status == "blocked")


def test_success_and_ide_waiting_keep_existing_meaning():
    assert normalize_execution_result(BackendTaskResult(True, "pi", "pi", output="Status: completed")).success
    waiting = normalize_execution_result(BackendTaskResult(True, "codex", "codex", waits_for_human=True))
    assert waiting.status == "waiting" and waiting.success
    assert not normalize_execution_result(BackendTaskResult(True, "pi", "pi", error="worker failed")).success
    blocked = normalize_execution_result({"status": "blocked", "success": True, "error": "Missing source credential"})
    assert blocked.status == "blocked" and blocked.waits_for_human and not blocked.success


def test_durable_session_keeps_blocked_status():
    from distr.core.db import get_session
    from distr.core.db.kanban import ProjectExecutionSession, ProjectExecutionEvent
    from distr.core.db.projects import Project
    from distr.core.kanban.project_execution import complete_execution_session
    with get_session() as db:
        project = Project(name="Blocked outcome")
        db.add(project); db.flush()
        row = ProjectExecutionSession(project_id=project.id, route_type="project_cli", route_backend="pi", status="running", input_packet="{}")
        db.add(row); db.commit(); sid = int(row.id)
    result = normalize_execution_result(BackendTaskResult(True, "pi", "pi", output="Status: needs_input"))
    complete_execution_session(sid, success=result.success, status=result.status, output_packet=result.to_dict())
    with get_session() as db:
        row = db.get(ProjectExecutionSession, sid)
        assert row.status == "blocked" and not json.loads(row.output_packet)["success"]
        assert db.query(ProjectExecutionEvent).filter_by(session_id=sid, event_type="session_blocked").one().status == "blocked"
