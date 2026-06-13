"""Tests for mid-flight harness steering."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.project_cli_backends.harness import is_steerable_backend, steer_harness
from distr.core.workflow.service import apply_run_harness_steer, _run_is_steerable
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _factory(tmp_path):
    db_path = tmp_path / "harness_steer.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_is_steerable_backend():
    assert is_steerable_backend("pi")
    assert is_steerable_backend("codex")
    assert is_steerable_backend("cursor")
    assert is_steerable_backend("cursor_ide")


@patch("distr.core.pi_rpc.get_rpc_session")
def test_steer_harness_pi_delivers(mock_get_rpc):
    rpc = MagicMock()
    rpc.is_alive = True
    rpc.steer.return_value = True
    mock_get_rpc.return_value = rpc

    result = steer_harness(message="Focus on tests only", backend_id="pi", project_id=42)
    assert result["success"] is True
    assert result["delivered"] is True
    assert result["method"] == "pi_rpc"
    rpc.steer.assert_called_once_with("Focus on tests only")


def test_steer_harness_codex_queues():
    result = steer_harness(message="Use smaller diff", backend_id="codex")
    assert result["success"] is True
    assert result["delivered"] is False
    assert result["method"] == "queued"


def test_apply_run_harness_steer_persists_queue(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    session = factory()
    wf = AutoWorkflow(name="Steer WF")
    session.add(wf)
    session.flush()
    run = AutoWorkflowRun(
        workflow_id=wf.id,
        status="running",
        run_data=json.dumps(
            {
                "execution_route": {"backend": "codex", "model": "auto"},
                "execution_session_id": 99,
            }
        ),
    )
    session.add(run)
    session.commit()
    run_id = run.id
    session.close()

    monkeypatch.setattr("distr.core.workflow.service.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.workflow.service.increment_workflow_updated", MagicMock())
    monkeypatch.setattr("distr.core.kanban.project_execution.append_execution_event", MagicMock())
    monkeypatch.setattr("distr.core.orchestrator.emit_event", MagicMock(return_value=1))
    monkeypatch.setattr("distr.core.workflow.standards_memory.capture_feedback_as_standard", MagicMock())

    result = apply_run_harness_steer(run_id, "Stop refactoring unrelated files")
    assert result["success"] is True
    assert result["delivered"] is False
    assert result["method"] == "queued"

    session = factory()
    row = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
    data = json.loads(row.run_data or "{}")
    assert data["last_harness_steer"]["message"] == "Stop refactoring unrelated files"
    session.close()


def test_run_is_steerable_blocks_route_approval():
    run = AutoWorkflowRun(status="waiting", run_data="{}")
    run_data = {"waiting_kind": "route_approval", "execution_route": {"backend": "pi"}}
    assert _run_is_steerable(run, run_data) is False


import contextlib


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
