"""Live restart recovery for an in-flight direct Development worker."""

from __future__ import annotations

import json
import os
import shutil
import signal
import time
import urllib.request
from pathlib import Path

import pytest

from distr.core.app_restart import spawn_restart_process
from scripts.workflow_ticket_loop_e2e import DEFAULT_BASE_URL, WorkflowTicketLoopHarness


pytestmark = pytest.mark.live_cli_matrix


def _health() -> dict:
    with urllib.request.urlopen(f"{DEFAULT_BASE_URL}/health", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_health(timeout: float = 60, *, different_from_pid: int | None = None) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            health = _health()
            if different_from_pid is None or int(health["pid"]) != different_from_pid:
                return health
        except Exception:
            pass
        time.sleep(1)
    raise AssertionError("Decisions server did not return after controlled restart.")


def _wait_for_execution(harness: WorkflowTicketLoopHarness, chat_id: int, statuses: set[str], timeout: float) -> dict:
    deadline = time.time() + timeout
    latest: dict = {}
    while time.time() < deadline:
        try:
            latest = harness.api_request(f"/workflows/studio/tasks/{chat_id}/execution")
            if str(latest.get("status") or "") in statuses:
                return latest
        except Exception as exc:
            latest = {"request_error": repr(exc)}
        time.sleep(1)
    raise AssertionError(f"Execution did not reach {statuses}: {latest}")


def test_live_direct_worker_recovers_after_server_restart_without_stale_identity(tmp_path: Path) -> None:
    if os.environ.get("DECISIONS_RUN_LIVE_RESTART_E2E") != "1":
        pytest.skip("Set DECISIONS_RUN_LIVE_RESTART_E2E=1 to run live restart certification.")
    harness = WorkflowTicketLoopHarness(DEFAULT_BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {DEFAULT_BASE_URL}")

    project_dir = tmp_path / "direct-restart"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("Disposable restart certification fixture.\n", encoding="utf-8")
    project = harness.api_request(
        "/projects",
        method="POST",
        data={
            "name": f"E2E Direct restart {int(time.time())}",
            "folder_location": str(project_dir),
            "coding_backend": "pi",
            "coding_backend_model": "auto",
        },
    )
    project_id = int(project["id"])
    project_board = harness.api_request(f"/projects/{project_id}/kanban-board").get("board") or {}
    chat_id = 0
    try:
        started = harness.api_request(
            "/workflows/studio/tasks",
            method="POST",
            timeout=60,
            data={
                "prompt": (
                    "Inspect README.md, then create interrupted-proof.txt containing INTERRUPTED_WORK. "
                    "Read it back and finish. Work directly in one turn."
                ),
                "title": "Live direct restart recovery",
                "project_id": project_id,
                "route_mode": "auto",
                "autonomy_level": "full",
                "permission_mode": "trusted",
                "routing_assessment": {
                    "complexity": "low",
                    "decision_engine": "e2e",
                    "execution_mode_override": "direct",
                    "route": {"backend": "pi", "model": "auto", "model_provider": "ollama"},
                },
            },
        )
        chat_id = int(started["task"]["id"])
        running = _wait_for_execution(harness, chat_id, {"running"}, 60)
        old_session_id = int(running.get("execution_session_id") or 0)
        assert old_session_id > 0, running

        old_pid = int(_health()["pid"])
        runtime = harness.api_request("/diagnostics/runtime")
        previous_db_dir = os.environ.get("DECISIONS_DB_DIR")
        os.environ["DECISIONS_DB_DIR"] = str(runtime["db_root"])
        try:
            spawn_restart_process()
        finally:
            if previous_db_dir is None:
                os.environ.pop("DECISIONS_DB_DIR", None)
            else:
                os.environ["DECISIONS_DB_DIR"] = previous_db_dir
        os.kill(old_pid, signal.SIGTERM)
        returned = _wait_for_health(different_from_pid=old_pid)
        assert int(returned["pid"]) != old_pid

        recovered = _wait_for_execution(harness, chat_id, {"cancelled", "failed"}, 30)
        assert int(recovered.get("execution_session_id") or 0) == old_session_id, recovered
        assert "restart" in str(recovered.get("error") or "").lower() or recovered.get("status") == "cancelled"

        harness.api_request(
            f"/workflows/studio/tasks/{chat_id}/messages",
            method="POST",
            timeout=60,
            data={
                "message": (
                    "Create restart-proof.txt containing exactly RESTART_RECOVERY_GREEN on one line. "
                    "Read it back, then finish. Work directly in one turn."
                ),
                "routing_assessment": {
                    "complexity": "low",
                    "decision_engine": "e2e",
                    "execution_mode_override": "direct",
                    "route": {"backend": "pi", "model": "auto", "model_provider": "ollama"},
                },
            },
        )
        completed = _wait_for_execution(harness, chat_id, {"completed", "failed"}, 900)
        assert completed.get("status") == "completed", completed
        assert int(completed.get("execution_session_id") or 0) != old_session_id, completed
        assert (project_dir / "restart-proof.txt").read_text(encoding="utf-8").strip() == "RESTART_RECOVERY_GREEN"
    finally:
        _wait_for_health()
        if chat_id:
            try:
                harness.api_request(f"/workflows/studio/tasks/{chat_id}/execution/stop", method="POST")
            except Exception:
                pass
            harness._best_effort_api_delete(f"/workflows/studio/tasks/{chat_id}?delete_linked_ticket=false")
        if project_board.get("id"):
            harness._best_effort_api_delete(f"/tickets/boards/{int(project_board['id'])}")
        harness._best_effort_api_delete(f"/projects/{project_id}")
        if project_dir.exists():
            shutil.rmtree(project_dir)
