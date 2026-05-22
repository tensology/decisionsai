"""Unit tests for Hermes correction dispatch, approval gates, and channel intake."""

from __future__ import annotations

import contextlib
import json
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.hermes import HermesCorrectionAttempt, HermesEvent
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.hermes import (
    emit_channel_intake_event,
    format_correction_instruction,
    is_hermes_enabled,
    mark_correction_dispatched,
)
from distr.core.workflow.router import StepRouter


def _make_factory(tmp_path):
    import distr.core.db.hermes  # noqa: F401
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

    with patch("distr.core.hermes.get_session", get_session), patch(
        "distr.core.hermes.is_hermes_enabled", return_value=True
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
            row = session.query(HermesEvent).filter(HermesEvent.id == event_id).first()
            assert row is not None
            assert row.event_type == "channel_intake_ticket_created"
            assert row.source == "whatsapp"
            assert row.ticket_id == 42


def test_maybe_auto_dispatch_correction_marks_attempt_dispatched(tmp_path):
    factory = _make_factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with get_session() as session:
        wf = AutoWorkflow(
            name="Dispatch WF",
            status="active",
            run_settings=json.dumps({
                "auto_dispatch_corrections": True,
                "max_correction_attempts": 2,
            }),
        )
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
        from distr.core.db.hermes import HermesValidationRecord

        validation = HermesValidationRecord(
            workflow_id=wf.id,
            run_id=run.id,
            step_id=step.id,
            verdict="fail",
        )
        session.add(validation)
        session.flush()
        attempt = HermesCorrectionAttempt(
            validation_record_id=validation.id,
            workflow_id=wf.id,
            run_id=run.id,
            step_id=step.id,
            status="queued",
            attempt_number=1,
            correction_packet="{}",
        )
        session.add(attempt)
        session.flush()
        wf_id, step_id, run_id, attempt_id = wf.id, step.id, run.id, attempt.id

    router = StepRouter()
    run_data: dict = {}
    with patch("distr.core.workflow.router.get_session", get_session), patch(
        "distr.core.hermes.get_session", get_session
    ), patch("distr.core.hermes.is_hermes_enabled", return_value=True), patch(
        "distr.core.settings.load_settings_from_db",
        return_value={"hermes_correction_provider": "codex", "hermes_correction_model": "auto"},
    ):
        with get_session() as session:
            step = session.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            decision = router._maybe_auto_dispatch_correction(
                session,
                run=run,
                step=step,
                run_id=run_id,
                verified_passed=False,
                correction_attempt_id=attempt_id,
                correction_packet={"instruction": "Correct the work"},
                run_data=run_data,
            )
        assert decision is not None
        assert decision["action"] == "correction_retry"
        assert run_data["pending_correction"]["correction_attempt_id"] == attempt_id
        with get_session() as session:
            row = session.query(HermesCorrectionAttempt).filter(
                HermesCorrectionAttempt.id == attempt_id
            ).first()
            assert row.status == "dispatched"


def test_hermes_disabled_skips_emit(tmp_path, monkeypatch):
    monkeypatch.setattr("distr.core.hermes.is_hermes_enabled", lambda: False)
    assert emit_channel_intake_event(channel="gmail", ticket_id=1) is None


def test_is_hermes_enabled_defaults_true(monkeypatch):
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"hermes_enabled": False},
    )
    assert is_hermes_enabled() is False
