"""Live two-route workflow certification for per-step orchestration."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from scripts.workflow_ticket_loop_e2e import DEFAULT_BASE_URL, WorkflowTicketLoopHarness


pytestmark = pytest.mark.live_cli_matrix


def _scoped_route(backend: str, provider: str, model: str, role: str, *, read_only: bool = False) -> dict:
    return {
        "step_role": role,
        "read_only": read_only,
        "execution_route": {
            "enabled": True,
            "mode": "scoped",
            "scoped_model_key": f"{backend}:{provider}:{model}",
            "route_snapshot": {
                "backend_id": backend,
                "provider": provider,
                "model": model,
                "name": f"{backend} {model}",
            },
        },
    }


def test_live_workflow_executes_pi_then_codex_with_step_receipts(tmp_path: Path) -> None:
    if os.environ.get("DECISIONS_RUN_LIVE_STEP_ROUTING_E2E") != "1":
        pytest.skip("Set DECISIONS_RUN_LIVE_STEP_ROUTING_E2E=1 to run live step routing certification.")
    harness = WorkflowTicketLoopHarness(DEFAULT_BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {DEFAULT_BASE_URL}")

    project_dir = tmp_path / "step-routing"
    project_dir.mkdir()
    project = harness.api_request(
        "/projects",
        method="POST",
        data={
            "name": f"[e2e] Per-step routing {int(time.time())}",
            "folder_location": str(project_dir),
            "coding_backend": "pi",
            "coding_backend_model": "auto",
        },
    )
    project_id = int(project["id"])
    board = harness.api_request(f"/projects/{project_id}/kanban-board").get("board") or {}
    workflow = harness.api_request(
        "/workflows",
        method="POST",
        data={"name": f"[e2e] Two route workflow {int(time.time())}"},
    )
    workflow_id = int(workflow["id"])
    workflow = harness.api_request(
        f"/workflows/{workflow_id}/steps",
        method="POST",
        data={
            "name": "Implement with Pi",
            "action_type": "send_to_project_cli",
            "instruction": (
                "Create route-proof.txt in the project root containing exactly PER_STEP_ROUTE_GREEN on one line. "
                "Read it back. Return Status: completed and Blockers: none."
            ),
            "config": _scoped_route("pi", "ollama", "auto", "implementation"),
            "validation_type": "none",
        },
    )
    workflow = harness.api_request(
        f"/workflows/{workflow_id}/steps",
        method="POST",
        data={
            "name": "Review with Codex Luna",
            "action_type": "send_to_project_cli",
            "instruction": (
                "Independently verify route-proof.txt contains exactly PER_STEP_ROUTE_GREEN. Do not edit files. "
                "Return Status: completed, Tests run: exact content check passed, and Blockers: none."
            ),
            "config": _scoped_route("codex", "openai", "gpt-5.6-luna", "review", read_only=True),
            "validation_type": "none",
        },
    )
    runtime = harness.api_request("/diagnostics/runtime")
    db_path = Path(str(runtime["db_root"])) / "settings.db"
    with sqlite3.connect(db_path) as db:
        session_floor = int(db.execute("SELECT COALESCE(MAX(id), 0) FROM project_execution_sessions").fetchone()[0])
    chat_id = 0
    run_id = 0
    try:
        started = harness.api_request(
            "/workflows/studio/tasks",
            method="POST",
            data={
                "prompt": "Create the route proof, then review it using the workflow's configured step routes.",
                "title": "[e2e] Per-step route proof",
                "project_id": project_id,
                "workflow_id": workflow_id,
                "route_mode": "auto",
                "execution_profile": "code",
                "autonomy_level": "goal",
                "permission_mode": "trusted",
                "skip_human_checkpoints": True,
            },
        )
        chat_id = int((started.get("task") or {}).get("id") or 0)
        execution = started.get("execution") if isinstance(started.get("execution"), dict) else {}
        run_id = int(execution.get("run_id") or execution.get("id") or 0)
        assert chat_id
        assert run_id
        deadline = time.time() + 900
        terminal: dict = {}
        while time.time() < deadline:
            runs = harness.api_request(f"/workflows/{workflow_id}/runs")
            terminal = next((row for row in runs if int(row.get("id") or 0) == run_id), {})
            if str(terminal.get("status") or "") in {"completed", "failed", "cancelled"}:
                break
            time.sleep(2)
        assert terminal.get("status") == "completed", json.dumps(terminal, indent=2)
        assert (project_dir / "route-proof.txt").read_text(encoding="utf-8").strip() == "PER_STEP_ROUTE_GREEN"

        with sqlite3.connect(db_path) as db:
            rows = db.execute(
                "SELECT route_backend, selected_model, selection_reason, input_packet, output_packet "
                "FROM project_execution_sessions "
                "WHERE id > ? AND project_id = ? AND workflow_id = ? AND run_id = ? ORDER BY id",
                (session_floor, project_id, workflow_id, run_id),
            ).fetchall()
        assert [row[0] for row in rows] == ["pi", "codex"]
        assert rows[1][1] == "gpt-5.6-luna"
        first_input = json.loads(rows[0][3])
        second_input = json.loads(rows[1][3])
        assert (first_input["backend_id"], first_input["model_provider"], first_input["model"]) == (
            "pi",
            "ollama",
            "auto",
        )
        assert (second_input["backend_id"], second_input["model_provider"], second_input["model"]) == (
            "codex",
            "openai",
            "gpt-5.6-luna",
        )
        assert json.loads(rows[0][4])["status"] == "completed"
        assert json.loads(rows[1][4])["status"] == "completed"
    finally:
        if run_id:
            try:
                harness.api_request(f"/workflows/{workflow_id}/cancel-run/{run_id}", method="POST")
            except Exception:
                pass
        if chat_id:
            harness._best_effort_api_delete(f"/workflows/studio/tasks/{chat_id}?delete_linked_ticket=false")
        harness._best_effort_api_delete(f"/workflows/{workflow_id}")
        if board.get("id"):
            harness._best_effort_api_delete(f"/tickets/boards/{int(board['id'])}")
        harness._best_effort_api_delete(f"/projects/{project_id}")
