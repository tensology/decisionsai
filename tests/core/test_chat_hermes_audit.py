from __future__ import annotations

import contextlib
import json

import distr.core.db.hermes  # noqa: F401
from distr.core.db import Base, Chat
from distr.core.db.hermes import HermesEvent, HermesLearnedRule
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


def test_chat_service_user_message_records_hermes_audit_event(monkeypatch):
    from distr.core.chat import ChatService

    factory = _factory()
    monkeypatch.setattr("distr.core.chat.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        root = Chat(parent_id=None, title="Project chat", provider="Ollama", model_name="m")
        session.add(root)
        session.flush()
        chat_id = root.id

    ChatService.add_user_message(chat_id, "Please tighten the Hermes chat bridge.")

    with _session_ctx(factory) as session:
        events = session.query(HermesEvent).all()
        assert len(events) == 1
        event = events[0]
        payload = json.loads(event.payload)

    assert event.source == "chat"
    assert event.event_type == "worker_progress"
    assert payload["orchestration"]["surface"] == "chat"
    assert payload["orchestration"]["subtype"] == "chat_user_message"
    assert payload["orchestration"]["thread_id"] == str(chat_id)
    assert payload["role"] == "user"
    assert payload["chat_id"] == chat_id
    assert payload["content_hash"]
    assert "Hermes chat bridge" in payload["content_preview"]


def test_chat_service_starting_question_records_hermes_audit_event(monkeypatch):
    from distr.core.chat import ChatService

    factory = _factory()
    monkeypatch.setattr("distr.core.chat.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))

    chat_id, first_message = ChatService.create_new_chat(
        llm_provider="Ollama",
        llm_model="m",
        title="Start",
        starting_question="Can Hermes see the first chat turn?",
    )

    with _session_ctx(factory) as session:
        events = session.query(HermesEvent).all()
        assert len(events) == 1
        event = events[0]
        payload = json.loads(event.payload)

    assert first_message == "Can Hermes see the first chat turn?"
    assert payload["orchestration"]["subtype"] == "chat_user_message"
    assert payload["chat_id"] == chat_id
    assert payload["role"] == "user"
    assert "first chat turn" in payload["content_preview"]


def test_chat_manager_assistant_message_records_hermes_audit_event(monkeypatch):
    from distr.core.chat_manager import ChatManagerCore

    factory = _factory()
    monkeypatch.setattr("distr.core.chat_manager.get_session", lambda: factory())
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        root = Chat(parent_id=None, title="Project chat", provider="Ollama", model_name="m")
        child = Chat(parent_id=1, input="What changed?", response="")
        session.add(root)
        session.flush()
        child.parent_id = root.id
        session.add(child)
        session.flush()
        chat_id = root.id
        child_id = child.id

    manager = ChatManagerCore()
    manager.chat_histories[chat_id] = [{"role": "user", "content": "What changed?"}]
    manager.add_assistant_message(chat_id, "I tightened the chat audit path.")

    with _session_ctx(factory) as session:
        events = session.query(HermesEvent).all()
        assert len(events) == 1
        event = events[0]
        payload = json.loads(event.payload)

    assert event.source == "chat"
    assert payload["orchestration"]["subtype"] == "chat_assistant_message"
    assert payload["chat_id"] == chat_id
    assert payload["chat_row_id"] == child_id
    assert payload["role"] == "assistant"
    assert "chat audit path" in payload["content_preview"]


def test_removing_chat_transcript_audit_events_preserves_hermes_memory(monkeypatch):
    from distr.core.chat import ChatService, remove_chat_transcript_audit_events

    factory = _factory()
    monkeypatch.setattr("distr.core.chat.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        root = Chat(parent_id=None, title="Private chat", provider="Ollama", model_name="m")
        session.add(root)
        session.flush()
        chat_id = root.id

    ChatService.add_user_message(chat_id, "Remember this only while the chat exists.")

    with _session_ctx(factory) as session:
        session.add(
            HermesLearnedRule(
                scope="global",
                rule_type="user_preference",
                summary="Prefers voice-first concise updates",
                payload=json.dumps({"kind": "style_preference"}),
                confidence=0.9,
                evidence_count=2,
                enabled=1,
            )
        )
        session.add(
            HermesEvent(
                event_uid="memory-event-1",
                source="hermes_learning",
                event_type="memory_written",
                status="recorded",
                summary="Reusable preference captured",
                payload=json.dumps({"chat_id": chat_id, "thread_id": str(chat_id)}),
            )
        )
        session.add(
            HermesEvent(
                event_uid="chat-memory-event-1",
                source="chat",
                event_type="memory_written",
                status="recorded",
                summary="Reusable chat preference captured",
                payload=json.dumps(
                    {
                        "chat_id": chat_id,
                        "thread_id": str(chat_id),
                        "subtype": "chat_preference_saved",
                        "orchestration": {
                            "thread_id": str(chat_id),
                            "subtype": "chat_preference_saved",
                        },
                    }
                ),
            )
        )

    with _session_ctx(factory) as session:
        assert session.query(HermesEvent).filter(HermesEvent.source == "chat").count() == 2
        assert session.query(HermesEvent).filter(HermesEvent.source == "hermes_learning").count() == 1
        assert session.query(HermesLearnedRule).count() == 1

    deleted = remove_chat_transcript_audit_events(chat_id)

    with _session_ctx(factory) as session:
        assert deleted == 1
        assert session.query(HermesEvent).filter(HermesEvent.source == "chat").count() == 1
        assert session.query(HermesEvent).filter(HermesEvent.source == "hermes_learning").count() == 1
        assert session.query(HermesLearnedRule).count() == 1


def test_chat_manager_clear_removes_transcript_audit_events_only(monkeypatch):
    from distr.core.chat import ChatService
    from distr.core.chat_manager import ChatManagerCore

    factory = _factory()
    monkeypatch.setattr("distr.core.chat.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.hermes.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.chat_manager.get_session", lambda: factory())

    with _session_ctx(factory) as session:
        root = Chat(parent_id=None, title="Private chat", provider="Ollama", model_name="m")
        session.add(root)
        session.flush()
        chat_id = root.id

    ChatService.add_user_message(chat_id, "Please remove this transcript later.")

    with _session_ctx(factory) as session:
        session.add(
            HermesEvent(
                event_uid="manager-clear-chat-memory-event-1",
                source="chat",
                event_type="memory_written",
                status="recorded",
                summary="Reusable chat preference captured",
                payload=json.dumps(
                    {
                        "chat_id": chat_id,
                        "thread_id": str(chat_id),
                        "subtype": "chat_preference_saved",
                        "orchestration": {
                            "thread_id": str(chat_id),
                            "subtype": "chat_preference_saved",
                        },
                    }
                ),
            )
        )

    manager = ChatManagerCore()
    assert manager.clear_chat_messages(chat_id) is True

    with _session_ctx(factory) as session:
        assert session.get(Chat, chat_id) is not None
        assert session.query(Chat).filter(Chat.parent_id == chat_id).count() == 0
        assert session.query(HermesEvent).filter(HermesEvent.source == "chat").count() == 1
        remaining = session.query(HermesEvent).filter(HermesEvent.source == "chat").one()
        payload = json.loads(remaining.payload)

    assert payload["subtype"] == "chat_preference_saved"
