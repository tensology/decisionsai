"""Gate-fix regression tests (Jackcat run 162 false completion, 2026-10-23).

Loop exhaustion while a review/ship-audit gate is failing must not route to the
report step as a success path, and a run whose latest gate result failed must
never be marked ``completed``.
"""
from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock

import distr.core.db.workflow  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
from distr.core.kanban.ticket_workflow_engagement import build_run_done_message
from distr.core.workflow import dispatcher
from distr.core.workflow.router import StepRouter, failing_gate_steps


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextlib.contextmanager
def _ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _build(db, *, max_iterations=6, loop_iteration=0):
    """Mirror the Development preset shape: understand..report with review/fix loop."""
    wf = AutoWorkflow(name="Gate workflow", workflow_type="manual", status="active")
    db.add(wf)
    db.flush()
    names = ["understand", "contract", "implement", "review", "fix", "ship audit", "report"]
    steps = {}
    for pos, name in enumerate(names):
        s = AutoWorkflowStep(workflow_id=wf.id, position=pos, name=name, action_type="send_to_project_cli")
        db.add(s)
        db.flush()
        steps[name] = s
    steps["implement"].on_fail_goto = steps["implement"].id  # self-retry, not a gate
    steps["review"].on_pass_goto = steps["ship audit"].id
    steps["review"].on_fail_goto = steps["fix"].id
    steps["fix"].on_pass_goto = steps["review"].id
    steps["fix"].on_fail_goto = steps["fix"].id
    steps["ship audit"].on_pass_goto = steps["report"].id
    steps["ship audit"].on_fail_goto = steps["fix"].id
    steps["report"].on_fail_goto = steps["fix"].id
    run = AutoWorkflowRun(
        workflow_id=wf.id,
        status="running",
        run_data=json.dumps({
            "loop_contract": {"max_iterations": max_iterations},
            "loop_iteration": loop_iteration,
            "result_packet": {"status": "running", "audit": {"final_verdict": "pass"}},
        }),
    )
    db.add(run)
    db.flush()
    return wf, steps, run


def _result(db, run, step, status):
    db.add(AutoWorkflowStepResult(step_id=step.id, run_id=run.id, status=status, agent_response=status))
    db.flush()


def test_loop_exhaustion_with_failing_review_does_not_route_to_report():
    factory = _factory()
    with _ctx(factory) as db:
        wf, steps, run = _build(db, max_iterations=2, loop_iteration=1)
        _result(db, run, steps["review"], "failed")
        _result(db, run, steps["fix"], "passed")
        _result(db, run, steps["review"], "failed")
        _result(db, run, steps["fix"], "passed")

        # The fix step passes and routes back to review: the backward edge exhausts the loop.
        target = StepRouter()._apply_loop_iteration_routing(
            db, run, steps["fix"], next_step_id=steps["review"].id, verified_passed=True,
        )
        assert target == -1, "must not jump to the report step"
        assert target != steps["report"].id
        data = json.loads(run.run_data)
        assert data["forced_terminal_status"] == "failed"
        assert data["loop_exhausted"]["failing_gate_step_ids"] == [steps["review"].id]
        assert "report step was not treated as success" in data["terminal_warning"]

        decision = StepRouter._end_run(run, status="completed")
        assert decision["status"] == "failed"
        assert run.status == "failed"
        assert decision.get("warning")


def test_loop_exhaustion_on_failing_gate_itself_is_not_completed():
    factory = _factory()
    with _ctx(factory) as db:
        wf, steps, run = _build(db, max_iterations=1)
        _result(db, run, steps["ship audit"], "failed")
        # A failing gate routing backwards also exhausts without sign-off.
        target = StepRouter()._apply_loop_iteration_routing(
            db, run, steps["ship audit"], next_step_id=steps["fix"].id, verified_passed=False,
        )
        assert target == -1
        assert json.loads(run.run_data)["forced_terminal_status"] == "failed"


def test_loop_exhaustion_with_all_gates_passing_keeps_report_path():
    factory = _factory()
    with _ctx(factory) as db:
        wf, steps, run = _build(db, max_iterations=1)
        _result(db, run, steps["review"], "passed")
        target = StepRouter()._apply_loop_iteration_routing(
            db, run, steps["fix"], next_step_id=steps["review"].id, verified_passed=True,
        )
        assert target == steps["report"].id
        assert not json.loads(run.run_data).get("forced_terminal_status")


def test_failing_gate_steps_uses_latest_result_and_ignores_self_retry_steps():
    factory = _factory()
    with _ctx(factory) as db:
        wf, steps, run = _build(db)
        _result(db, run, steps["implement"], "failed")  # self-retry step: not a gate
        _result(db, run, steps["review"], "failed")
        _result(db, run, steps["review"], "passed")
        assert failing_gate_steps(db, run_id=run.id, workflow_id=wf.id) == []
        _result(db, run, steps["ship audit"], "failed")
        assert [g.id for g in failing_gate_steps(db, run_id=run.id, workflow_id=wf.id)] == [steps["ship audit"].id]


def test_failing_gate_steps_is_safe_with_mock_db():
    assert failing_gate_steps(MagicMock(), run_id=1, workflow_id=2) == []
    assert failing_gate_steps(MagicMock(), run_id=None, workflow_id=2) == []


def _complete(monkeypatch, factory, run_id, status="completed"):
    monkeypatch.setattr(dispatcher, "get_session", lambda: _ctx(factory))
    finalize = MagicMock()
    monkeypatch.setattr(dispatcher, "_finalize_terminal_run", finalize)
    monkeypatch.setattr(dispatcher, "record_workflow_chat_event", MagicMock())
    monkeypatch.setattr(dispatcher, "increment_workflow_updated", MagicMock())
    monkeypatch.setattr(dispatcher, "enforce_validation_requirements", lambda **kw: (kw["run_status"], kw["packet"], []))
    monkeypatch.setattr(
        "distr.core.workflow.dogfood_gate.enforce_dogfood_exit_gate",
        lambda **kw: (kw["run_status"], kw["packet"], []),
    )
    monkeypatch.setattr(dispatcher, "_is_e2e_smoke_workflow", lambda _wf: False)
    assert dispatcher.complete_run(run_id, status)
    return finalize


def test_complete_run_refuses_completed_when_latest_review_failed(monkeypatch):
    factory = _factory()
    with _ctx(factory) as db:
        wf, steps, run = _build(db)
        for _ in range(5):
            _result(db, run, steps["review"], "failed")
            _result(db, run, steps["fix"], "passed")
        _result(db, run, steps["report"], "passed")
        run_id = run.id
        ship_id = steps["ship audit"].id

    finalize = _complete(monkeypatch, factory, run_id, "completed")
    with _ctx(factory) as db:
        run = db.get(AutoWorkflowRun, run_id)
        data = json.loads(run.run_data)
        assert run.status == "failed"
        assert data["completion_blocked_by_gates"]
        assert data["result_packet"]["status"] == "failed"
        assert data["result_packet"]["audit"]["final_verdict"] != "pass"
        # Unreached ship audit is not silently marked skipped as on a successful run.
        assert db.get(AutoWorkflowStep, ship_id).status != "skipped"
    assert finalize.call_args[0][2] == "failed"


def test_normal_path_review_and_ship_audit_pass_is_completed(monkeypatch):
    factory = _factory()
    with _ctx(factory) as db:
        wf, steps, run = _build(db)
        _result(db, run, steps["review"], "failed")
        _result(db, run, steps["fix"], "passed")
        _result(db, run, steps["review"], "passed")
        _result(db, run, steps["ship audit"], "passed")
        _result(db, run, steps["report"], "passed")
        run_id = run.id
        decision = StepRouter._end_run(run, status="completed")
        assert decision["status"] == "completed"

    _complete(monkeypatch, factory, run_id, "completed")
    with _ctx(factory) as db:
        run = db.get(AutoWorkflowRun, run_id)
        data = json.loads(run.run_data)
        assert run.status == "completed"
        assert "completion_blocked_by_gates" not in data
        assert data["result_packet"]["audit"]["final_verdict"] == "pass"


def test_failed_packet_verdict_is_not_pass():
    packet = dispatcher._finalize_result_packet_for_terminal_run(
        {"audit": {"final_verdict": "pass"}}, run_id=1, status="failed", risk_profile={},
    )
    assert packet["audit"]["final_verdict"] == "needs_changes"


def test_done_message_for_gate_failure_does_not_say_finished():
    warning = (
        "Iteration limit (6) reached while 'review' had not passed; "
        "the run stopped without sign-off and the report step was not treated as success."
    )
    msg = build_run_done_message(ticket_title="Jackcat", status="failed", warning=warning)
    assert "finished" not in msg.lower()
    assert "stopped before sign-off" in msg
