"""Live direct Development route certification against the running Decisions server."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from scripts.workflow_ticket_loop_e2e import DEFAULT_BASE_URL, WorkflowTicketLoopHarness


pytestmark = pytest.mark.live_cli_matrix


def _wait_for_terminal(harness: WorkflowTicketLoopHarness, chat_id: int, timeout: float = 900) -> dict:
    deadline = time.time() + timeout
    latest: dict = {}
    while time.time() < deadline:
        latest = harness.api_request(f"/workflows/studio/tasks/{chat_id}/execution")
        if str(latest.get("status") or "") in {"completed", "failed", "cancelled"}:
            return latest
        time.sleep(2)
    raise AssertionError(f"Direct Development execution did not finish: {latest}")


def _run_case(harness: WorkflowTicketLoopHarness, root: Path, case: str) -> dict:
    manual = case in {"codex_luna", "manual_failure"}
    expected_failure = case == "manual_failure"
    fallback = case == "auto_fallback"
    project_dir = root / f"direct-{case}"
    project_dir.mkdir(parents=True)
    (project_dir / "README.md").write_text("Disposable Decisions direct-route certification fixture.\n", encoding="utf-8")
    project = harness.api_request(
        "/projects",
        method="POST",
        data={
            "name": f"E2E Direct {case} {int(time.time())}",
            "folder_location": str(project_dir),
            "coding_backend": "codex" if manual or fallback else "pi",
            "coding_backend_model": (
                "decisions-invalid-model-e2e"
                if expected_failure or fallback
                else "gpt-5.6-luna"
                if manual
                else "auto"
            ),
        },
    )
    project_id = int(project["id"])
    project_board = harness.api_request(f"/projects/{project_id}/kanban-board").get("board") or {}
    chat_id = 0
    marker = {
        "codex_luna": "DIRECT_CODEX_LUNA_GREEN",
        "auto_fallback": "DIRECT_FALLBACK_GREEN",
        "manual_failure": "DIRECT_MANUAL_FAILURE_MUST_NOT_EXIST",
    }.get(case, "DIRECT_AUTO_GREEN")
    try:
        payload = {
            "prompt": (
                f"Create direct-proof.txt in this project containing exactly {marker} on one line. "
                "Do not change any other file. Read the file back to verify it, then finish. "
                "Work directly in one turn."
            ),
            "title": f"Live direct route {case}",
            "project_id": project_id,
            "route_mode": "manual" if manual else "auto",
            "backend": "codex" if manual else None,
            "provider": "openai" if manual else "ollama",
            "model_name": (
                "decisions-invalid-model-e2e"
                if expected_failure
                else "gpt-5.6-luna"
                if manual
                else ""
            ),
            "reasoning_effort": "low",
            "service_tier": "standard",
            "autonomy_level": "full",
            "permission_mode": "trusted",
            "routing_assessment": {
                "complexity": "low",
                "decision_engine": "e2e",
                "execution_mode_override": "direct",
                **(
                    {}
                    if manual
                    else {
                        "route": {
                            "backend": "codex" if fallback else "pi",
                            "model": "decisions-invalid-model-e2e" if fallback else "auto",
                            "model_provider": "openai" if fallback else "ollama",
                            "fallback_backend": "pi" if fallback else "",
                            "fallback_model": "auto" if fallback else "",
                            "fallback_model_provider": "ollama" if fallback else "",
                        }
                    }
                ),
            },
        }
        started = harness.api_request("/workflows/studio/tasks", method="POST", data=payload, timeout=60)
        chat_id = int(started["task"]["id"])
        terminal = _wait_for_terminal(harness, chat_id)
        assert terminal.get("status") == ("failed" if expected_failure else "completed"), terminal
        if expected_failure:
            assert not (project_dir / "direct-proof.txt").exists()
        else:
            assert (project_dir / "direct-proof.txt").read_text(encoding="utf-8").strip() == marker
        receipt = terminal.get("route_receipt") or {}
        selected = receipt.get("selected") or {}
        actual = receipt.get("actual") or {}
        assert selected.get("backend") == ("codex" if manual or fallback else "pi"), receipt
        assert actual.get("backend") == ("pi" if fallback else selected.get("backend")), receipt
        if fallback:
            assert (receipt.get("fallback") or {}).get("backend") == "pi", receipt
        if expected_failure:
            assert not receipt.get("fallback"), receipt
        if manual and not expected_failure:
            configured = receipt.get("configured") or {}
            assert configured.get("model") == "gpt-5.6-luna", receipt
            assert configured.get("reasoning_effort") == "low", receipt
            assert configured.get("service_tier") == "standard", receipt
            assert actual.get("model") == "gpt-5.6-luna", receipt
            assert actual.get("reasoning_effort") == "low", receipt
            assert actual.get("service_tier") == "standard", receipt
        return terminal
    finally:
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


def test_live_direct_development_routes_reconcile_selected_and_actual_workers(tmp_path: Path) -> None:
    if os.environ.get("DECISIONS_RUN_LIVE_DIRECT_E2E") != "1":
        pytest.skip("Set DECISIONS_RUN_LIVE_DIRECT_E2E=1 to run live direct route certification.")
    harness = WorkflowTicketLoopHarness(DEFAULT_BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {DEFAULT_BASE_URL}")
    selected = [row.strip() for row in os.environ.get("DECISIONS_LIVE_DIRECT_CASES", "pi_auto,codex_luna").split(",")]
    for case in selected:
        assert case in {"pi_auto", "codex_luna", "auto_fallback", "manual_failure"}
        _run_case(harness, tmp_path, case)
