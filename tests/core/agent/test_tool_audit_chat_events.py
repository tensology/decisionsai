from contextlib import contextmanager
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.agent.tool_audit import record_chat_settings_change, record_tool_execution
from distr.core.db import Base, Chat
import distr.core.db.workflow  # noqa: F401


def test_record_tool_execution_persists_chat_event(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def patched_get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr("distr.core.db.get_session", patched_get_session)
    monkeypatch.setattr(
        "distr.core.workflow.service.append_audit_step",
        lambda **kwargs: None,
    )

    with patched_get_session() as session:
        chat = Chat(title="Test chat")
        session.add(chat)
        session.commit()
        chat_id = chat.id
        chat.params = json.dumps({"active_turn_chat_row_id": chat_id})
        session.commit()

    record_tool_execution(
        chat_id,
        "clipboard_write",
        "Copied release notes to clipboard",
        "completed",
        instruction_hint="Write the Claude response to the clipboard",
        user_text="type out the response",
        routing_path="clipboard",
    )

    with patched_get_session() as session:
        chat = session.get(Chat, chat_id)
        params = json.loads(chat.params)

    events = params["tool_events"]
    assert len(events) == 1
    assert events[0]["chat_id"] == chat_id
    assert events[0]["turn_chat_id"] == chat_id
    assert events[0]["tool_name"] == "clipboard_write"
    assert events[0]["title"] == "Write the Claude response to the clipboard"
    assert events[0]["result_summary"] == "Copied release notes to clipboard"
    assert events[0]["routing_path"] == "clipboard"
    assert events[0]["chat_visible"] is True


def test_record_tool_execution_marks_internal_probe_tools_compact(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def patched_get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr("distr.core.db.get_session", patched_get_session)
    monkeypatch.setattr(
        "distr.core.workflow.service.append_audit_step",
        lambda **kwargs: None,
    )

    with patched_get_session() as session:
        chat = Chat(title="Test chat")
        session.add(chat)
        session.commit()
        chat_id = chat.id
        chat.params = json.dumps({"active_turn_chat_row_id": chat_id})
        session.commit()

    record_tool_execution(chat_id, "execute_code", "Output: (960, 540)", "completed")
    record_tool_execution(chat_id, "mode_control", "Error: Please specify action", "completed")

    with patched_get_session() as session:
        chat = session.get(Chat, chat_id)
        params = json.loads(chat.params)

    events = params["tool_events"]
    assert [event["tool_name"] for event in events] == ["execute_code", "mode_control"]
    assert all(event["chat_visible"] is True for event in events)
    assert all(event["chat_compact"] is True for event in events)
    assert events[0]["title"] == "Ran helper code"
    assert events[1]["title"] == "Checked mode control"
    assert events[1]["status"] == "failed"
    assert all(event["activity_style"] == "passive" for event in events)


def test_record_tool_execution_describes_clipboard_ingest_and_mouse_actions(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def patched_get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr("distr.core.db.get_session", patched_get_session)
    monkeypatch.setattr(
        "distr.core.workflow.service.append_audit_step",
        lambda **kwargs: None,
    )

    with patched_get_session() as session:
        chat = Chat(title="Test chat")
        session.add(chat)
        session.commit()
        chat_id = chat.id
        chat.params = json.dumps({"active_turn_chat_row_id": chat_id})
        session.commit()

    record_tool_execution(
        chat_id,
        "clipboard_action",
        "CLIPBOARD CONTENT:\n\nhello world\n\nThis is the current clipboard content.",
        "completed",
        instruction_hint="get clipboard",
    )
    record_tool_execution(
        chat_id,
        "mouse_movement",
        "Moved mouse to bottom right corner",
        "completed",
    )

    with patched_get_session() as session:
        chat = session.get(Chat, chat_id)
        params = json.loads(chat.params)

    events = params["tool_events"]
    assert events[0]["title"] == "Ingested clipboard into context"
    assert events[0]["activity_style"] == "passive"
    assert events[0]["chat_compact"] is True
    assert events[1]["title"] == "Moved mouse to bottom right corner"
    assert events[1]["activity_style"] == "active"
    assert events[1]["chat_compact"] is False


def test_record_tool_execution_does_not_create_audit_workflow(monkeypatch):
    from distr.core.db.workflow import AutoWorkflow

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def patched_get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr("distr.core.db.get_session", patched_get_session)

    with patched_get_session() as session:
        chat = Chat(title="Test chat")
        session.add(chat)
        session.commit()
        chat_id = chat.id
        chat.params = json.dumps({"active_turn_chat_row_id": chat_id})
        session.commit()

    record_tool_execution(
        chat_id,
        "developer_context",
        "Codex has one active session.",
        "completed",
        instruction_hint="Can we talk about what Codex is doing?",
    )

    with patched_get_session() as session:
        assert session.query(AutoWorkflow).count() == 0


def test_record_tool_execution_hides_events_outside_active_turn(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def patched_get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr("distr.core.db.get_session", patched_get_session)
    monkeypatch.setattr(
        "distr.core.workflow.service.append_audit_step",
        lambda **kwargs: None,
    )

    with patched_get_session() as session:
        chat = Chat(title="Test chat")
        session.add(chat)
        session.commit()
        chat_id = chat.id

    record_tool_execution(chat_id, "smart_open", "Opened application: Finder", "completed")

    with patched_get_session() as session:
        chat = session.get(Chat, chat_id)
        params = json.loads(chat.params)

    events = params["tool_events"]
    assert len(events) == 1
    assert events[0]["chat_visible"] is False


def test_record_chat_settings_change_persists_visible_activity(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextmanager
    def patched_get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr("distr.core.db.get_session", patched_get_session)
    monkeypatch.setattr(
        "distr.core.workflow.service.append_audit_step",
        lambda **kwargs: None,
    )

    with patched_get_session() as session:
        chat = Chat(
            title="Test chat",
            provider="openai",
            model_name="gpt-4o",
            voice_provider="coqui",
            voice_model="Alexa",
        )
        session.add(chat)
        session.commit()
        chat_id = chat.id

    event = record_chat_settings_change(
        chat_id,
        previous={
            "provider": "openai",
            "model_name": "gpt-4o",
            "voice_provider": "coqui",
            "voice_model": "Alexa",
        },
        current={
            "provider": "anthropic",
            "model_name": "claude-sonnet-4",
            "voice_provider": "coqui",
            "voice_model": "Alexa",
        },
    )

    assert event is not None
    assert event["tool_name"] == "chat_settings"
    assert event["chat_visible"] is True
    assert "LLM:" in event["result_summary"]
    assert "turn_chat_id" not in event

    with patched_get_session() as session:
        chat = session.get(Chat, chat_id)
        params = json.loads(chat.params)

    events = params["tool_events"]
    assert len(events) == 1
    assert events[0]["title"].startswith("LLM:")
