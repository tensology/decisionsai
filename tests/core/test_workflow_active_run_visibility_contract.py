"""Workflow active-run API exposes the UI state developers need while loops run."""

from __future__ import annotations

import json
import time
from datetime import timedelta

from distr.core.db import get_session
from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.workflow.service import get_active_run, get_active_runs
from distr.core.workflow.router import StepRouter
from distr.core.db.time import utc_now_naive


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
    assert active["loop_label"] == "Pass 2 · 1/3 retries"
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
    assert run["loop_label"] == "Pass 2 · 1/3 retries"
    assert run["current_step_action_type"] == "playwright"
    assert run["current_step_tools"] == ["playwright", "browser_use"]
    assert run["current_step_skills"] == ["webapp-testing", "verification-loop"]


def test_active_runs_expose_latest_worker_activity_and_heartbeat():
    workflow_id, run_id = _seed_active_loop_run()
    with get_session() as session:
        execution = ProjectExecutionSession(
            project_id=999,
            workflow_id=workflow_id,
            run_id=run_id,
            route_backend="codex",
            selected_model="gpt-5-codex",
            status="running",
        )
        session.add(execution)
        session.flush()
        session.add(ProjectExecutionEvent(
            session_id=execution.id,
            event_type="heartbeat",
            status="running",
            message="Codex CLI is still running (20s)",
        ))
        session.commit()

    run = next(item for item in get_active_runs(workflow_id=workflow_id) if item["id"] == run_id)

    assert run["last_activity"]["event_type"] == "heartbeat"
    assert run["last_heartbeat"]["message"] == "Codex CLI is still running (20s)"
    assert run["last_heartbeat"]["backend"] == "codex"
    assert run["heartbeat_age_seconds"] is not None
    assert run["activity_state"] == "active"


def test_active_runs_label_overdue_worker_heartbeat_without_timing_out_waiting_user():
    workflow_id, run_id = _seed_active_loop_run()
    with get_session() as session:
        execution = ProjectExecutionSession(
            project_id=999,
            workflow_id=workflow_id,
            run_id=run_id,
            route_backend="codex",
            selected_model="gpt-5-codex",
            status="running",
        )
        session.add(execution)
        session.flush()
        session.add(ProjectExecutionEvent(
            session_id=execution.id,
            event_type="heartbeat",
            status="running",
            message="Codex CLI is still running",
            created_at=utc_now_naive() - timedelta(seconds=125),
        ))
        session.commit()

    run = next(item for item in get_active_runs(workflow_id=workflow_id) if item["id"] == run_id)
    assert run["heartbeat_age_seconds"] >= 120
    assert run["activity_state"] == "stale"

    with get_session() as session:
        stored = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
        stored.status = "waiting"
        session.commit()
    waiting = next(item for item in get_active_runs(workflow_id=workflow_id) if item["id"] == run_id)
    assert waiting["activity_state"] == "waiting_for_user"


def test_browser_use_action_is_an_explicit_step_tool():
    assert StepRouter._step_tools_for_action("browser_use") == ["browser_use"]


def test_active_run_tool_fallbacks_are_specific_capabilities():
    assert StepRouter._step_tools_for_action("agent_instruction") == ["agent"]
    assert StepRouter._step_tools_for_action("execute_code") == ["python"]
    assert StepRouter._step_tools_for_action("run_command") == ["shell"]
    assert StepRouter._step_tools_for_action("http_request") == ["http"]
    assert StepRouter._step_tools_for_action("play_recording") == ["macro"]
    assert StepRouter._step_tools_for_action("send_to_project_cli") == ["cli"]
