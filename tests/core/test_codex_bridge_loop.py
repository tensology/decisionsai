"""Codex/IDE bridge events should become Hermes learning signal."""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock

import distr.core.db.hermes  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.hermes import HermesEvent
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowVariable
from distr.gui.web.routes.settings.workflows import register_routes


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


def _make_client():
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_codex_bridge_user_steer_is_recorded_and_captured_as_standard(monkeypatch):
    factory = _make_factory()

    with _session_ctx(factory) as session:
        workflow = AutoWorkflow(name="Codex Bridge Workflow", status="active")
        session.add(workflow)
        session.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Send to Codex",
            position=0,
            action_type="send_to_project_cli",
            status="running",
        )
        session.add(step)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="running",
            current_step_id=step.id,
            ticket_id=77,
            board_id=12,
            run_data=json.dumps({"project_id": 34, "execution_session_id": 56}),
        )
        session.add(run)
        session.flush()
        workflow_id = workflow.id
        run_id = run.id
        step_id = step.id

    def get_session():
        return _session_ctx(factory)

    append_execution_event = MagicMock()
    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr("distr.core.hermes.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.standards_memory.get_session", get_session)
    monkeypatch.setattr("distr.core.hermes.is_hermes_enabled", lambda: True)
    monkeypatch.setattr(
        "distr.core.kanban.project_execution.append_execution_event",
        append_execution_event,
    )
    monkeypatch.setattr(
        "distr.gui.web.routes.settings.workflows.increment_workflow_updated",
        MagicMock(),
        raising=False,
    )
    monkeypatch.setattr("distr.gui.web.workflow_events.increment_workflow_updated", MagicMock())

    client = _make_client()
    message = (
        "User correction: require browser validation evidence before marking "
        "frontend tickets complete."
    )
    response = client.post(
        f"/api/workflows/{workflow_id}/runs/{run_id}/codex-events",
        json={
            "event_type": "user_steer",
            "status": "observed",
            "message": message,
            "execution_session_id": 56,
            "step_id": step_id,
            "ticket_id": 77,
            "project_id": 34,
            "payload": {"source": "codex_plugin"},
            "evidence": {"note": "human corrected the run"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["event_type"] == "user_steer"
    assert body["captured_standard"] is True

    append_execution_event.assert_called_once()
    assert append_execution_event.call_args.args[:2] == (56, "user_steer")

    with _session_ctx(factory) as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        run_data = json.loads(run.run_data or "{}")
        assert run_data["last_codex_bridge_state"]["event_type"] == "user_steer"
        assert run_data["codex_bridge_events"][-1]["message"] == message

        event = session.query(HermesEvent).filter(HermesEvent.event_type == "user_steer").one()
        assert event.source == "codex"
        assert event.status == "observed"
        assert event.workflow_id == workflow_id
        assert event.run_id == run_id
        assert event.step_id == step_id
        assert event.ticket_id == 77
        assert event.board_id == 12
        assert event.project_id == 34

        adaptive = (
            session.query(AutoWorkflowVariable)
            .filter(AutoWorkflowVariable.workflow_id == workflow_id)
            .filter(AutoWorkflowVariable.name == "Adaptive Quality Memory")
            .one()
        )
        assert "browser validation evidence" in adaptive.default_value
