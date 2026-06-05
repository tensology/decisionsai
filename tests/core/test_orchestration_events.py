from __future__ import annotations

import contextlib
import json

import distr.core.db.hermes  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.hermes import HermesEvent
from distr.core.db.projects import Project
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


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


def test_orchestration_event_normalizes_legacy_names_and_keeps_voice_clean(monkeypatch):
    from distr.core.orchestration_events import (
        build_orchestration_notification,
        emit_orchestration_event,
        list_orchestration_timeline,
    )

    factory = _factory()
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))

    event_id = emit_orchestration_event(
        source="codex",
        event_type="codex_needs_input",
        status="waiting",
        workflow_id=10,
        run_id=20,
        step_id=30,
        project_id=40,
        summary="Hermes says Codex needs a decision about browser validation.",
        payload={"raw": "value"},
    )

    with _session_ctx(factory) as session:
        row = session.query(HermesEvent).filter(HermesEvent.id == event_id).one()
        payload = json.loads(row.payload)
        assert row.event_type == "needs_input"
        assert payload["orchestration"]["event_type"] == "needs_input"
        assert payload["orchestration"]["legacy_event_type"] == "codex_needs_input"

    timeline = list_orchestration_timeline(workflow_id=10, run_id=20)

    assert [item["event_type"] for item in timeline] == ["needs_input"]
    assert timeline[0]["legacy_event_type"] == "codex_needs_input"
    notification = build_orchestration_notification(timeline[0])
    assert notification["should_notify"] is True
    assert "Codex needs input" in notification["text"]
    assert "Hermes" not in notification["text"]


def test_project_execution_lifecycle_events_share_the_same_timeline(monkeypatch, tmp_path):
    from distr.core.kanban.project_execution import (
        append_execution_event,
        complete_execution_session,
        create_execution_session,
    )
    from distr.core.orchestration_events import list_orchestration_timeline

    factory = _factory()
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.kanban.project_execution.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        project = Project(name="Merrypak", folder_location=str(tmp_path), coding_backend="cursor")
        session.add(project)
        session.flush()
        project_id = project.id

    execution_session_id = create_execution_session(
        project_id=project_id,
        workflow_id=7,
        run_id=8,
        step_id=9,
        route_backend="cursor",
        selected_model="auto",
        origin="workflow",
    )
    append_execution_event(
        execution_session_id,
        "message_update",
        status="running",
        message="Cursor is checking the checkout flow.",
        payload={"backend": "cursor"},
    )
    complete_execution_session(
        execution_session_id,
        success=True,
        output_packet={"summary": "Checkout flow fixed."},
    )

    timeline = list_orchestration_timeline(workflow_id=7, run_id=8)

    assert [item["event_type"] for item in timeline] == [
        "worker_dispatched",
        "worker_progress",
        "worker_completed",
    ]
    assert {item["source"] for item in timeline} == {"cursor"}
    assert all(item["execution_session_id"] == execution_session_id for item in timeline)


def test_user_notification_event_is_recorded_without_internal_branding(monkeypatch):
    from distr.core.orchestration_events import emit_user_notification, list_orchestration_timeline

    factory = _factory()
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))

    event_id = emit_user_notification(
        channel="telegram",
        text="Hermes routed this through Codex.",
        workflow_id=1,
        run_id=2,
        project_id=3,
    )
    timeline = list_orchestration_timeline(workflow_id=1, run_id=2)

    assert event_id
    assert timeline[0]["event_type"] == "user_notified"
    assert timeline[0]["payload"]["channel"] == "telegram"
    assert "Hermes" not in timeline[0]["summary"]


def test_orchestration_timeline_exposes_surface_subtype_and_attachment(monkeypatch):
    from distr.core.orchestration_events import emit_orchestration_event, list_orchestration_timeline

    factory = _factory()
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))

    emit_orchestration_event(
        source="cursor",
        event_type="cursor_prompt_submitted",
        status="observed",
        project_id=3,
        execution_session_id=4,
        summary="Cursor prompt submitted.",
        payload={
            "surface": "cursor",
            "subtype": "ide_prompt_submitted",
            "correlation_id": "corr-1",
            "thread_id": "thread-1",
            "is_workflow_attached": False,
        },
    )

    timeline = list_orchestration_timeline(project_id=3, execution_session_id=4)

    assert timeline[0]["event_type"] == "worker_progress"
    assert timeline[0]["surface"] == "cursor"
    assert timeline[0]["subtype"] == "ide_prompt_submitted"
    assert timeline[0]["correlation_id"] == "corr-1"
    assert timeline[0]["thread_id"] == "thread-1"
    assert timeline[0]["is_workflow_attached"] is False


def test_route_notification_mentions_missing_visual_baseline():
    from distr.core.orchestration_events import build_orchestration_notification

    notification = build_orchestration_notification({
        "event_type": "route_decided",
        "source": "hermes",
        "summary": "Route codex (harness_preference) for medium complexity",
        "payload": {
            "decision": {
                "backend": "codex",
                "rationale": "Policy route for medium complexity; visual baseline not ready",
            },
            "visual_baseline_readiness": {
                "ready": False,
                "missing_screen_count": 2,
            },
        },
    })

    assert notification["should_notify"] is False
    assert "visual baseline not ready" in notification["text"].lower()
    assert "2 missing reference screens" in notification["text"]
