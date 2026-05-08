"""WebKit E2E: Workflows page shows active run context strip."""

from __future__ import annotations

import json
import time
import urllib.request

import pytest
from sqlalchemy.exc import OperationalError

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflowStep
from distr.core.workflow.dispatcher import start_workflow_run
from distr.core.workflow.service import add_step, create_workflow


pytestmark = [pytest.mark.e2e_playwright, pytest.mark.only_browser("webkit")]

BASE_URL = "http://127.0.0.1:8765"


def _retry_db_write(fn, retries: int = 8, delay_s: float = 0.35):
    for i in range(retries):
        try:
            return fn()
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower() or i == retries - 1:
                raise
            time.sleep(delay_s)


def _seed_running_workflow() -> tuple[int, str]:
    stamp = int(time.time())
    wf_name = f"UI ACTIVE RUN {stamp}"

    def _create() -> tuple[int, str]:
        workflow_id = create_workflow(name=wf_name, description="Active run ui test")
        step_id = add_step(
            workflow_id,
            name="Hold run",
            action_type="run_command",
            position=0,
        )
        with get_session() as s:
            step = s.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            step.instruction = "Hold run for active-run UI visibility."
            step.timeout_seconds = 30
            step.config = json.dumps({"command": "sleep 8; echo ui-active-run", "timeout_seconds": 20})
        return workflow_id, wf_name

    return _retry_db_write(_create)


def test_workflows_run_bar_renders_without_crashing(page):
    try:
        urllib.request.urlopen(f"{BASE_URL}/workflows/", timeout=3)
    except Exception as exc:
        pytest.skip(f"Web server not reachable at {BASE_URL}: {exc}")

    workflow_id, workflow_name = _seed_running_workflow()
    start_workflow_run(
        workflow_id,
        context="UI active-run context",
        board_id=9,
        ticket_id=29,
        run_metadata={
            "source_type": "ui_active_run_test",
            "board_id": 9,
            "board_name": "TEST PROJECT",
            "ticket_id": 29,
            "ticket_title": "T1 setup",
            "phase": "planning",
        },
    )

    page.goto(f"{BASE_URL}/workflows/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1800)

    search = page.locator("#wf-search")
    if search.count() > 0:
        search.fill(workflow_name)
        page.wait_for_timeout(1000)

    row = page.locator("#wf-list [data-id]", has_text=workflow_name).first
    assert row.count() > 0
    row.click()
    page.wait_for_timeout(4500)

    # Stability assertion: workflow can be selected and run-bar container remains present.
    run_bar = page.locator("#wf-run-bar")
    assert run_bar.count() > 0
