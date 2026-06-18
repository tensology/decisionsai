from __future__ import annotations

import contextlib
import json

import distr.core.db.kanban  # noqa: F401
import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base, Chat
from distr.core.db.orchestrator import OrchestratorEvent
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


def test_emit_agent_activity_step_persists_ledger_and_chat_projection(monkeypatch):
    from distr.core.agent_activity import emit_agent_activity_step

    factory = _factory()
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        chat = Chat(title="Telegram control room")
        session.add(chat)
        session.commit()
        chat_id = chat.id

    result = emit_agent_activity_step(
        source="telegram",
        surface="chat",
        chat_id=chat_id,
        project_id=8,
        ticket_id=13,
        status="running",
        title="Checked WhatsApp",
        summary="Matched Merrypak and found Carmen's latest screenshot.",
        run_key="chat:telegram:42",
        thread_key="main",
        step_key="whatsapp-snapshot",
        step_type="context_lookup",
        payload={"group": "Merrypak"},
        evidence={"message_ids": [16354, 16355]},
    )

    assert result["event_id"]
    assert result["chat_event"]["chat_id"] == chat_id
    assert result["chat_event"]["agent_activity"]["run_key"] == "chat:telegram:42"
    assert result["chat_event"]["agent_activity"]["title"] == "Checked WhatsApp"

    with _session_ctx(factory) as session:
        row = session.query(OrchestratorEvent).filter(OrchestratorEvent.id == result["event_id"]).one()
        payload = json.loads(row.payload or "{}")
        chat = session.get(Chat, chat_id)
        params = json.loads(chat.params or "{}")

    assert row.source == "telegram"
    assert row.event_type == "worker_progress"
    assert row.project_id == 8
    assert row.ticket_id == 13
    assert payload["agent_activity"]["run_key"] == "chat:telegram:42"
    assert payload["agent_activity"]["thread_key"] == "main"
    assert payload["agent_activity"]["step_key"] == "whatsapp-snapshot"
    assert payload["agent_activity"]["step_type"] == "context_lookup"
    assert payload["agent_activity"]["owner"]["chat_id"] == chat_id
    assert params["workflow_events"][0]["agent_activity"]["run_key"] == "chat:telegram:42"
    assert params["workflow_events"][0]["type"] == "agent_activity_step"


def test_list_agent_activity_returns_chronological_thread(monkeypatch):
    from distr.core.agent_activity import emit_agent_activity_step, list_agent_activity

    factory = _factory()
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))

    emit_agent_activity_step(
        source="workflow",
        surface="workflow",
        workflow_id=3,
        run_id=4,
        project_id=5,
        status="running",
        title="Started run",
        summary="The workflow started.",
        step_key="start",
    )
    emit_agent_activity_step(
        source="codex",
        surface="workflow",
        workflow_id=3,
        run_id=4,
        project_id=5,
        status="completed",
        title="Codex finished",
        summary="Codex returned an implementation packet.",
        thread_key="codex",
        step_key="codex-complete",
    )

    activity = list_agent_activity(workflow_id=3, run_id=4)

    assert [item["agent_activity"]["title"] for item in activity] == [
        "Started run",
        "Codex finished",
    ]
    assert activity[0]["agent_activity"]["run_key"] == "workflow:3:run:4"
    assert activity[1]["agent_activity"]["thread_key"] == "codex"
    assert activity[1]["status"] == "completed"


def test_workflow_scoped_agent_activity_notifies_workflow_ui(monkeypatch):
    from distr.core.agent_activity import emit_agent_activity_step

    factory = _factory()
    calls = []
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(
        "distr.gui.web.workflow_events.increment_workflow_updated",
        lambda: calls.append("updated"),
    )

    emit_agent_activity_step(
        source="codex",
        surface="workflow",
        workflow_id=6,
        run_id=7,
        status="running",
        title="Codex started",
        summary="Codex is applying the ticket.",
    )

    assert calls == ["updated"]


def test_agent_activity_can_carry_contextual_needs_input_payload(monkeypatch):
    from distr.core.agent_activity import emit_agent_activity_step

    factory = _factory()
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))

    result = emit_agent_activity_step(
        source="codex",
        surface="workflow",
        workflow_id=10,
        run_id=20,
        step_id=30,
        project_id=40,
        status="waiting",
        title="Need a decision",
        summary="Should I use browser_use for the checkout flow?",
        step_type="needs_input",
        context={
            "project": "Player1Sport",
            "workflow": "QA Workflow",
            "step": "Checkout browser validation",
            "tools": ["browser_use"],
            "situation": "The scripted selector is unstable.",
        },
        question="Should I switch this step to browser_use?",
        spoken_text="I’m working on Player1Sport in QA Workflow. The checkout step needs input. Should I switch this step to browser_use?",
    )

    with _session_ctx(factory) as session:
        row = session.get(OrchestratorEvent, result["event_id"])
        payload = json.loads(row.payload or "{}")

    assert row.event_type == "needs_input"
    assert payload["agent_activity"]["context"]["tools"] == ["browser_use"]
    assert payload["agent_activity"]["question"] == "Should I switch this step to browser_use?"
    assert "Player1Sport" in payload["agent_activity"]["spoken_text"]
