"""Tests for workflow steering memory."""

from __future__ import annotations

import contextlib
import json

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun


def _factory(tmp_path):
    db_path = tmp_path / "steering.sqlite3"
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


def test_steering_log_and_context(tmp_path):
    from unittest.mock import patch

    from distr.core.workflow.steering_memory import (
        append_run_steering_entry,
        build_steering_context_for_run_id,
    )

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.workflow.steering_memory.get_session", get_session), patch(
        "distr.core.db.get_session", get_session
    ):
        with get_session() as db:
            wf = AutoWorkflow(name="W", description="", workflow_input="{}")
            db.add(wf)
            db.flush()
            run = AutoWorkflowRun(workflow_id=wf.id, status="running", run_data="{}")
            db.add(run)
            db.flush()
            run_id = run.id

        append_run_steering_entry(
            run_id,
            source="cursor",
            event_type="user_steer",
            message="The header looks wrong — align it with the baseline.",
        )
        context = build_steering_context_for_run_id(run_id)

    assert "[WORKFLOW STEERING MEMORY]" in context
    assert "header looks wrong" in context


def test_record_steering_writes_learned_rule(tmp_path):
    from unittest.mock import patch

    from distr.core.orchestrator import build_learned_rules_context
    from distr.core.workflow.steering_memory import record_run_steering_feedback

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.workflow.steering_memory.get_session", get_session), patch(
        "distr.core.db.get_session", get_session
    ), patch("distr.core.orchestrator.get_session", get_session):
        with get_session() as db:
            wf = AutoWorkflow(name="W", description="", workflow_input="{}")
            db.add(wf)
            db.flush()
            workflow_id = wf.id
            run = AutoWorkflowRun(workflow_id=workflow_id, board_id=9, status="waiting", run_data="{}")
            db.add(run)
            db.flush()
            run_id = run.id

        record_run_steering_feedback(
            run_id=run_id,
            message="Always validate checkout in Playwright before marking UI tickets done.",
            workflow_id=workflow_id,
            board_id=9,
            source="workflow",
            event_type="user_continuation",
        )
        learned = build_learned_rules_context(9)

    assert "Playwright" in learned


def test_get_run_steering_snapshot(tmp_path):
    from unittest.mock import patch

    from distr.core.workflow.steering_memory import (
        append_run_steering_entry,
        get_run_steering_snapshot,
    )

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.workflow.steering_memory.get_session", get_session), patch(
        "distr.core.db.get_session", get_session
    ), patch("distr.core.orchestrator.get_session", get_session):
        with get_session() as db:
            wf = AutoWorkflow(name="W", description="", workflow_input="{}")
            db.add(wf)
            db.flush()
            run = AutoWorkflowRun(workflow_id=wf.id, board_id=3, status="waiting", run_data="{}")
            db.add(run)
            db.flush()
            run_id = run.id

        append_run_steering_entry(
            run_id,
            source="workflow",
            event_type="user_feedback",
            message="Spacing on the checkout button is too tight.",
        )
        snapshot = get_run_steering_snapshot(run_id)

    assert snapshot is not None
    assert snapshot["run_id"] == run_id
    assert snapshot["board_id"] == 3
    assert len(snapshot["steering_log"]) == 1
    assert "checkout button" in snapshot["steering_log"][0]["message"]
    assert "checkout button" in snapshot["prompt_preview"]
