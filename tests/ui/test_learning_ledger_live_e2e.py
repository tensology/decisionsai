"""Live Learning Ledger candidate, promotion, reuse, and rollback proof."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from scripts.workflow_ticket_loop_e2e import DEFAULT_BASE_URL, WorkflowTicketLoopHarness


pytestmark = pytest.mark.live_cli_matrix


def _insert_run(db_path: Path, workflow_id: int, board_id: int, project_id: int) -> int:
    with sqlite3.connect(db_path) as db:
        cursor = db.execute(
            "INSERT INTO auto_workflow_runs "
            "(workflow_id, board_id, started_at, status, run_data, step_results, variable_values) "
            "VALUES (?, ?, CURRENT_TIMESTAMP, 'running', ?, '{}', '{}')",
            (
                workflow_id,
                board_id,
                json.dumps({
                    "project_id": project_id,
                    "execution_route": {"backend": "codex", "model": "gpt-5.6-luna"},
                }),
            ),
        )
        db.commit()
        return int(cursor.lastrowid)


def test_live_learning_candidate_promotes_reuses_and_rolls_back(tmp_path: Path) -> None:
    if os.environ.get("DECISIONS_RUN_LIVE_LEARNING_E2E") != "1":
        pytest.skip("Set DECISIONS_RUN_LIVE_LEARNING_E2E=1 to run live learning certification.")
    harness = WorkflowTicketLoopHarness(DEFAULT_BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {DEFAULT_BASE_URL}")

    project_dir = tmp_path / "learning-ledger"
    project_dir.mkdir()
    project = harness.api_request(
        "/projects",
        method="POST",
        data={
            "name": f"[e2e] Learning ledger {int(time.time())}",
            "folder_location": str(project_dir),
            "coding_backend": "codex",
            "coding_backend_model": "gpt-5.6-luna",
        },
    )
    project_id = int(project["id"])
    board = harness.api_request(f"/projects/{project_id}/kanban-board").get("board") or {}
    board_id = int(board["id"])
    workflow = harness.api_request(
        "/workflows",
        method="POST",
        data={"name": f"[e2e] Learning workflow {int(time.time())}"},
    )
    workflow_id = int(workflow["id"])
    runtime = harness.api_request("/diagnostics/runtime")
    db_path = Path(str(runtime["db_root"])) / "settings.db"
    first_run = _insert_run(db_path, workflow_id, board_id, project_id)
    second_run = 0
    feedback = "Validate checkout with Playwright before marking the ticket complete."
    try:
        first = harness.api_request(
            f"/workflows/{workflow_id}/runs/{first_run}/steer",
            method="POST",
            data={"message": feedback},
        )
        assert first["success"] is True
        candidate = harness.api_request(
            f"/workflows/{workflow_id}/runs/{first_run}/steering-memory"
        )
        matching = [rule for rule in candidate["learned_rules"] if rule["summary"] == feedback]
        assert len(matching) == 1
        assert matching[0]["enabled"] is False
        assert matching[0]["evidence_count"] == 1
        assert feedback in candidate["prompt_preview"]

        harness.api_request(
            f"/workflows/{workflow_id}/runs/{first_run}/steer",
            method="POST",
            data={"message": feedback},
        )
        promoted = harness.api_request(
            f"/workflows/{workflow_id}/runs/{first_run}/steering-memory"
        )
        rule = next(rule for rule in promoted["learned_rules"] if rule["summary"] == feedback)
        assert rule["enabled"] is True
        assert rule["evidence_count"] == 2
        assert feedback in promoted["learned_rules_preview"]

        second_run = _insert_run(db_path, workflow_id, board_id, project_id)
        reused = harness.api_request(
            f"/workflows/{workflow_id}/runs/{second_run}/steering-memory"
        )
        assert feedback in reused["learned_rules_preview"]

        rolled_back = harness.api_request(
            f"/tickets/boards/{board_id}/orchestrator-learned-rules/{int(rule['id'])}",
            method="PATCH",
            data={"enabled": False},
        )
        assert rolled_back == {"success": True, "id": int(rule["id"]), "enabled": False}
        after_rollback = harness.api_request(
            f"/workflows/{workflow_id}/runs/{second_run}/steering-memory"
        )
        assert feedback not in after_rollback["learned_rules_preview"]
        disabled = next(row for row in after_rollback["learned_rules"] if int(row["id"]) == int(rule["id"]))
        assert disabled["enabled"] is False
    finally:
        harness._best_effort_api_delete(f"/workflows/{workflow_id}")
        harness._best_effort_api_delete(f"/tickets/boards/{board_id}")
        harness._best_effort_api_delete(f"/projects/{project_id}")
