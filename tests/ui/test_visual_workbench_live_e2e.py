"""Live visual artifact, baseline, regression, and correction certification."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from scripts.workflow_ticket_loop_e2e import DEFAULT_BASE_URL, WorkflowTicketLoopHarness


pytestmark = pytest.mark.live_cli_matrix


def _seed_identity(db_path: Path, workflow_id: int, board_id: int, project_id: int) -> tuple[int, int]:
    with sqlite3.connect(db_path) as db:
        chat = db.execute(
            "INSERT INTO chats "
            "(project_id, title, input, response, params, additional_context, is_archived, is_new, is_hidden, "
            "route_mode, execution_profile, autonomy_level, created_date, modified_date) "
            "VALUES (?, '[e2e] Visual workbench', '', '', '{}', '', 0, 0, 0, 'manual', 'code', 'full', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (project_id,),
        )
        run = db.execute(
            "INSERT INTO auto_workflow_runs "
            "(workflow_id, chat_id, board_id, started_at, status, run_data, step_results, variable_values) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP, 'running', ?, '{}', '{}')",
            (workflow_id, int(chat.lastrowid), board_id, json.dumps({"project_id": project_id})),
        )
        db.commit()
        return int(chat.lastrowid), int(run.lastrowid)


def _record_visual_validation(
    *, db_root: str, artifacts: dict, workflow_id: int, run_id: int, board_id: int, project_id: int,
) -> int:
    code = (
        "import json,sys; from distr.core.orchestrator import record_ui_quality_validation; "
        "a=json.loads(sys.argv[1]); "
        "print(record_ui_quality_validation(artifacts=a, workflow_id=int(sys.argv[2]), "
        "run_id=int(sys.argv[3]), board_id=int(sys.argv[4]), project_id=int(sys.argv[5]), "
        "baseline_set_id=int(sys.argv[6])))"
    )
    env = dict(os.environ)
    env["DECISIONS_DB_DIR"] = db_root
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            json.dumps(artifacts),
            str(workflow_id),
            str(run_id),
            str(board_id),
            str(project_id),
            str(artifacts["visual_baseline_id"]),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return int(result.stdout.strip().splitlines()[-1])


def test_live_visual_plan_baseline_detects_and_clears_regression(tmp_path: Path) -> None:
    if os.environ.get("DECISIONS_RUN_LIVE_VISUAL_E2E") != "1":
        pytest.skip("Set DECISIONS_RUN_LIVE_VISUAL_E2E=1 to run live visual certification.")
    harness = WorkflowTicketLoopHarness(DEFAULT_BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {DEFAULT_BASE_URL}")

    project_dir = tmp_path / "visual-workbench"
    project_dir.mkdir()
    project = harness.api_request(
        "/projects",
        method="POST",
        data={
            "name": f"[e2e] Visual workbench {int(time.time())}",
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
        data={"name": f"[e2e] Visual workflow {int(time.time())}"},
    )
    workflow_id = int(workflow["id"])
    runtime = harness.api_request("/diagnostics/runtime")
    db_root = str(runtime["db_root"])
    db_path = Path(db_root) / "settings.db"
    chat_id, run_id = _seed_identity(db_path, workflow_id, board_id, project_id)
    stored_baseline_paths: list[Path] = []

    desktop_gold = tmp_path / "desktop-gold.png"
    mobile_gold = tmp_path / "mobile-gold.png"
    desktop_bad = tmp_path / "desktop-bad.png"
    mobile_bad = tmp_path / "mobile-bad.png"
    desktop_fixed = tmp_path / "desktop-fixed.png"
    mobile_fixed = tmp_path / "mobile-fixed.png"
    for path, size, color in (
        (desktop_gold, (1440, 900), (34, 92, 160)),
        (mobile_gold, (390, 844), (34, 92, 160)),
        (desktop_bad, (1440, 900), (210, 64, 64)),
        (mobile_bad, (390, 844), (210, 64, 64)),
        (desktop_fixed, (1440, 900), (34, 92, 160)),
        (mobile_fixed, (390, 844), (34, 92, 160)),
    ):
        Image.new("RGB", size, color=color).save(path)

    try:
        wireframe = harness.api_request(
            f"/workflows/studio/tasks/{chat_id}/artifacts",
            method="POST",
            data={
                "artifact_type": "interface",
                "title": "Approved CRUD wireframe",
                "workflow_id": workflow_id,
                "run_id": run_id,
                "content_format": "html",
                "content": "<main><h1>Customers</h1><button>Add customer</button><table aria-label='Customers'></table></main>",
                "status": "approved",
                "metadata": {"version": 1, "acceptance_criterion": "crud-layout", "step": "design"},
            },
        )
        diagram = harness.api_request(
            f"/workflows/studio/tasks/{chat_id}/artifacts",
            method="POST",
            data={
                "artifact_type": "architecture",
                "title": "CRUD validation flow",
                "workflow_id": workflow_id,
                "run_id": run_id,
                "content_format": "mermaid",
                "content": "flowchart LR\n  List --> Create --> Validate --> List",
                "status": "approved",
                "metadata": {"version": 1, "acceptance_criterion": "crud-flow", "step": "design"},
            },
        )
        assert wireframe["status"] == diagram["status"] == "approved"

        desktop_approval = harness.api_request(
            f"/workflows/{workflow_id}/runs/{run_id}/ui-feedback",
            method="POST",
            data={
                "label": "approved",
                "reason": "Approved desktop CRUD reference.",
                "board_id": board_id,
                "project_id": project_id,
                "screenshot_paths": [str(desktop_gold)],
                "save_as_visual_baseline": True,
                "visual_baseline_name": "E2E CRUD Gold",
                "baseline_screen_name": "CRUD desktop",
            },
        )
        baseline_id = int(desktop_approval["visual_baseline"]["id"])
        mobile_approval = harness.api_request(
            f"/workflows/{workflow_id}/runs/{run_id}/ui-feedback",
            method="POST",
            data={
                "label": "approved",
                "reason": "Approved mobile CRUD reference.",
                "board_id": board_id,
                "project_id": project_id,
                "screenshot_paths": [str(mobile_gold)],
                "save_as_visual_baseline": True,
                "visual_baseline_name": "E2E CRUD Gold",
                "baseline_screen_name": "CRUD mobile",
            },
        )
        assert int(mobile_approval["visual_baseline"]["id"]) == baseline_id
        stored_baseline_paths = [
            Path(str(screen["screenshot_path"]))
            for screen in mobile_approval["visual_baseline"].get("screens", [])
        ]
        readiness = harness.api_request(
            f"/workflows/visual-baselines/readiness?baseline_set_id={baseline_id}"
        )
        assert readiness["visual_baseline_readiness"]["ready"] is True
        assert readiness["visual_baseline_readiness"]["screen_count"] == 2

        shared = {
            "before_unavailable_reason": "Initial implementation is the comparison subject.",
            "flow_summary": "Open the CRUD list, create a customer, validate, and return to the list.",
            "happy_path_steps": ["Open list", "Create customer", "Validate", "Return to list"],
            "click_count": 3,
            "layout_hierarchy_notes": "List title, primary action, filters, table, then pagination.",
            "visual_baseline_id": baseline_id,
            "visual_diff_threshold": 0.01,
        }
        failed_id = _record_visual_validation(
            db_root=db_root,
            artifacts={
                **shared,
                "after_screenshot": str(desktop_bad),
                "screenshots_by_screen": {
                    "CRUD desktop": str(desktop_bad),
                    "CRUD mobile": str(mobile_bad),
                },
            },
            workflow_id=workflow_id,
            run_id=run_id,
            board_id=board_id,
            project_id=project_id,
        )
        passed_id = _record_visual_validation(
            db_root=db_root,
            artifacts={
                **shared,
                "before_screenshot": str(desktop_bad),
                "after_screenshot": str(desktop_fixed),
                "screenshots_by_screen": {
                    "CRUD desktop": str(desktop_fixed),
                    "CRUD mobile": str(mobile_fixed),
                },
            },
            workflow_id=workflow_id,
            run_id=run_id,
            board_id=board_id,
            project_id=project_id,
        )
        with sqlite3.connect(db_path) as db:
            rows = db.execute(
                "SELECT id, verdict, payload FROM orchestrator_validation_records WHERE id IN (?, ?) ORDER BY id",
                (failed_id, passed_id),
            ).fetchall()
        assert [row[1] for row in rows] == ["fail", "pass"]
        failed = json.loads(rows[0][2])["visual_baseline"]
        corrected = json.loads(rows[1][2])["visual_baseline"]
        assert {screen["status"] for screen in failed["screen_results"]} == {"fail"}
        assert {screen["status"] for screen in corrected["screen_results"]} == {"pass"}
        artifacts = harness.api_request(f"/workflows/studio/tasks/{chat_id}/artifacts")["items"]
        assert {item["content_format"] for item in artifacts} == {"html", "mermaid"}
    finally:
        harness._best_effort_api_delete(f"/workflows/{workflow_id}")
        harness._best_effort_api_delete(f"/tickets/boards/{board_id}")
        harness._best_effort_api_delete(f"/projects/{project_id}")
        for path in stored_baseline_paths:
            path.unlink(missing_ok=True)
        if stored_baseline_paths:
            stored_baseline_paths[0].parent.rmdir()
