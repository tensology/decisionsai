"""WebKit E2E: Kanban ticket -> Send to Workflow flow."""

from __future__ import annotations

import json
import time
import urllib.request
from urllib.error import HTTPError

import pytest
from sqlalchemy.exc import OperationalError

from distr.core.db import get_session
from distr.core.db.kanban import KanbanTicket

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


def _seed_kanban_workflow_flow() -> tuple[int, str]:
    stamp = int(time.time())
    ticket_title = f"E2E ticket {stamp}"
    code, boards = _api_request("/api/kanban/boards")
    assert code == 200, (code, boards)
    target_board = next((b for b in boards if (b.get("name") or "").strip() == "TEST PROJECT"), None)
    if not target_board:
        pytest.skip("No 'TEST PROJECT' board available for e2e flow.")
    board_id = int(target_board["id"])
    code, board_data = _api_request(f"/api/kanban/boards/{board_id}")
    assert code == 200, (code, board_data)
    if not board_data.get("default_workflow_id"):
        pytest.skip("'TEST PROJECT' board has no default workflow configured.")

    todo_lane_id = next(
        lane["id"] for lane in board_data["lanes"] if lane["name"].lower() in {"to do", "todo"}
    )

    # POST /api/kanban/tickets is auth-gated in this environment; seed ticket via DB with retry.
    for _ in range(8):
        try:
            with get_session() as s:
                ticket = KanbanTicket(
                    lane_id=todo_lane_id,
                    title=ticket_title,
                    description="E2E send-to-workflow ticket",
                    priority="medium",
                    position=0,
                    linked_workflow_id=board_data.get("default_workflow_id"),
                )
                s.add(ticket)
                s.flush()
                return board_id, ticket_title
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            time.sleep(0.35)
    pytest.skip("Could not seed ticket due to persistent sqlite lock.")


def test_kanban_send_to_workflow_modal_and_status(page):
    try:
        urllib.request.urlopen(f"{BASE_URL}/kanban/", timeout=3)
    except Exception as exc:
        pytest.skip(f"Web server not reachable at {BASE_URL}: {exc}")

    board_id, ticket_title = _seed_kanban_workflow_flow()

    page.goto(f"{BASE_URL}/kanban/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2500)
    local_tab = page.locator("#kb-src-local")
    if local_tab.count() > 0:
        local_tab.click()
        page.wait_for_timeout(600)

    # Open the seeded board from sidebar.
    board_item = page.locator("#kb-db-boards .kb-board-item", has_text="TEST PROJECT").first
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
    card.click()
    page.wait_for_timeout(700)

    send_btn = page.locator("#kb-modal-act-workflow")
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
