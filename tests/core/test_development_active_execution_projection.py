import contextlib
import json
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, DevelopmentWorkItem
from distr.core.workflow import service
from distr.core.workflow import development_harness, dispatcher
from distr.core.project_cli_backends import registry


@contextlib.contextmanager
def _session(factory):
    db = factory()
    try:
        yield db
    finally:
        db.close()


def test_active_execution_projection_merges_workflow_and_direct_development_runs(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(service, "get_session", lambda: _session(factory))

    with factory() as db:
        project = Project(name="Decisions", folder_location=str(tmp_path), coding_backend="codex")
        board = KanbanBoard(name="Development")
        db.add_all([project, board])
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Doing")
        db.add(lane)
        db.flush()
        direct_ticket = KanbanTicket(title="Direct fix", lane_id=lane.id, linked_project_id=project.id)
        workflow_ticket = KanbanTicket(title="Workflow fix", lane_id=lane.id, linked_project_id=project.id)
        workflow = AutoWorkflow(name="Development", workflow_type="manual")
        db.add_all([direct_ticket, workflow_ticket, workflow])
        db.flush()
        workflow_run = AutoWorkflowRun(
            workflow_id=workflow.id,
            ticket_id=workflow_ticket.id,
            board_id=board.id,
            status="running",
            run_data=json.dumps({"project_id": project.id}),
        )
        chat = Chat(
            title="Direct fix",
            project_id=project.id,
            params=json.dumps({"development": {"execution": {
                "status": "waiting", "job_id": "job-1", "started_at": "2026-09-02T08:00:00"
            }}}),
        )
        db.add_all([workflow_run, chat])
        db.flush()
        db.add(DevelopmentWorkItem(
            chat_id=chat.id,
            identity_key="decisions:ticket:direct",
            source_type="ticket",
            board_provider="decisions",
            board_key=str(board.id),
            local_ticket_id=direct_ticket.id,
            project_id=project.id,
            ticket_title=direct_ticket.title,
        ))
        db.commit()

    rows = service.get_active_runs(limit=20)

    assert {row["execution_kind"] for row in rows} == {"workflow", "development"}
    direct = next(row for row in rows if row["execution_kind"] == "development")
    assert direct["status"] == "waiting"
    assert direct["ticket_title"] == "Direct fix"
    assert direct["board_name"] == "Development"
    assert direct["project_name"] == "Decisions"
    assert direct["cancellation_target"] == {
        "kind": "development",
        "url": f"/api/workflows/studio/tasks/{direct['chat_id']}/execution/stop",
    }
    assert direct["open_url"] == f"/development/threads/{direct['chat_id']}/"


def test_active_execution_projection_globally_sorts_before_limit(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(service, "get_session", lambda: _session(factory))

    with factory() as db:
        workflow = AutoWorkflow(name="Older workflow", workflow_type="manual")
        chat = Chat(
            title="New direct run",
            modified_date=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
            params=json.dumps({"development": {"execution": {
                "status": "running", "job_id": "new", "started_at": "2026-09-02T12:00:00+00:00"
            }}}),
        )
        db.add_all([workflow, chat])
        db.flush()
        db.add(AutoWorkflowRun(
            workflow_id=workflow.id,
            status="running",
            started_at=datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
            run_data="{}",
        ))
        db.add(DevelopmentWorkItem(chat_id=chat.id, identity_key="prompt:new"))
        db.commit()

    rows = service.get_active_runs(limit=1)

    assert len(rows) == 1
    assert rows[0]["execution_kind"] == "development"


class _FakeProcess:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


def test_development_process_cancellation_is_scoped_to_chat():
    first = _FakeProcess()
    second = _FakeProcess()
    registry._ONE_SHOT_PROCESSES.clear()
    registry._register_oneshot_process(
        7, "codex", first, board_id=3, execution_id=101, execution_kind="development"
    )
    registry._register_oneshot_process(
        7, "codex", second, board_id=3, execution_id=101, execution_kind="workflow"
    )

    assert registry.terminate_backend_process(
        7, "codex", board_id=3, execution_id=101, execution_kind="development"
    ) is True
    assert first.terminated is True
    assert second.terminated is False
    registry._ONE_SHOT_PROCESSES.clear()


def test_active_execution_projection_deduplicates_workflow_owned_chat(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(service, "get_session", lambda: _session(factory))

    with factory() as db:
        workflow = AutoWorkflow(name="Development", workflow_type="manual")
        chat = Chat(title="Owned", params=json.dumps({"development": {"execution": {"status": "running"}}}))
        db.add_all([workflow, chat])
        db.flush()
        db.add(AutoWorkflowRun(workflow_id=workflow.id, chat_id=chat.id, status="running", run_data="{}"))
        db.add(DevelopmentWorkItem(chat_id=chat.id, identity_key="prompt:owned", workflow_id=workflow.id))
        db.commit()

    rows = service.get_active_runs(limit=20)

    assert len(rows) == 1
    assert rows[0]["execution_kind"] == "workflow"


def test_direct_execution_updates_reconcile_linked_ticket_status(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(development_harness, "get_session", lambda: _session(factory))
    monkeypatch.setattr(development_harness, "_notify", lambda: None)
    with factory() as db:
        board = KanbanBoard(name="Development")
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Doing")
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(title="Waiting fix", lane_id=lane.id)
        chat = Chat(title="Waiting fix", params="{}")
        db.add_all([ticket, chat])
        db.flush()
        db.add(DevelopmentWorkItem(
            chat_id=chat.id,
            identity_key="ticket:waiting",
            local_ticket_id=ticket.id,
        ))
        db.commit()
        chat_id, ticket_id = chat.id, ticket.id

    development_harness._update_execution(chat_id, status="waiting", job_id="job-2")

    with factory() as db:
        assert db.get(KanbanTicket, ticket_id).workflow_status == "waiting"


def test_workflow_cancel_reconciles_linked_ticket_status(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(dispatcher, "get_session", lambda: _session(factory))
    monkeypatch.setattr(dispatcher, "increment_workflow_updated", lambda: None)
    monkeypatch.setattr(dispatcher, "record_workflow_chat_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatcher, "_finalize_terminal_run", lambda *args, **kwargs: None)
    with factory() as db:
        board = KanbanBoard(name="Development")
        workflow = AutoWorkflow(name="Development", workflow_type="manual")
        db.add_all([board, workflow])
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Doing")
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(title="Cancel fix", lane_id=lane.id, workflow_status="running")
        db.add(ticket)
        db.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, ticket_id=ticket.id, status="running")
        db.add(run)
        db.commit()
        run_id, ticket_id = run.id, ticket.id

    assert dispatcher.cancel_run(run_id) is True
    with factory() as db:
        assert db.get(KanbanTicket, ticket_id).workflow_status == "cancelled"


def test_workflow_cancel_rejects_run_from_different_workflow(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(dispatcher, "get_session", lambda: _session(factory))

    with factory() as db:
        expected_workflow = AutoWorkflow(name="Expected", workflow_type="manual")
        actual_workflow = AutoWorkflow(name="Actual", workflow_type="manual")
        db.add_all([expected_workflow, actual_workflow])
        db.flush()
        run = AutoWorkflowRun(workflow_id=actual_workflow.id, status="running")
        db.add(run)
        db.commit()
        expected_workflow_id = expected_workflow.id
        run_id = run.id

    assert dispatcher.cancel_run(run_id, workflow_id=expected_workflow_id) is False
    with factory() as db:
        assert db.get(AutoWorkflowRun, run_id).status == "running"
