from contextlib import contextmanager
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat, ChatTurnEvent
from distr.core.db.projects import Project
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


def test_shared_workflow_projects_activity_to_its_run_thread_and_original_turn(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("distr.core.db.get_session", factory)
    monkeypatch.setattr("distr.core.chat_turns.get_session", factory)

    with factory() as db:
        project_a, project_b = Project(name="Project A"), Project(name="Project B")
        db.add_all([project_a, project_b])
        db.flush()
        template_chat, run_chat = Chat(title="Original workflow chat", project_id=project_a.id), Chat(
            title="Current workflow chat", project_id=project_b.id,
        )
        db.add_all([template_chat, run_chat])
        db.flush()
        initial = Chat(parent_id=run_chat.id, input="Launch the approved workflow")
        later = Chat(parent_id=run_chat.id, input="A later question")
        db.add_all([initial, later])
        db.flush()
        run_chat.params = json.dumps({"active_turn_chat_row_id": later.id})
        workflow = AutoWorkflow(name="Shared template", chat_id=template_chat.id)
        db.add(workflow)
        db.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, chat_id=run_chat.id, run_data=json.dumps({
            "chat_id": run_chat.id, "project_id": project_b.id, "development_turn_id": initial.id,
        }))
        db.add(run)
        db.flush()
        ids = run.id, template_chat.id, run_chat.id, initial.id, later.id
        db.commit()

    run_id, template_id, chat_id, turn_id, newer_turn_id = ids
    for event, status in [("step_started", "running"), ("step_completed", "passed")]:
        payload = record_workflow_chat_event(run_id, event, status=status, step_id=42)
        assert payload is not None and payload["chat_id"] == chat_id
        assert payload["turn_id"] == turn_id
    with factory() as db:
        from distr.gui.web.routes.chat import _merge_thread_rows_with_tool_and_workflow_events

        assert not db.get(Chat, template_id).params
        root = db.get(Chat, chat_id)
        assert json.loads(root.params)["active_turn_chat_row_id"] == newer_turn_id
        messages = _merge_thread_rows_with_tool_and_workflow_events(
            [db.get(Chat, turn_id), db.get(Chat, newer_turn_id)], root,
        )
        assert [message["role"] for message in messages] == ["user", "workflow", "workflow", "user"]
        assert all(message["turn_chat_id"] == turn_id for message in messages[1:3])
        rows = db.query(ChatTurnEvent).filter(ChatTurnEvent.event_type == "tool_completed").all()
        assert [(row.chat_id, row.turn_id) for row in rows] == [(chat_id, turn_id)]
        run = db.get(AutoWorkflowRun, run_id)
        run.run_data = json.dumps({"chat_id": chat_id, "development_turn_id": None})
        db.commit()
    assert "turn_id" not in record_workflow_chat_event(run_id, "step_started", step_id=43)
    with factory() as db:
        assert db.query(ChatTurnEvent).filter_by(turn_id=newer_turn_id).count() == 0
        db.get(AutoWorkflowRun, run_id).run_data = json.dumps({"chat_id": template_id})
        db.commit()
    assert record_workflow_chat_event(run_id, "step_started", step_id=44) is None


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


def test_record_chat_workflow_event_preserves_agent_activity(monkeypatch):
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
        chat = Chat(title="Agent activity")
        session.add(chat)
        session.commit()
        chat_id = chat.id

    event = record_chat_workflow_event(
        chat_id,
        "agent_activity_step",
        status="running",
        summary="Checked WhatsApp.",
        agent_activity={
            "run_key": "chat:1",
            "thread_key": "main",
            "step_key": "whatsapp",
            "title": "Checked WhatsApp",
        },
    )

    assert event["agent_activity"]["run_key"] == "chat:1"

    with patched_get_session() as session:
        chat = session.get(Chat, chat_id)
        params = json.loads(chat.params)

    assert params["workflow_events"][0]["agent_activity"]["title"] == "Checked WhatsApp"


def test_workflow_completion_notice_is_hidden_from_ordinary_chat(monkeypatch):
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

    notices = []
    monkeypatch.setattr("distr.core.db.get_session", patched_get_session)
    monkeypatch.setattr(
        "distr.core.chat.ChatService.append_assistant_notice",
        lambda chat_id, text, hidden=False: notices.append((chat_id, text, hidden)),
    )

    with patched_get_session() as session:
        chat = Chat(title="Ordinary chat")
        session.add(chat)
        session.commit()
        chat_id = chat.id

    record_chat_workflow_event(chat_id, "run_completed", status="completed")

    assert notices == [
        (chat_id, "The workflow completed. The ticket now contains the recorded result and audit evidence.", True)
    ]
