from contextlib import contextmanager
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.agent.tool_audit import record_tool_execution
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

    record_tool_execution(
        chat_id,
        "developer_context",
        "Codex has one active session.",
        "completed",
        instruction_hint="Can we talk about what Codex is doing?",
    )

    with patched_get_session() as session:
        assert session.query(AutoWorkflow).count() == 0
