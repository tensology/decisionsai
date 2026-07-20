"""Closed loop: steering feedback → learned rules → next routing context."""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.project_cli_backends.base import BackendStatus


def _factory(tmp_path):
    db_path = tmp_path / "learning_loop.sqlite3"
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


def _backend_ready(backend_id: str):
    backend = MagicMock()
    backend.setup_status.return_value = BackendStatus(
        id=backend_id,
        name=backend_id,
        installed=True,
        ready=True,
        state="ready",
        message="ready",
    )
    return backend


@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.project_cli_backends.get_backend")
@patch("distr.core.kanban.ticket_policy.resolve_ticket_cli_route")
def test_steering_feedback_surfaces_in_next_route_learned_context(
    mock_resolve,
    mock_get_backend,
    _mock_emit,
    tmp_path,
):
    from distr.core.orchestrator_routing import resolve_execution_route
    from distr.core.workflow.steering_memory import record_run_steering_feedback

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    captured: dict = {}

    def _capture_llm(**kwargs):
        captured.update(kwargs)
        return None

    mock_resolve.return_value = {"backend": "codex", "model": "auto", "complexity": "medium"}
    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)

    board = SimpleNamespace(id=11, orchestrator_policy=json.dumps({"routing_mode": "policy"}))
    project = SimpleNamespace(id=1, name="Demo", folder_location="/tmp/demo")
    ticket = SimpleNamespace(id=5, title="Fix checkout", description="", complexity="medium", lane_id=1)

    with patch("distr.core.workflow.steering_memory.get_session", get_session), patch(
        "distr.core.db.get_session", get_session
    ), patch("distr.core.orchestrator.get_session", get_session), patch(
        "distr.core.orchestrator_routing._call_orchestrator_llm", side_effect=_capture_llm
    ):
        with get_session() as db:
            wf = AutoWorkflow(name="Loop", description="", workflow_input="{}")
            db.add(wf)
            db.flush()
            run = AutoWorkflowRun(workflow_id=wf.id, board_id=11, status="waiting", run_data="{}")
            db.add(run)
            db.flush()
            run_id = run.id

        record_run_steering_feedback(
            run_id=run_id,
            message="Prefer Codex for backend fixes after browser validation fails.",
            workflow_id=wf.id,
            board_id=11,
            source="cursor",
            event_type="user_steer",
        )
        # A preference becomes route context only after repeat evidence unless
        # the user explicitly says always/never/remember this.
        record_run_steering_feedback(
            run_id=run_id,
            message="Prefer Codex for backend fixes after browser validation fails.",
            workflow_id=wf.id,
            board_id=11,
            source="cursor",
            event_type="user_steer",
        )

        resolve_execution_route(
            project=project,
            ticket=ticket,
            board=board,
            allow_orchestrator_override=True,
            emit_event=False,
        )

    learned = str(captured.get("learned_context") or "")
    assert "browser validation" in learned.lower() or "codex" in learned.lower()


@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.project_cli_backends.get_backend")
def test_steering_from_run_one_appears_in_run_two_prompt_context(
    mock_get_backend,
    _mock_emit,
    tmp_path,
):
    """Run N steering should surface in run N+1 agent context via board learned rules."""
    from distr.core.workflow.standards_memory import build_standards_context
    from distr.core.workflow.steering_memory import record_run_steering_feedback

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    mock_get_backend.side_effect = lambda bid: _backend_ready(bid)
    steer_message = "Always run Playwright on checkout before marking UI tickets complete."

    with patch("distr.core.workflow.steering_memory.get_session", get_session), patch(
        "distr.core.db.get_session", get_session
    ), patch("distr.core.orchestrator.get_session", get_session):
        with get_session() as db:
            wf = AutoWorkflow(name="Learning Loop", description="", workflow_input="{}")
            db.add(wf)
            db.flush()
            run_one = AutoWorkflowRun(workflow_id=wf.id, board_id=11, status="waiting", run_data="{}")
            db.add(run_one)
            db.flush()
            run_one_id = run_one.id

        record_run_steering_feedback(
            run_id=run_one_id,
            message=steer_message,
            workflow_id=wf.id,
            board_id=11,
            source="cursor",
            event_type="user_steer",
        )

        context = build_standards_context("", board_id=11)

    assert "playwright" in context.lower() or "checkout" in context.lower()
    assert "[BOARD LEARNED RULES]" in context
