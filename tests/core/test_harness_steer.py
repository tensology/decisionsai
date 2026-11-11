"""Tests for mid-flight harness steering."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.project_cli_backends.harness import is_steerable_backend, steer_harness
from distr.core.workflow.coordination_plan import render_coordination_map
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


@pytest.mark.parametrize("backend_id", ["codex", "pi"])
def test_apply_run_harness_steer_persists_queue(tmp_path, monkeypatch, backend_id):
    factory = _factory(tmp_path)
    session = factory()
    wf = AutoWorkflow(name="Steer WF")
    project = Project(name="Steer project", folder_location=str(tmp_path))
    session.add_all([wf, project])
    session.flush()
    run = AutoWorkflowRun(
        workflow_id=wf.id,
        status="running",
        run_data=json.dumps(
            {
                "execution_route": {"backend": backend_id, "model": "auto"},
                "project_id": project.id,
                "execution_session_id": 99,
                "coordination_plan": {
                    "assignments": {
                        "10": {
                            "step_id": 10,
                            "position": 0,
                            "primary_route": {"backend": "codex", "model": "auto"},
                        }
                    },
                    "revisions": [],
                },
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
    monkeypatch.setattr("distr.core.workflow.steering_memory.record_run_steering_feedback", MagicMock())

    interactive_rpc = MagicMock(is_alive=True)
    interactive_rpc.steer.return_value = True
    lookup = MagicMock(return_value=interactive_rpc)
    monkeypatch.setattr("distr.core.pi_rpc.get_rpc_session", lookup)

    result = apply_run_harness_steer(run_id, "Stop refactoring unrelated files")
    lookup.assert_not_called()
    interactive_rpc.steer.assert_not_called()
    assert result["success"] is True
    assert result["delivered"] is False
    assert result["method"] == "queued"
    assert result["steering_impact"] == "plan"
    assert result["coordination_plan_revision"]["affected_step_ids"] == [10]

    session = factory()
    row = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
    data = json.loads(row.run_data or "{}")
    assert data["last_harness_steer"]["message"] == "Stop refactoring unrelated files"
    assert data["coordination_plan"]["assignments"]["10"]["steering_constraints"] == [
        "Stop refactoring unrelated files"
    ]
    assert "Human steer: Stop refactoring unrelated files" in render_coordination_map(
        data["coordination_plan"], current_step_id=10
    )
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


def test_workflow_steer_route_rejects_mismatched_workflow_and_terminal_run(tmp_path, monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.workflow_execution import register_routes

    factory = _factory(tmp_path)
    with _session_ctx(factory) as db:
        owner = AutoWorkflow(name='Owner')
        other = AutoWorkflow(name='Other')
        db.add_all([owner, other])
        db.flush()
        active = AutoWorkflowRun(workflow_id=owner.id, status='running', run_data='{}')
        completed = AutoWorkflowRun(workflow_id=owner.id, status='completed', run_data='{}')
        db.add_all([active, completed])
        db.commit()
        owner_id, other_id, active_id, completed_id = owner.id, other.id, active.id, completed.id

    monkeypatch.setattr('distr.core.workflow.service.get_session', lambda: _session_ctx(factory))
    steer = MagicMock(return_value={'success': False, 'error': 'Unexpected worker call'})
    monkeypatch.setattr('distr.core.project_cli_backends.harness.steer_harness', steer)
    app = FastAPI()
    router = APIRouter(prefix='/api')
    register_routes(router, None)
    app.include_router(router)

    with TestClient(app) as client:
        mismatch = client.post(f'/api/workflows/{other_id}/runs/{active_id}/steer', json={'message': 'Check the original request'})
        terminal = client.post(f'/api/workflows/{owner_id}/runs/{completed_id}/steer', json={'message': 'Check the original request'})
    assert mismatch.status_code == 404
    assert terminal.status_code == 409
    steer.assert_not_called()


def test_next_pi_dispatch_keeps_the_complete_queued_instruction_inside_the_prompt_limit():
    from distr.core.workflow.step_executor import StepExecutorMixin

    long_steer = 'Keep all verified files. ' * 150 + 'Final constraint: report the change without deploying it.'
    pending = [
        {'ts': 1, 'message': 'An instruction already delivered', 'delivered': True},
        {'ts': 2, 'message': 'A steer already applied', 'applied_to_prompt': True},
        {'ts': 3, 'message': long_steer},
        {'ts': 4, 'message': 'Limit the change to the approved project.'},
    ]
    original = 'Project identity\n' + 'Historical context\n' * 2000 + 'Current task'
    prompt, included = StepExecutorMixin._prepare_project_cli_dispatch_prompt(original, pending, max_chars=12000)

    assert len(prompt) <= 12000
    assert prompt.startswith('Project identity')
    assert 'Current task' in prompt
    assert long_steer in prompt and pending[3]['message'] in prompt
    assert 'An instruction already delivered' not in prompt
    assert 'A steer already applied' not in prompt
    assert included == [(4, pending[3]['message']), (3, long_steer)]
