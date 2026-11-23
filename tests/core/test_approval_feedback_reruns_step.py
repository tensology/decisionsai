import json
from unittest.mock import Mock

import pytest

from distr.core import orchestration_events
from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.workflow import dispatcher, steering_memory
from distr.core.workflow.interactions import _apply_standard_workflow_interaction, _checkpoint_scope
from distr.core.workflow.router import StepRouter


@pytest.fixture
def approval_wait():
    with get_session() as db:
        workflow = AutoWorkflow(name="Approval feedback regression")
        db.add(workflow)
        db.flush()
        step = AutoWorkflowStep(workflow_id=workflow.id, name="Review output", status="waiting", require_approval=True)
        db.add(step)
        db.flush()
        run_data = {
            "waiting_kind": "approval",
            "waiting_prompt": "Approve this output?",
            "waiting_result": "Original output",
            "waiting_passed": True,
        }
        run = AutoWorkflowRun(
            workflow_id=workflow.id, current_step_id=step.id, status="waiting", run_data=json.dumps(run_data),
        )
        db.add(run)
        db.flush()
        item = {
            "run_id": run.id,
            "step_id": step.id,
            "kind": "approval",
            "scope_json": json.dumps(_checkpoint_scope(db.connection(), run.id)),
        }
        db.commit()
    return item, run_data


@pytest.fixture
def workflow_events(monkeypatch):
    chat = Mock()
    orchestration = Mock()
    monkeypatch.setattr(dispatcher, "record_workflow_chat_event", chat)
    monkeypatch.setattr(orchestration_events, "emit_orchestration_event", orchestration)
    monkeypatch.setattr(steering_memory, "record_run_steering_feedback", Mock())
    return chat, orchestration


def test_approval_feedback_dispatches_same_step(approval_wait, workflow_events, monkeypatch):
    item, original_data = approval_wait
    run_id, step_id = item["run_id"], item["step_id"]
    chat, orchestration = workflow_events
    resume = Mock()
    route = Mock()
    monkeypatch.setattr(StepRouter, "resume_from_feedback", resume)
    monkeypatch.setattr(StepRouter, "route", route)

    def dispatch(step_to_run, target_run):
        assert (step_to_run, target_run) == (step_id, run_id)
        with get_session() as db:
            run = db.get(AutoWorkflowRun, run_id)
            step = db.get(AutoWorkflowStep, step_id)
            assert run.status == "running"
            assert run.current_step_id == step_id
            assert step.status == "queued"
            assert step.require_approval
            data = json.loads(run.run_data)
            assert data["feedback"] == "Approve only after adding a receipt check."
            assert data.keys().isdisjoint(original_data)
        return {"status": "dispatched"}

    redispatch = Mock(side_effect=dispatch)
    monkeypatch.setattr(dispatcher.StepDispatcher, "run_in_workflow", redispatch)
    result = _apply_standard_workflow_interaction(
        item=item, action="feedback", response_text="  Approve only after adding a receipt check.\n", source="web",
    )

    assert result == {"success": True, "action": "retry_step", "step_id": step_id, "dispatch": {"status": "dispatched"}}
    redispatch.assert_called_once_with(step_id, run_id)
    resume.assert_not_called()
    route.assert_not_called()
    chat.assert_called_once_with(
        run_id, "resumed", status="running", step_id=step_id, summary="Re-running the step with your feedback.",
    )
    assert [call.kwargs["event_type"] for call in orchestration.call_args_list] == ["workflow_step_feedback_received"]


@pytest.mark.parametrize("response_text", ["", " \n\t "])
def test_blank_approval_feedback_leaves_checkpoint_waiting(approval_wait, workflow_events, monkeypatch, response_text):
    item, original_data = approval_wait
    chat, orchestration = workflow_events
    resume = Mock()
    redispatch = Mock()
    monkeypatch.setattr(StepRouter, "resume_from_feedback", resume)
    monkeypatch.setattr(dispatcher.StepDispatcher, "run_in_workflow", redispatch)

    result = _apply_standard_workflow_interaction(item=item, action="feedback", response_text=response_text, source="web")

    assert result == {"success": False, "status": "waiting", "status_code": 422, "error": "Feedback text is required."}
    with get_session() as db:
        run = db.get(AutoWorkflowRun, item["run_id"])
        assert run.status == "waiting"
        assert run.current_step_id == item["step_id"]
        assert json.loads(run.run_data) == original_data
        assert db.get(AutoWorkflowStep, item["step_id"]).status == "waiting"
    resume.assert_not_called()
    redispatch.assert_not_called()
    chat.assert_not_called()
    orchestration.assert_not_called()


def test_approve_still_resumes_through_approval_gate(approval_wait, workflow_events, monkeypatch):
    item, _ = approval_wait
    _, orchestration = workflow_events
    decision = {"action": "waiting"}
    route = Mock(return_value=decision)
    redispatch = Mock()
    monkeypatch.setattr(StepRouter, "route", route)
    monkeypatch.setattr(dispatcher.StepDispatcher, "run_in_workflow", redispatch)
    monkeypatch.setattr("distr.core.orchestrator.emit_event", Mock())

    result = _apply_standard_workflow_interaction(item=item, action="approve", response_text="", source="web")

    assert result == decision
    route.assert_called_once_with(
        item["step_id"], "Original output\n\n[FEEDBACK]: approve", True, item["run_id"],
        skip_wait=True, skip_approval=True,
    )
    redispatch.assert_not_called()
    grants = [call.kwargs for call in orchestration.call_args_list if call.kwargs["event_type"] == "route_approval_granted"]
    assert len(grants) == 1
    assert (grants[0]["status"], grants[0]["run_id"], grants[0]["step_id"]) == ("granted", item["run_id"], item["step_id"])
