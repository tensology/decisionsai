"""WebKit E2E: Kanban ticket -> Send to Workflow flow."""

from __future__ import annotations

import json
import time
import urllib.request
from urllib.error import HTTPError

import pytest
from sqlalchemy.exc import OperationalError

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

pytestmark = [pytest.mark.e2e_playwright, pytest.mark.only_browser("webkit")]

BASE_URL = "http://127.0.0.1:8765"


def _api_request(path: str, *, method: str = "GET", data: dict | None = None):
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        method=method,
        data=body,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"raw": raw}
        return exc.code, payload


def _seed_kanban_workflow_flow() -> tuple[int, str, str]:
    stamp = int(time.time())
    board_name = f"E2E TEST PROJECT {stamp}"
    ticket_title = f"E2E ticket {stamp}"

    # POST /api/tickets/tickets is auth-gated in this environment; seed ticket via DB with retry.
    for _ in range(8):
        try:
            with get_session() as s:
                workflow = AutoWorkflow(
                    name=f"E2E default workflow {stamp}",
                    description="Self-contained E2E default workflow for Kanban send-to-workflow.",
                    status="active",
                    workflow_type="manual",
                )
                s.add(workflow)
                s.flush()
                s.add(
                    AutoWorkflowStep(
                        workflow_id=workflow.id,
                        position=0,
                        name="Review ticket",
                        action_type="agent_instruction",
                        step_type="agent_instruction",
                        instruction="Review the ticket and summarize the next development step.",
                        validation_type="none",
                    )
                )
                board = KanbanBoard(
                    name=board_name,
                    description="Self-contained E2E board for Kanban workflow testing.",
                    source="database",
                    default_workflow_id=workflow.id,
                    archived=False,
                    position=0,
                )
                s.add(board)
                s.flush()
                todo_lane = KanbanLane(board_id=board.id, name="To Do", position=0)
                s.add(todo_lane)
                s.add(KanbanLane(board_id=board.id, name="Doing", position=1))
                s.add(KanbanLane(board_id=board.id, name="Done", position=2))
                s.flush()
                ticket = KanbanTicket(
                    lane_id=todo_lane.id,
                    title=ticket_title,
                    description="E2E send-to-workflow ticket",
                    priority="medium",
                    position=0,
                    linked_workflow_id=workflow.id,
                )
                s.add(ticket)
                s.flush()
                return int(board.id), board_name, ticket_title
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            time.sleep(0.35)
    pytest.skip("Could not seed ticket due to persistent sqlite lock.")


def test_kanban_send_to_workflow_modal_and_status(page):
    try:
        urllib.request.urlopen(f"{BASE_URL}/tickets/", timeout=3)
    except Exception as exc:
        pytest.skip(f"Web server not reachable at {BASE_URL}: {exc}")

    board_id, board_name, ticket_title = _seed_kanban_workflow_flow()

    page.goto(f"{BASE_URL}/tickets/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2500)
    local_tab = page.locator("#kb-src-local")
    if local_tab.count() > 0:
        local_tab.click()
        page.wait_for_timeout(600)

    # Open the seeded board from sidebar.
    board_item = page.locator("#kb-db-boards .kb-board-item", has_text=board_name).first
    for _ in range(12):
        if board_item.count() > 0:
            break
        page.wait_for_timeout(250)
    assert board_item.count() > 0
    board_item.click()
    page.wait_for_timeout(1200)

    # Open target ticket card.
    card = page.locator("#kb-lanes .kb-card", has_text=ticket_title).first
    for _ in range(10):
        if card.count() > 0:
            break
        page.wait_for_timeout(250)
    assert card.count() > 0
    send_btn = card.locator(".kb-act-workflow")
    assert send_btn.count() > 0
    send_btn.click()
    page.wait_for_timeout(500)

    workflow_modal = page.locator("#kb-send-workflow-modal")
    assert workflow_modal.count() > 0
    assert not workflow_modal.first.evaluate("el => el.classList.contains('hidden')")

    select_el = page.locator("#kb-send-workflow-select")
    selected = select_el.input_value()
    assert selected.strip() != ""

    # Smoke assertion: modal opens and resolves a non-empty default workflow selection.
    # Full network submission is covered in backend and route tests.
