from __future__ import annotations

import contextlib
import json

import distr.core.db.hermes  # noqa: F401
from distr.core.db import Base, Chat
from distr.core.db.hermes import HermesEvent, HermesMachineActivity, HermesUserMemory
from fastapi import FastAPI
from fastapi.testclient import TestClient
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


def test_user_memory_upserts_duplicates_and_emits_memory_event(monkeypatch):
    from distr.core.hermes_memory import list_user_memories, record_user_memory

    factory = _factory()
    monkeypatch.setattr("distr.core.hermes_memory.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))

    first_id = record_user_memory(
        "Prefers voice notes over Telegram text.",
        category="communication_preference",
        source_type="chat",
        source_id="chat-1",
        source_chat_id=1,
        tags=["voice", "telegram"],
    )
    second_id = record_user_memory(
        "  prefers voice notes over telegram text  ",
        category="communication_preference",
        source_type="telegram",
        source_id="telegram-1",
        source_chat_id=1,
        tags=["telegram"],
    )

    with _session_ctx(factory) as session:
        memories = session.query(HermesUserMemory).all()
        events = session.query(HermesEvent).filter(HermesEvent.event_type == "memory_written").all()

    assert first_id == second_id
    assert len(memories) == 1
    assert memories[0].category == "communication_preference"
    assert memories[0].evidence_count == 2
    assert memories[0].visibility == "private"
    assert set(json.loads(memories[0].tags)) == {"voice", "telegram"}
    assert len(events) >= 1
    assert "voice notes" in list_user_memories()[0]["content"].lower()


def test_chat_memory_extraction_survives_chat_clear(monkeypatch):
    from distr.core.chat import ChatService
    from distr.core.chat_manager import ChatManagerCore

    factory = _factory()
    monkeypatch.setattr("distr.core.chat.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.chat_manager.get_session", lambda: factory())
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes_memory.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        root = Chat(parent_id=None, title="Private chat", provider="Ollama", model_name="m")
        session.add(root)
        session.flush()
        chat_id = root.id

    ChatService.add_user_message(chat_id, "Please remember that I prefer voice notes over text chats.")

    manager = ChatManagerCore()
    assert manager.clear_chat_messages(chat_id) is True

    with _session_ctx(factory) as session:
        transcript_events = session.query(HermesEvent).filter(HermesEvent.source == "chat").all()
        memories = session.query(HermesUserMemory).all()

    assert transcript_events == []
    assert len(memories) == 1
    assert memories[0].source_chat_id == chat_id
    assert memories[0].category == "communication_preference"
    assert "voice notes" in memories[0].content.lower()


def test_memory_extraction_records_idle_voice_note_guardrail():
    from distr.core.hermes_memory import extract_memory_candidates_from_text

    candidates = extract_memory_candidates_from_text(
        "Stop sending idle voice notes when Cursor has been quiet for more than 20 minutes."
    )

    assert any(
        item["category"] == "engagement_guardrail"
        and "stale-session notifications" in item["content"]
        and "voice notes" in item["content"]
        for item in candidates
    )


def test_machine_activity_records_quiet_local_context(monkeypatch):
    from distr.core.hermes_memory import list_machine_activity, record_machine_activity

    factory = _factory()
    monkeypatch.setattr("distr.core.hermes_memory.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.notification_routing.record_surface_activity", lambda *a, **k: None)

    activity_id = record_machine_activity(
        surface="desktop",
        app_name="Cursor",
        window_title="DecisionsAI - hermes_memory.py",
        workspace_path="/Users/example/DECISIONS/DecisionsAI",
        project_id=42,
        metadata={"url": "https://example.test/?token=super-secret-token"},
        at=123.0,
    )
    duplicate_id = record_machine_activity(
        surface="desktop",
        app_name="Cursor",
        window_title="DecisionsAI - hermes_memory.py",
        workspace_path="/Users/example/DECISIONS/DecisionsAI",
        project_id=42,
        metadata={"url": "https://example.test/?token=super-secret-token"},
        at=124.0,
    )

    with _session_ctx(factory) as session:
        rows = session.query(HermesMachineActivity).all()
        notification_events = session.query(HermesEvent).filter(HermesEvent.source == "notification").all()

    assert activity_id == duplicate_id
    assert len(rows) == 1
    assert rows[0].evidence_count == 2
    assert rows[0].surface == "desktop"
    assert "[redacted]" in rows[0].metadata_json
    assert notification_events == []
    assert list_machine_activity(limit=1)[0]["app_name"] == "Cursor"


def test_external_agent_context_records_codex_and_cursor_activity(monkeypatch):
    from distr.core.external_agent_context import record_external_agent_context_activity
    from distr.core.hermes_memory import list_machine_activity

    factory = _factory()
    monkeypatch.setattr("distr.core.hermes_memory.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.notification_routing.record_surface_activity", lambda *a, **k: None)

    count = record_external_agent_context_activity({
        "codex_threads": [
            {
                "id": "codex-thread-1",
                "cwd": "/Users/example/DECISIONS/DecisionsAI",
                "title": "Implement Hermes memory",
                "updated_at": "2026-06-05T09:00:00+00:00",
            }
        ],
        "cursor_workspaces": [
            {
                "folder": "/Users/example/DECISIONS/DecisionsAI",
                "updated_at": "2026-06-05T09:05:00+00:00",
            }
        ],
    })

    activities = list_machine_activity(limit=10)

    assert count == 2
    assert {item["surface"] for item in activities} == {"codex", "cursor"}
    assert any("Hermes memory" in item["summary"] for item in activities)
    assert any(item["workspace_path"].endswith("DecisionsAI") for item in activities)


def test_machine_activity_compaction_summarizes_old_detail_without_notifications(monkeypatch):
    from distr.core.hermes_memory import compact_machine_activity, list_machine_activity, record_machine_activity

    factory = _factory()
    monkeypatch.setattr("distr.core.hermes_memory.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.notification_routing.record_surface_activity", lambda *a, **k: None)

    for index in range(3):
        record_machine_activity(
            surface="desktop",
            app_name="Cursor",
            window_title=f"Old Decisions task {index}",
            workspace_path="/Users/example/DECISIONS/DecisionsAI",
            metadata={"visible_text": f"Working note {index}"},
            at=100.0 + index,
        )
    record_machine_activity(
        surface="desktop",
        app_name="Cursor",
        window_title="Recent Decisions task",
        workspace_path="/Users/example/DECISIONS/DecisionsAI",
        metadata={"visible_text": "Fresh work"},
        at=10_000.0,
    )

    result = compact_machine_activity(now=10_000.0, older_than_s=3_600)
    activities = list_machine_activity(limit=10)

    with _session_ctx(factory) as session:
        notification_events = session.query(HermesEvent).filter(HermesEvent.source == "notification").all()

    assert result["compacted_rows"] == 3
    assert result["summary_rows"] == 1
    assert len(activities) == 2
    assert any(item["compacted"] and item["evidence_count"] == 3 for item in activities)
    assert any(not item["compacted"] and item["window_title"] == "Recent Decisions task" for item in activities)
    assert notification_events == []


def test_weekly_machine_activity_compaction_runs_once_per_week(monkeypatch):
    from distr.core.hermes_memory import record_machine_activity, run_weekly_machine_activity_compaction

    factory = _factory()
    monkeypatch.setattr("distr.core.hermes_memory.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.notification_routing.record_surface_activity", lambda *a, **k: None)

    record_machine_activity(
        surface="codex",
        app_name="Codex",
        window_title="Old implementation thread",
        workspace_path="/Users/example/DECISIONS/DecisionsAI",
        at=100.0,
    )

    first = run_weekly_machine_activity_compaction(now=10_000.0, older_than_s=3_600)
    second = run_weekly_machine_activity_compaction(now=10_100.0, older_than_s=3_600)
    third = run_weekly_machine_activity_compaction(now=10_000.0 + (8 * 24 * 60 * 60), older_than_s=3_600)

    assert first["ran"] is True
    assert second["ran"] is False
    assert third["ran"] is True


def test_hermes_memory_routes_expose_memories_and_activity(monkeypatch):
    from distr.core.hermes_memory import record_machine_activity
    from distr.gui.web.routes.hermes_memory import create_routes

    factory = _factory()
    monkeypatch.setattr("distr.core.hermes_memory.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.notification_routing.record_surface_activity", lambda *a, **k: None)

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    created = client.post(
        "/api/hermes/memories",
        json={"content": "Prefers concise voice notes.", "category": "communication_preference", "tags": ["voice"]},
    )
    assert created.status_code == 200
    assert created.json()["success"] is True

    record_machine_activity(
        surface="codex",
        app_name="Codex",
        window_title="Hermes route test",
        workspace_path="/Users/example/DECISIONS/DecisionsAI",
    )

    memories = client.get("/api/hermes/memories").json()
    activity = client.get("/api/hermes/activity").json()

    assert any("concise voice notes" in item["content"].lower() for item in memories["memories"])
    assert any(item["surface"] == "codex" for item in activity["activity"])
