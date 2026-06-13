"""Tests for pending Hermes route override approval on workflow runs."""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.workflow.service import apply_run_route_approval
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _factory(tmp_path):
    db_path = tmp_path / "route_approval.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
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


def test_apply_run_route_approval_approve_updates_execution_route(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    with _session_ctx(factory) as session:
        wf = AutoWorkflow(name="Route Approval WF")
        session.add(wf)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=wf.id,
            status="waiting",
            run_data=json.dumps(
                {
                    "waiting_kind": "route_approval",
                    "execution_route": {"backend": "codex", "model": "auto", "source": "policy"},
                    "pending_route_approval": {
                        "backend": "pi",
                        "model": "auto",
                        "rationale": "Small scoped edit",
                    },
                }
            ),
        )
        session.add(run)
        session.flush()
        run_id = run.id

    monkeypatch.setattr("distr.core.workflow.service.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.StepDispatcher.run_in_workflow",
        MagicMock(return_value={"success": True}),
    )
    monkeypatch.setattr("distr.core.workflow.service.increment_workflow_updated", MagicMock())

    result = apply_run_route_approval(run_id, approved=True)
    assert result["success"] is True
    assert result["approved"] is True
    assert result["execution_route"]["backend"] == "pi"

    with _session_ctx(factory) as session:
        row = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        data = json.loads(row.run_data or "{}")
        assert not data.get("pending_route_approval")
        assert data.get("approved_route_override", {}).get("backend") == "pi"


def test_apply_run_route_approval_reject_clears_pending(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    with _session_ctx(factory) as session:
        wf = AutoWorkflow(name="Route Reject WF")
        session.add(wf)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=wf.id,
            status="waiting",
            run_data=json.dumps(
                {
                    "waiting_kind": "route_approval",
                    "pending_route_approval": {"backend": "pi", "model": "auto"},
                }
            ),
        )
        session.add(run)
        session.flush()
        run_id = run.id

    monkeypatch.setattr("distr.core.workflow.service.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.StepDispatcher.run_in_workflow",
        MagicMock(return_value={"success": True}),
    )
    monkeypatch.setattr("distr.core.workflow.service.increment_workflow_updated", MagicMock())

    result = apply_run_route_approval(run_id, approved=False)
    assert result["success"] is True
    assert result["approved"] is False

    with _session_ctx(factory) as session:
        row = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        data = json.loads(row.run_data or "{}")
        assert not data.get("pending_route_approval")
        assert data.get("suppress_orchestrator_override") is True
