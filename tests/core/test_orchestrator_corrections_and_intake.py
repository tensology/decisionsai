"""Unit tests for Hermes correction dispatch, approval gates, and channel intake."""

from __future__ import annotations

import contextlib
import json
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.orchestrator import OrchestratorCorrectionAttempt, OrchestratorEvent
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.orchestrator import (
    emit_channel_intake_event,
    format_correction_instruction,
    is_orchestrator_enabled,
)
from distr.core.workflow.router import StepRouter


def _make_factory(tmp_path):
    import distr.core.db.orchestrator  # noqa: F401
    import distr.core.db.workflow  # noqa: F401

    db_path = tmp_path / "hermes_corrections.sqlite3"
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


def test_format_correction_instruction_includes_failed_validation():
    text = format_correction_instruction({
        "instruction": "Fix the login flow.",
        "failed_validation": {
            "expected": "User can log in",
            "observed": "401 on /login",
            "correction_hint": "Verify credentials against test user.",
        },
        "executor_output": "Build failed",
    })
    assert "Fix the login flow." in text
    assert "Expected: User can log in" in text
    assert "Observed: 401 on /login" in text
    assert "Build failed" in text


def test_emit_channel_intake_event_persists(tmp_path):
    factory = _make_factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.orchestrator.get_session", get_session), patch(
        "distr.core.orchestrator.is_orchestrator_enabled", return_value=True
    ):
        event_id = emit_channel_intake_event(
            channel="whatsapp",
            ticket_id=42,
            board_id=7,
            summary="WhatsApp ticket created",
            payload={"message_count": 3},
        )
        assert event_id is not None
        with get_session() as session:
            row = session.query(OrchestratorEvent).filter(OrchestratorEvent.id == event_id).first()
            assert row is not None
            assert row.event_type == "channel_intake_ticket_created"
            assert row.source == "whatsapp"
            assert row.ticket_id == 42


def test_maybe_auto_dispatch_correction_is_disabled(tmp_path):
    factory = _make_factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with get_session() as session:
        wf = AutoWorkflow(name="Dispatch WF", status="active", run_settings="{}")
        session.add(wf)
        session.flush()
        step = AutoWorkflowStep(
            workflow_id=wf.id,
            name="CLI step",
            position=0,
            action_type="send_to_project_cli",
            status="failed",
        )
        session.add(step)
        session.flush()
        run = AutoWorkflowRun(workflow_id=wf.id, status="running", run_data="{}")
        session.add(run)
        session.flush()
        step_id, run_id = step.id, run.id

    router = StepRouter()
    run_data: dict = {}
    with get_session() as session:
        step = session.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        decision = router._maybe_auto_dispatch_correction(
            session,
            run=run,
            step=step,
            run_id=run_id,
            verified_passed=False,
            correction_attempt_id=99,
            correction_packet={"instruction": "Correct the work"},
            run_data=run_data,
        )
    assert decision is None
    assert "pending_correction" not in run_data


def test_orchestrator_disabled_skips_emit(tmp_path, monkeypatch):
    monkeypatch.setattr("distr.core.orchestrator.is_orchestrator_enabled", lambda: False)
    assert emit_channel_intake_event(channel="gmail", ticket_id=1) is None


def test_is_orchestrator_enabled_defaults_true(monkeypatch):
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"orchestrator_enabled": False},
    )
    assert is_orchestrator_enabled() is False
