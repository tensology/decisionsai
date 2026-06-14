"""Workflow active-run API exposes the UI state developers need while loops run."""

from __future__ import annotations

import json
import time

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.workflow.service import get_active_run, get_active_runs


def _seed_active_loop_run() -> tuple[int, int]:
    stamp = int(time.time() * 1000)
    with get_session() as session:
        workflow = AutoWorkflow(
            name=f"API loop visibility {stamp}",
            description="Active loop visibility contract",
            status="active",
            workflow_type="manual",
            workflow_input=json.dumps({"loop_contract": {"max_iterations": 3}}),
        )
        session.add(workflow)
        session.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            position=1,
            name="Validate in Playwright",
            action_type="playwright",
            step_type="playwright",
            status="running",
            config=json.dumps({
                "skills": ["webapp-testing", "verification-loop"],
                "tools": ["playwright", "browser_use"],
            }),
        )
        session.add(step)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="running",
            current_step_id=step.id,
            run_data=json.dumps({
                "loop_contract": {"max_iterations": 3},
                "loop_iteration": 1,
                "execution_route": {
                    "backend": "codex",
                    "model": "gpt-5-codex",
                    "source": "orchestrator_override",
                    "rationale": "UI regression needs browser validation.",
                    "skills": ["executing-plans", "webapp-testing"],
                },
                "result_packet": {
                    "status": "running",
                    "summary": "Route selected and ticket context transferred.",
                },
            }),
        )
        session.add(run)
        session.flush()
        workflow_id = int(workflow.id)
        run_id = int(run.id)
        session.commit()
    return workflow_id, run_id


def test_active_run_payload_exposes_loop_and_current_step_context():
    workflow_id, _run_id = _seed_active_loop_run()

    active = get_active_run(workflow_id)

    assert active is not None
    assert active["loop_iteration"] == 1
    assert active["loop_max_iterations"] == 3
    assert active["loop_label"] == "Loop 1 / 3"
    assert active["current_step_action_type"] == "playwright"
    assert active["current_step_tools"] == ["playwright", "browser_use"]
    assert active["current_step_skills"] == ["webapp-testing", "verification-loop"]
    assert active["execution_route"]["skills"] == ["executing-plans", "webapp-testing"]


def test_active_runs_list_payload_exposes_same_developer_visibility_contract():
    workflow_id, run_id = _seed_active_loop_run()

    runs = get_active_runs(workflow_id=workflow_id)
    run = next(item for item in runs if item["id"] == run_id)

    assert run["loop_iteration"] == 1
    assert run["loop_max_iterations"] == 3
    assert run["loop_label"] == "Loop 1 / 3"
    assert run["current_step_action_type"] == "playwright"
    assert run["current_step_tools"] == ["playwright", "browser_use"]
    assert run["current_step_skills"] == ["webapp-testing", "verification-loop"]
