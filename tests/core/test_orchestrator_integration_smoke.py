"""
Integration smoke test — exercises Hermes features built across sessions.

This is not a browser E2E test. It validates that orchestration subsystems
compose correctly against an isolated SQLite DB with mocked LLM/harness calls.
"""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock, patch

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.skills.catalog import infer_skills_for_ticket, merge_transfer_skills
from distr.core.workflow.service import apply_run_harness_steer, apply_run_route_approval
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _factory(tmp_path):
    db_path = tmp_path / "hermes_integration.sqlite3"
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


def test_orchestrator_integration_smoke_skills_route_steer(tmp_path, monkeypatch):
    """Skills inference + route approval + harness steer compose on one run."""
    factory = _factory(tmp_path)

    # Skills: ticket text maps to google skill
    skills = infer_skills_for_ticket("Deploy Cloud Run service with BigQuery export")
    assert "cloud-run-basics" in skills or "bigquery-basics" in skills
    merged = merge_transfer_skills(
        workflow_pre_chain=["pre-flight-review"],
        inferred_skills=skills,
        advisory_skills=["gemini-api"],
    )
    assert "pre-flight-review" in merged

    with _session_ctx(factory) as session:
        wf = AutoWorkflow(name="Integration WF", pre_chain=json.dumps(["pre-flight-review"]))
        session.add(wf)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=wf.id,
            status="waiting",
            run_data=json.dumps(
                {
                    "waiting_kind": "route_approval",
                    "execution_route": {"backend": "codex", "model": "auto"},
                    "pending_route_approval": {"backend": "pi", "model": "auto", "rationale": "Small fix"},
                    "execution_session_id": 12,
                }
            ),
        )
        session.add(run)
        session.flush()
        run_id = run.id

    monkeypatch.setattr("distr.core.workflow.service.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.workflow.service.increment_workflow_updated", MagicMock())
    monkeypatch.setattr("distr.core.workflow.dispatcher.StepDispatcher.run_in_workflow", MagicMock(return_value={"success": True}))
    monkeypatch.setattr("distr.core.orchestrator.emit_event", MagicMock(return_value=1))
    monkeypatch.setattr("distr.core.kanban.project_execution.append_execution_event", MagicMock())
    monkeypatch.setattr("distr.core.workflow.standards_memory.capture_feedback_as_standard", MagicMock())

    approval = apply_run_route_approval(run_id, approved=True)
    assert approval["success"] is True
    assert approval["execution_route"]["backend"] == "pi"

    with _session_ctx(factory) as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        run.status = "running"
        session.commit()

    steer = apply_run_harness_steer(run_id, "Only fix the auth middleware")
    assert steer["success"] is True

    with _session_ctx(factory) as session:
        row = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        data = json.loads(row.run_data or "{}")
        assert data.get("last_harness_steer", {}).get("message") == "Only fix the auth middleware"
        assert not data.get("pending_route_approval")
