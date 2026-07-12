from __future__ import annotations

import contextlib
import json
import uuid

import distr.core.db.kanban  # noqa: F401
import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _factory():
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


def test_wait_for_continue_is_autonomous_unless_human_checkpoints_enabled(monkeypatch):
    from distr.core.workflow.runtime_contract import should_pause_after_step

    factory = _factory()
    monkeypatch.setattr("distr.core.workflow.runtime_contract.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        wf = AutoWorkflow(name="Runtime Contract")
        session.add(wf)
        session.flush()
        default_run = AutoWorkflowRun(workflow_id=wf.id, run_data=json.dumps({}))
        checkpoint_run = AutoWorkflowRun(
            workflow_id=wf.id,
            run_data=json.dumps({"run_settings": {"human_checkpoints": True}}),
        )
        session.add_all([default_run, checkpoint_run])
        session.flush()
        default_id = default_run.id
        checkpoint_id = checkpoint_run.id

    assert not should_pause_after_step(run_id=default_id, step_wait_for_continue=True)
    assert should_pause_after_step(run_id=checkpoint_id, step_wait_for_continue=True)
    assert not should_pause_after_step(run_id=checkpoint_id, step_wait_for_continue=True, skip_wait=True)


def test_build_step_preflight_reports_missing_command():
    from distr.core.workflow.runtime_contract import build_step_preflight

    result = build_step_preflight(
        {
            "id": 10,
            "name": "Run command",
            "action_type": "run_command",
            "instruction": "",
            "config": {},
        },
        run_id=None,
    )

    assert result["ok"] is False
    assert result["action_type"] == "run_command"
    assert result["checks"][0]["name"] == "command"
    assert result["checks"][0]["ok"] is False


def test_current_step_activity_filters_run_noise_and_other_steps(monkeypatch):
    from distr.core.workflow.runtime_contract import current_step_activity

    factory = _factory()
    monkeypatch.setattr("distr.core.workflow.runtime_contract.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        wf = AutoWorkflow(name="Activity WF")
        session.add(wf)
        session.flush()
        step_one = AutoWorkflowStep(workflow_id=wf.id, name="Step one", position=0)
        step_two = AutoWorkflowStep(workflow_id=wf.id, name="Step two", position=1)
        session.add_all([step_one, step_two])
        session.flush()
        run = AutoWorkflowRun(workflow_id=wf.id, current_step_id=step_one.id, status="running")
        session.add(run)
        session.flush()
        workflow_id = wf.id
        run_id = run.id
        current_step_id = step_one.id
        other_step_id = step_two.id
        session.add_all([
            OrchestratorEvent(
                event_uid=uuid.uuid4().hex,
                source="workflow",
                event_type="run_started",
                status="running",
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=current_step_id,
                summary="Workflow started.",
                payload=json.dumps({"orchestration": {"event_type": "run_started"}}),
                evidence=json.dumps({}),
            ),
            OrchestratorEvent(
                event_uid=uuid.uuid4().hex,
                source="workflow",
                event_type="worker_progress",
                status="running",
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=other_step_id,
                summary="Other step should not show.",
                payload=json.dumps({"orchestration": {"event_type": "worker_progress"}}),
                evidence=json.dumps({}),
            ),
            OrchestratorEvent(
                event_uid=uuid.uuid4().hex,
                source="workflow",
                event_type="worker_progress",
                status="running",
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=current_step_id,
                summary="Preflight passed.",
                payload=json.dumps({"orchestration": {"event_type": "worker_progress"}}),
                evidence=json.dumps({}),
            ),
        ])

    result = current_step_activity(workflow_id=workflow_id, run_id=run_id)

    assert result["success"] is True
    assert result["current_step_id"] == current_step_id
    assert [event["summary"] for event in result["events"]] == ["Preflight passed."]
