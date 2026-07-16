import contextlib
import json
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.workflow.dispatcher import StepDispatcher, approve_pre_execution_step
from distr.core.workflow.interactions import allowed_actions_for_kind


@contextlib.contextmanager
def _session(factory):
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def test_requested_deployment_gate_waits_before_dispatch_and_resumes_once():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with _session(factory) as db:
        workflow = AutoWorkflow(name="Ship", status="active")
        db.add(workflow)
        db.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Deploy release",
            action_type="send_to_project_cli",
            instruction="Deploy the approved release",
        )
        db.add(step)
        db.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="running",
            current_step_id=step.id,
            run_data=json.dumps({
                "requested_execution_policy": {
                    "version": 1,
                    "approval_before_roles": ["deployment"],
                }
            }),
        )
        db.add(run)
        db.flush()
        ids = (run.id, step.id)

    provider = lambda: _session(factory)
    notification = Mock()
    with patch("distr.core.workflow.dispatcher.get_session", side_effect=provider), \
         patch("distr.core.workflow.dispatcher.record_workflow_chat_event"), \
         patch(
             "distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress",
             notification,
         ), \
         patch("distr.core.orchestrator.emit_approval_event"):
        gated = StepDispatcher()._enter_requested_pre_execution_gate(
            {"id": ids[1], "name": "Deploy release", "config": {}},
            ids[0],
        )

    assert gated is True
    notification.assert_called_once()
    with _session(factory) as db:
        run = db.get(AutoWorkflowRun, ids[0])
        step = db.get(AutoWorkflowStep, ids[1])
        assert run.status == "waiting"
        assert step.status == "waiting"
        assert json.loads(run.run_data)["waiting_kind"] == "pre_execution_approval"

    dispatch = Mock(return_value={"success": True})
    started = []

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            started.append(True)
            self.target(*self.args)

    with patch("distr.core.workflow.dispatcher.get_session", side_effect=provider), \
         patch("distr.core.workflow.dispatcher._dispatch_workflow_step", dispatch), \
         patch("distr.core.workflow.dispatcher.threading.Thread", ImmediateThread):
        result = approve_pre_execution_step(ids[0], ids[1], response_text="approve")

    assert result["success"] is True
    assert result["queued"] is True
    assert started == [True]
    dispatch.assert_called_once_with(ids[0], ids[1])
    with _session(factory) as db:
        run = db.get(AutoWorkflowRun, ids[0])
        step = db.get(AutoWorkflowStep, ids[1])
        data = json.loads(run.run_data)
        assert run.status == "running"
        assert step.status == "pending"
        assert ids[1] in data["approved_pre_execution_steps"]
        assert "waiting_kind" not in data

    assert allowed_actions_for_kind("pre_execution_approval") == [
        "approve",
        "stop",
    ]
    engine.dispose()
