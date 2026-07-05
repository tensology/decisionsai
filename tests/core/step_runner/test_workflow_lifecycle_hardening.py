"""Lifecycle hardening tests for workflow run receipts and stale callbacks."""

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import distr.core.db.workflow  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
from distr.core.workflow import dispatcher
from distr.core.workflow.step_executor import StepExecutorMixin


def _make_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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


def test_workflow_run_receipt_has_consistent_terminal_shape():
    receipt = dispatcher.build_workflow_run_receipt(
        run_id=7,
        workflow_id=3,
        status="cancelled",
        steps_summary=[
            {"title": "Prepare", "status": "completed", "result": "ready"},
            {"title": "Execute", "status": "cancelled", "result": "stopped"},
        ],
        board_id=11,
        ticket_id=22,
        project_id=33,
        validation_records=[{"rule": "smoke", "status": "passed"}],
        result_packet={"final_verdict": "needs_changes"},
    )

    assert receipt == {
        "run_id": 7,
        "workflow_id": 3,
        "status": "cancelled",
        "success": False,
        "cancelled": True,
        "board_id": 11,
        "ticket_id": 22,
        "project_id": 33,
        "steps_summary": [
            {"title": "Prepare", "status": "completed", "result": "ready"},
            {"title": "Execute", "status": "cancelled", "result": "stopped"},
        ],
        "step_count": 2,
        "completed_step_count": 1,
        "has_completion_evidence": True,
        "validation_records": [{"rule": "smoke", "status": "passed"}],
        "result_packet": {"final_verdict": "needs_changes"},
    }


def test_workflow_run_context_guard_rejects_removed_context():
    run_ctx = SimpleNamespace(run_id=44)
    with dispatcher._runs_lock:
        dispatcher._active_runs[44] = run_ctx

    try:
        assert dispatcher.workflow_run_context_is_current(44, run_ctx) is True
        with dispatcher._runs_lock:
            dispatcher._active_runs.pop(44, None)
        assert dispatcher.workflow_run_context_is_current(44, run_ctx) is False
    finally:
        with dispatcher._runs_lock:
            dispatcher._active_runs.pop(44, None)


def test_finalize_terminal_run_persists_receipt_for_completed_failed_and_cancelled(monkeypatch):
    factory = _make_factory()

    def get_session():
        return _session_ctx(factory)

    monkeypatch.setattr(dispatcher, "get_session", get_session)
    monkeypatch.setattr(
        "distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge.on_workflow_completed",
        MagicMock(),
    )

    run_ids = {}
    with _session_ctx(factory) as db:
        workflow = AutoWorkflow(name="Receipt workflow", workflow_type="manual", status="active")
        db.add(workflow)
        db.flush()
        for index, status in enumerate(("completed", "failed", "cancelled"), start=1):
            step = AutoWorkflowStep(
                workflow_id=workflow.id,
                position=index,
                name=f"{status.title()} step",
                action_type="agent_instruction",
                status=status,
            )
            db.add(step)
            db.flush()
            run = AutoWorkflowRun(
                workflow_id=workflow.id,
                status=status,
                current_step_id=step.id,
                run_data=json.dumps(
                    {
                        "project_id": 123,
                        "result_packet": {"final_verdict": "pass" if status == "completed" else "needs_changes"},
                    }
                ),
            )
            db.add(run)
            db.flush()
            db.add(
                AutoWorkflowStepResult(
                    step_id=step.id,
                    run_id=run.id,
                    status=status,
                    agent_response=f"{status} evidence",
                )
            )
            run_ids[status] = run.id

    for status, run_id in run_ids.items():
        dispatcher._finalize_terminal_run(run_id, workflow.id, status)

    with _session_ctx(factory) as db:
        for status, run_id in run_ids.items():
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            run_data = json.loads(run.run_data or "{}")
            receipt = run_data["terminal_receipt"]
            assert receipt["run_id"] == run_id
            assert receipt["workflow_id"] == workflow.id
            assert receipt["status"] == status
            assert receipt["step_count"] == 1
            assert receipt["project_id"] == 123
            assert receipt["has_completion_evidence"] is True


class _FakeExecutor(StepExecutorMixin):
    def __init__(self, run_ctx):
        self.run_ctx = run_ctx
        self.recorded = []

    def _build_agent_prompt(self, step_data, run_id):
        return "agent prompt"

    def _get_run_context(self, step_id, run_id):
        return self.run_ctx

    def _augment_agent_result_with_tool_evidence(self, result_text, workflow_agent):
        return result_text

    def _record_result_and_route(self, step_id, run_id, result_text, passed):
        self.recorded.append(
            {
                "step_id": step_id,
                "run_id": run_id,
                "result_text": result_text,
                "passed": passed,
            }
        )


def test_agent_done_callback_ignores_stale_run_context():
    callbacks = []

    class FakeFuture:
        def add_done_callback(self, callback):
            callbacks.append(callback)

        def result(self, timeout=0):
            return "agent says PASS"

        def done(self):
            return True

    run_ctx = SimpleNamespace(
        run_id=55,
        workflow_agent=MagicMock(),
        event_loop=MagicMock(),
    )
    run_ctx.workflow_agent.execute.return_value = "coroutine-placeholder"
    executor = _FakeExecutor(run_ctx)

    with dispatcher._runs_lock:
        dispatcher._active_runs[55] = run_ctx

    try:
        with patch("asyncio.run_coroutine_threadsafe", return_value=FakeFuture()):
            result = executor._run_agent(
                {"id": 9, "instruction": "do the work", "timeout_seconds": 999},
                run_id=55,
            )

        assert result["async"] is True
        assert len(callbacks) == 1

        with dispatcher._runs_lock:
            dispatcher._active_runs.pop(55, None)

        callbacks[0](FakeFuture())

        assert executor.recorded == []
    finally:
        with dispatcher._runs_lock:
            dispatcher._active_runs.pop(55, None)
