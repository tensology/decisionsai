from contextlib import contextmanager
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.workflow.chat_trace import record_chat_workflow_event, record_workflow_chat_event


def test_record_workflow_chat_event_persists_to_owning_chat(monkeypatch):
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
        chat = Chat(title="Workflow owner")
        session.add(chat)
        session.flush()
        workflow = AutoWorkflow(name="Ship feature", status="active", chat_id=chat.id)
        session.add(workflow)
        session.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, status="running")
        session.add(run)
        session.commit()
        chat_id = chat.id
        run_id = run.id

    event = record_workflow_chat_event(
        run_id,
        "step_started",
        status="running",
        step_id=42,
        step_name="Validate result",
        summary="Started validation.",
        phase="validation",
    )

    assert event is not None
    assert event["chat_id"] == chat_id
    assert event["workflow_name"] == "Ship feature"
    assert event["step_name"] == "Validate result"

    with patched_get_session() as session:
        chat = session.get(Chat, chat_id)
        params = json.loads(chat.params)

    events = params["workflow_events"]
    assert len(events) == 1
    assert events[0]["run_id"] == run_id
    assert events[0]["type"] == "step_started"
    assert events[0]["phase"] == "validation"


def test_record_chat_workflow_event_persists_explicit_chat(monkeypatch):
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
        chat = Chat(title="Automation owner")
        session.add(chat)
        session.commit()
        chat_id = chat.id

    event = record_chat_workflow_event(
        chat_id,
        "automation_run",
        status="running",
        workflow_id=12,
        workflow_name="Daily Plan",
        run_id=99,
        summary="Summarize my inbox.",
    )

    assert event is not None
    assert event["chat_id"] == chat_id
    assert event["type"] == "automation_run"
    assert event["workflow_name"] == "Daily Plan"
    assert event["summary"] == "Summarize my inbox."

    with patched_get_session() as session:
        chat = session.get(Chat, chat_id)
        params = json.loads(chat.params)

    assert params["workflow_events"][0]["run_id"] == 99
