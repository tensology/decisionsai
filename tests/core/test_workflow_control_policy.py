from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock, patch

import distr.core.db.kanban  # noqa: F401
import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.workflow.control_policy import (
    classify_learning_signal,
    classify_steering,
    decide_interruption,
    resolve_inspection_budget,
)


def _factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'control.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def test_learning_is_run_local_unless_reusable_or_explicit():
    assert classify_learning_signal("Make the green button black").disposition == "run_only"
    assert classify_learning_signal("For this ticket, skip the footer").disposition == "run_only"
    candidate = classify_learning_signal("Validate checkout with Playwright before shipping")
    assert candidate.disposition == "candidate"
    assert candidate.promote_after == 2
    assert classify_learning_signal("Always validate checkout with Playwright").disposition == "promote"


def test_steering_impact_distinguishes_worker_plan_route_and_stop():
    assert classify_steering("Use a smaller diff").impact == "local"
    assert classify_steering("Stop refactoring unrelated files").impact == "plan"
    route = classify_steering("Swap the remaining work to Cursor")
    assert route.impact == "route"
    assert route.route_preference == "cursor"
    assert classify_steering("Stop the workflow").impact == "stop"


def test_interruption_only_for_real_decisions():
    assert not decide_interruption(worker_status="failed").should_interrupt
    needed = decide_interruption(worker_status="needs_input", blockers="Missing API token")
    assert needed.should_interrupt
    assert "Missing API token" in needed.question
    assert decide_interruption(repeated_failures=2).should_interrupt
    assert decide_interruption(paid_escalation=True).should_interrupt


def test_research_no_code_implementation_gets_a_bounded_soft_budget():
    budget = resolve_inspection_budget(
        {"max_tool_calls": 30, "hard_max_tool_calls": 40},
        complexity="high",
        step_role="implementation",
        ticket_context=(
            "Research the supplied sources and produce a cited markdown brief. "
            "No code changes. Browser screenshots are required."
        ),
    )

    assert budget["ticket_profile"] == "research_or_no_code"
    assert budget["max_tool_calls"] == 10
    assert budget["hard_max_tool_calls"] == 14
    assert budget["enforcement"] == "soft"


def test_code_implementation_keeps_complexity_budget():
    budget = resolve_inspection_budget(
        {
            "max_tool_calls": 18,
            "max_tool_calls_by_complexity": {"low": 10, "medium": 18, "high": 30},
        },
        complexity="high",
        step_role="implementation",
        ticket_context="Implement checkout and run the repository tests.",
    )

    assert budget["max_tool_calls"] == 30
    assert "ticket_profile" not in budget


def test_plan_steering_revises_only_current_and_future_assignments():
    from distr.core.workflow.coordination_plan import apply_steering_to_plan

    plan = {
        "assignments": {
            "1": {"step_id": 1, "position": 0, "primary_route": {"backend": "pi"}},
            "2": {"step_id": 2, "position": 1, "primary_route": {"backend": "pi"}},
            "3": {"step_id": 3, "position": 2, "primary_route": {"backend": "pi"}},
        },
        "revisions": [],
    }
    revised, revision = apply_steering_to_plan(
        plan,
        current_step_id=2,
        message="Use Cursor for the remaining work",
        impact="route",
        route_preference="cursor",
    )

    assert revision is not None
    assert revision["affected_step_ids"] == [2, 3]
    assert "steering_constraints" not in revised["assignments"]["1"]
    assert revised["assignments"]["2"]["primary_route"]["backend"] == "pi"
    assert revised["assignments"]["3"]["primary_route"]["backend"] == "cursor"
    assert revised["assignments"]["3"]["needs_replan"] is True


def test_direct_worker_needs_input_pauses_and_retries_same_step(tmp_path, monkeypatch):
    from distr.core.workflow.dispatcher import _resume_worker_needs_input
    from distr.core.workflow.post_execution import PostExecutionMixin

    factory = _factory(tmp_path)
    with _session_ctx(factory) as db:
        workflow = AutoWorkflow(name="Development", workflow_input="{}")
        db.add(workflow)
        db.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Implement",
            position=0,
            status="running",
        )
        db.add(step)
        db.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            current_step_id=step.id,
            status="running",
            run_data=json.dumps({"project_id": 12}),
        )
        db.add(run)
        db.flush()
        run_id, step_id = int(run.id), int(step.id)

    get_session = lambda: _session_ctx(factory)
    monkeypatch.setattr("distr.core.workflow.post_execution.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.dispatcher.get_session", get_session)
    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.post_execution.increment_workflow_updated", MagicMock())
    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress",
        MagicMock(),
    )
    monkeypatch.setattr("distr.core.orchestration_events.emit_orchestration_event", MagicMock())

    paused = PostExecutionMixin()._enter_worker_needs_input_state(
        step_id=step_id,
        run_id=run_id,
        result_text=(
            "Status: needs_input\n"
            "Summary: I cannot continue without the API token.\n"
            "Blockers: Missing API token"
        ),
    )
    assert paused is True
    with _session_ctx(factory) as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).one()
        data = json.loads(run.run_data)
        assert run.status == "waiting"
        assert step.status == "waiting"
        assert data["waiting_kind"] == "worker_needs_input"
        assert "Missing API token" in data["worker_question"]

    dispatch = MagicMock(return_value={"success": True})
    with patch("distr.core.workflow.dispatcher.StepDispatcher.run_in_workflow", dispatch), patch(
        "distr.core.workflow.steering_memory.record_run_steering_feedback",
        MagicMock(),
    ):
        result = _resume_worker_needs_input(run_id, "Use token from the project environment")

    assert result["action"] == "retry_step"
    dispatch.assert_called_once_with(step_id, run_id)
    with _session_ctx(factory) as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).one()
        data = json.loads(run.run_data)
        assert run.status == "running"
        assert step.status == "queued"
        assert data["worker_answer"] == "Use token from the project environment"
        assert "waiting_kind" not in data


def test_interruption_shapes_include_question_recommendation_and_options():
    paid = decide_interruption(paid_escalation=True)
    assert paid.should_interrupt
    assert paid.question
    assert paid.recommendation
    assert "Stop" in paid.options

    repeated = decide_interruption(repeated_failures=2)
    assert repeated.should_interrupt
    assert "failed twice" in repeated.question.lower() or "change" in repeated.question.lower()
    assert repeated.options

    needed = decide_interruption(worker_status="needs_input", blockers="Missing deploy key")
    assert needed.should_interrupt
    assert "Missing deploy key" in needed.question
    assert needed.to_dict()["should_interrupt"] is True


def test_decide_workflow_next_action_uses_control_interrupt_policy():
    from distr.core.workflow.service import decide_workflow_next_action

    decision = decide_workflow_next_action(
        run_data={
            "waiting_kind": "worker_needs_input",
            "worker_question": "Which API token should I use?",
            "needs_input_context": {"blockers": "Missing API token"},
            "consecutive_step_failures": 0,
        }
    )
    assert decision["action"] == "needs_human_input"
    assert decision["question"]
    assert decision["options"]

    repeated = decide_workflow_next_action(
        run_data={"consecutive_step_failures": 2}
    )
    assert repeated["action"] == "needs_human_input"
    assert "Change" in " ".join(repeated.get("options") or []) or repeated.get("recommendation")
