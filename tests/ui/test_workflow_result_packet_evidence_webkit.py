"""WebKit E2E: Workflows Runs tab renders result-packet evidence."""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime

import pytest
from sqlalchemy.exc import OperationalError

from distr.core.db import get_session
from distr.core.db.hermes import HermesCorrectionAttempt, HermesValidationRecord
from distr.core.db.workflow import AutoWorkflowRun
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


def _seed_completed_run_with_evidence() -> tuple[int, str]:
    stamp = int(time.time())
    workflow_name = f"UI EVIDENCE RUN {stamp}"

    def _create() -> tuple[int, str]:
        workflow_id = create_workflow(
            name=workflow_name,
            description="Result packet evidence ui test",
        )
        step_id = add_step(
            workflow_id,
            name="Validate Evidence",
            action_type="computer_use",
            position=0,
        )
        packet = {
            "status": "completed",
            "summary": "Workflow result packet evidence rendered.",
            "audit": {"final_verdict": "pass", "rationale": "Validation passed."},
            "artifacts": {
                "screenshots": ["/tmp/decisions/workflow_screenshots/evidence-step.png"],
                "logs": ["/tmp/decisions/logs/evidence-run.log"],
                "diffs_or_patches": ["/tmp/decisions/patches/evidence.diff"],
                "links": ["https://example.test/evidence"],
            },
            "execution": {
                "action_trace": [
                    {
                        "step": "1",
                        "action_type": "click",
                        "description": "open evidence panel",
                        "result": "Clicked at (0.50, 0.20): True",
                    },
                    {
                        "step": "2",
                        "action_type": "type",
                        "description": "enter validation text",
                        "result": "Typed 18 chars: True",
                    },
                ],
                "validation_snapshots": [
                    {
                        "step_id": step_id,
                        "step_name": "Validate Evidence",
                        "validation_type": "text_match",
                        "expected": "Evidence panel is visible.",
                        "observed": "Evidence panel is visible with logs.",
                        "caller_passed": True,
                        "verified_passed": True,
                        "verdict": "pass",
                    },
                    {
                        "step_id": step_id,
                        "step_name": "Validate Evidence",
                        "validation_type": "ui_quality",
                        "expected": "UI work matches selected visual baseline.",
                        "observed": "Visual baseline changed.",
                        "verdict": "fail",
                        "correction_attempt_id": None,
                    }
                ],
            },
        }
        with get_session() as s:
            run = AutoWorkflowRun(
                workflow_id=workflow_id,
                status="completed",
                current_step_id=step_id,
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
                run_data=json.dumps({
                    "phase": "validation",
                    "source_type": "ui_evidence_test",
                    "result_packet": packet,
                }),
            )
            s.add(run)
            s.flush()
            validation = HermesValidationRecord(
                workflow_id=workflow_id,
                run_id=run.id,
                step_id=step_id,
                validation_type="ui_quality",
                verdict="fail",
                expected="UI work matches selected visual baseline.",
                observed="Visual baseline changed.",
            )
            s.add(validation)
            s.flush()
            attempt = HermesCorrectionAttempt(
                validation_record_id=validation.id,
                workflow_id=workflow_id,
                run_id=run.id,
                step_id=step_id,
                status="dispatched",
                attempt_number=1,
                correction_packet=json.dumps({"failed_validation": {"validation_type": "ui_quality"}}),
                dispatch_result=json.dumps({"auto_dispatch": True, "terminal_ui_quality_gate": True}),
            )
            s.add(attempt)
            s.flush()
            packet["execution"]["validation_snapshots"][-1]["correction_attempt_id"] = attempt.id
            run.run_data = json.dumps({
                "phase": "validation",
                "source_type": "ui_evidence_test",
                "result_packet": packet,
            })
            s.commit()
        return workflow_id, workflow_name

    return _retry_db_write(_create)


def test_workflows_runs_tab_shows_result_packet_evidence(page):
    try:
        urllib.request.urlopen(f"{BASE_URL}/workflows/", timeout=3)
    except Exception as exc:
        pytest.skip(f"Web server not reachable at {BASE_URL}: {exc}")

    _workflow_id, workflow_name = _seed_completed_run_with_evidence()

    page.goto(f"{BASE_URL}/workflows/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1500)

    search = page.locator("#wf-search")
    if search.count() > 0:
        search.fill(workflow_name)
        page.wait_for_timeout(900)

    row = page.locator("#wf-list [data-id]", has_text=workflow_name).first
    assert row.count() > 0
    row.click()
    page.wait_for_timeout(1200)

    page.locator(".wf-tab[data-tab='runs']").click()
    page.wait_for_timeout(900)
    page.locator(".wf-runs-subtab[data-runs-tab='recent']").click()
    page.wait_for_timeout(900)

    evidence = page.locator('[data-testid="wf-run-evidence"]').first
    assert evidence.count() > 0
    taste_controls = page.locator('[data-testid="wf-ui-taste-controls"]').first
    assert taste_controls.count() > 0
    expect_text = page.locator("#wf-runs-list").inner_text()
    assert "Workflow result packet evidence rendered." in expect_text
    assert "Verdict: pass" in expect_text
    assert "click: open evidence panel" in expect_text
    assert "Validation" in expect_text
    assert "Taste" in expect_text
    assert "Approve" in expect_text
    assert "Spacing" in expect_text
    assert "Flow" in expect_text
    assert "Evidence panel is visible." in expect_text
    assert "/tmp/decisions/workflow_screenshots/evidence-step.png" in expect_text
    assert "/tmp/decisions/logs/evidence-run.log" in expect_text
