from __future__ import annotations

import contextlib

import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
from distr.core.db.projects import Project
from distr.core.kanban.project_execution import (
    complete_execution_session,
    create_execution_session,
    execution_activity_message,
    format_execution_session_outcome,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def test_failed_session_message_explains_quota_and_next_step():
    text = format_execution_session_outcome(
        "failed",
        "Error code: 402 - {'error': {'message': 'Insufficient credits. Add credits or subscribe.', 'code': 402}}",
    )
    assert "Project execution session failed." not in text
    assert "quota or billing" in text.lower() or "credits" in text.lower()
    assert "Settings" in text


def test_failed_session_message_without_error_still_says_what_to_do():
    text = format_execution_session_outcome("failed", "")
    assert "Project execution session failed." not in text
    assert "retry" in text.lower()
    assert "Settings" in text


def test_failed_session_message_explains_missing_folder():
    text = format_execution_session_outcome("failed", "The linked project folder is unavailable.")
    assert "Relink the project folder" in text
    assert "Project execution session failed." not in text


def test_legacy_generic_activity_is_replaced_with_the_real_error():
    text = execution_activity_message(
        "Project execution session failed.",
        "failed",
        "Error code: 402 - {'error': {'message': 'Insufficient credits', 'code': 402}}",
    )
    assert "Project execution session failed." not in text
    assert "quota or billing" in text.lower() or "credits" in text.lower()


def _factory():
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


def test_complete_execution_session_stores_plain_failure_on_the_event(monkeypatch, tmp_path):
    factory = _factory()
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.kanban.project_execution.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        project = Project(name="Plain failure", folder_location=str(tmp_path), coding_backend="pi")
        session.add(project)
        session.flush()
        project_id = project.id

    session_id = create_execution_session(project_id=project_id, route_backend="pi", run_id=77, step_id=9)
    complete_execution_session(
        session_id,
        success=False,
        error="Error code: 402 - {'error': {'message': 'Insufficient credits', 'code': 402}}",
    )

    with _session_ctx(factory) as session:
        row = session.query(ProjectExecutionSession).filter_by(id=session_id).one()
        event = session.query(ProjectExecutionEvent).filter_by(session_id=session_id, event_type="session_failed").one()
        assert row.status == "failed"
        assert "Project execution session failed." not in event.message
        assert "quota or billing" in event.message.lower() or "credits" in event.message.lower()
        assert "Settings" in event.message
