"""Tests for pending Hermes route override approval on workflow runs."""

from __future__ import annotations

import contextlib
import json
import time
from unittest.mock import MagicMock
import pytest

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.workflow.service import (
    apply_run_provider_model_selection,
    apply_run_route_approval,
)
from distr.core.workflow.router import _apply_approved_provider_replacements
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


@pytest.mark.parametrize("synchronous_redispatch", [False, True])
def test_apply_run_route_approval_approve_updates_execution_route(
    tmp_path, monkeypatch, synchronous_redispatch
):
    factory = _factory(tmp_path)
    with _session_ctx(factory) as session:
        wf = AutoWorkflow(name="Route Approval WF")
        session.add(wf)
        session.flush()
        step = AutoWorkflowStep(
            workflow_id=wf.id,
            name="Plan",
            position=0,
            status="waiting",
        )
        session.add(step)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=wf.id,
            status="waiting",
            current_step_id=step.id,
            run_data=json.dumps(
                {
                    "waiting_kind": "provider_preflight",
                    "waiting_prompt": "Use Codex?",
                    "waiting_result": "Use Codex?",
                    "waiting_passed": True,
                    "execution_route": {
                        "backend": "pi",
                        "model": "ornith:35b",
                        "model_provider": "ollama",
                        "source": "policy",
                    },
                    "pending_route_approval": {
                        "backend": "codex",
                        "model": "auto",
                        "rationale": "Bounded planning fallback",
                    },
                    "step_routes": {
                        str(step.id): {
                            "backend": "pi",
                            "model": "ornith:35b",
                            "model_provider": "ollama",
                        },
                        "99": {
                            "backend": "pi",
                            "model": "independent-review-model",
                        },
                    },
                    "step_role_routes": {
                        "planning": {"backend": "pi", "model": "ornith:35b"},
                        "review": {"backend": "pi", "model": "independent-review-model"},
                    },
                    "ticket_group_common_metadata": {
                        "qualification_scenario_id": "multi_ticket_delivery",
                    },
                }
            ),
        )
        session.add(run)
        session.flush()
        run_id = run.id

    monkeypatch.setattr("distr.core.workflow.service.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))
    dispatch = MagicMock(return_value={"success": True})
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.StepDispatcher.run_in_workflow",
        dispatch,
    )
    monkeypatch.setattr("distr.core.workflow.service.increment_workflow_updated", MagicMock())

    result = apply_run_route_approval(
        run_id,
        approved=True,
        synchronous_redispatch=synchronous_redispatch,
    )
    assert result["success"] is True
    assert result["approved"] is True
    assert result["execution_route"]["backend"] == "codex"
    assert "model_provider" not in result["execution_route"]
    assert result["redispatched"] is True
    assert result["redispatch_mode"] == (
        "synchronous" if synchronous_redispatch else "background"
    )
    deadline = time.monotonic() + 1
    while not dispatch.called and time.monotonic() < deadline:
        time.sleep(0.01)
    dispatch.assert_called_once_with(step.id, run_id)

    with _session_ctx(factory) as session:
        row = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        data = json.loads(row.run_data or "{}")
        assert not data.get("pending_route_approval")
        assert "waiting_prompt" not in data
        assert "waiting_result" not in data
        assert "waiting_passed" not in data
        assert data.get("approved_route_override", {}).get("backend") == "codex"
        assert data["step_routes"][str(step.id)]["backend"] == "codex"
        assert data["step_routes"]["99"]["model"] == "independent-review-model"
        assert data["step_role_routes"]["planning"]["backend"] == "codex"
        assert data["step_role_routes"]["review"]["model"] == "independent-review-model"
        assert data["approved_provider_replacements"][-1] == {
            "from_backend": "pi",
            "from_model": "ornith:35b",
            "to_backend": "codex",
            "to_model": "auto",
        }
        assert data["ticket_group_common_metadata"]["approved_provider_replacements"] == data[
            "approved_provider_replacements"
        ]
        step_row = session.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == row.current_step_id).one()
        assert step_row.status == "pending"


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


def test_coordination_revision_preserves_approved_provider_replacement():
    routes = {
        "planning": {
            "backend": "pi",
            "model": "retired/free-model",
            "model_provider": "openrouter",
        },
        "review": {"backend": "pi", "model": "independent-review-model"},
    }

    updated = _apply_approved_provider_replacements(routes, [{
        "from_backend": "pi",
        "from_model": "retired/free-model",
        "to_backend": "codex",
        "to_model": "auto",
    }])

    assert updated["planning"]["backend"] == "codex"
    assert updated["planning"]["model"] == "auto"
    assert "model_provider" not in updated["planning"]
    assert updated["review"] == routes["review"]


def test_free_model_selection_preserves_visible_codex_escalation(tmp_path, monkeypatch):
    from distr.core.project_cli_backends.provider_preflight import ProviderPreflight

    factory = _factory(tmp_path)
    with _session_ctx(factory) as session:
        wf = AutoWorkflow(name="Provider Choice WF")
        session.add(wf)
        session.flush()
        step = AutoWorkflowStep(workflow_id=wf.id, name="Plan", position=0, status="waiting")
        session.add(step)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=wf.id,
            status="waiting",
            current_step_id=step.id,
            run_data=json.dumps({
                "waiting_kind": "provider_preflight",
                "execution_route": {
                    "backend": "pi",
                    "model": "ornith:35b",
                    "fallback_chain": [
                        {"backend": "codex", "model": "auto", "automatic": True}
                    ],
                },
                "provider_free_candidates": [
                    {"backend": "pi", "model_provider": "openrouter", "model": "free/coder:free"}
                ],
                "pending_route_approval": {
                    "backend": "pi",
                    "model_provider": "openrouter",
                    "model": "free/coder:free",
                },
            }),
        )
        session.add(run)
        session.flush()
        run_id = run.id

    monkeypatch.setattr("distr.core.workflow.service.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.probe_openrouter_model_readiness",
        lambda **_kwargs: ProviderPreflight(
            provider="openrouter",
            model="free/coder:free",
            status="ready",
            ready=True,
            message="ready",
        ),
    )
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})
    monkeypatch.setattr("distr.core.workflow.service.increment_workflow_updated", MagicMock())
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.StepDispatcher.run_in_workflow",
        MagicMock(return_value={"success": True}),
    )

    result = apply_run_provider_model_selection(run_id, 0)

    assert result["success"] is True
    assert result["execution_route"]["paid_fallback_route"]["backend"] == "codex"
