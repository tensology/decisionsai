import contextlib
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketInput, KanbanTicketTool
from distr.core.agent.tools.step_runner.workflow_tools import RunWorkflowTool
from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket


def _memory_session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    return get_session


def test_ticket_batch_is_atomic_linked_and_retry_safe(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        board = KanbanBoard(
            name="Example Artist",
            agent_source_lane="Backlog",
            default_project_id=16,
            default_workflow_id=369,
        )
        session.add(board)
        session.flush()
        session.add(KanbanLane(board_id=board.id, name="Backlog", position=0))
        session.flush()
        board_id = board.id

    drafts = [
        {"title": "Copy backend foundation", "description": "Copy source files first. Validate imports and migrations.", "priority": "high"},
        {"title": "Validate checkout", "description": "Test Yoco sandbox checkout and failure recovery.", "priority": "high"},
    ]
    first = KanbanTicketTool()._run(action="create_ticket_batch", board_id=board_id, tickets=drafts)
    second = KanbanTicketTool()._run(action="create_ticket_batch", board_id=board_id, tickets=drafts)

    assert "created 2 ticket(s)" in first
    assert "#1 Copy backend foundation" in first
    assert "created 0 ticket(s)" in second
    assert "Skipped existing titles" in second
    with get_session() as session:
        rows = session.query(KanbanTicket).order_by(KanbanTicket.position).all()
        assert [row.title for row in rows] == ["Copy backend foundation", "Validate checkout"]
        assert all(row.linked_project_id == 16 for row in rows)
        assert all(row.linked_workflow_id == 369 for row in rows)


def test_ticket_batch_schema_requires_complete_nested_drafts():
    parsed = KanbanTicketInput.model_validate({
        "action": "create_ticket_batch",
        "tickets": [{"title": "One", "description": "Complete scope and validation."}],
    })
    assert parsed.tickets[0].title == "One"


def test_run_workflow_hands_ordered_ticket_group_to_dispatcher():
    built = [
        {"ticket_id": 177, "board_id": 12, "context": "First", "run_metadata": {}},
        {"ticket_id": 178, "board_id": 12, "context": "Second", "run_metadata": {}},
    ]
    result = {
        "group_id": "example-group",
        "started": [{"ticket_id": 177, "run_id": 900}],
        "queued_count": 1,
    }
    with patch("distr.core.workflow.workflow_resolve.resolve_workflow_id", return_value=(369, None)), \
         patch("distr.core.workflow.ticket_dispatch.build_ticket_run_item", side_effect=built) as build, \
         patch("distr.core.workflow.dispatcher.start_workflow_ticket_group", return_value=result) as start:
        message = RunWorkflowTool()._run(workflow_id=369, ticket_ids=[177, 178, 177])

    assert "first of 2 selected tickets" in message
    assert "remaining 1" in message
    assert "group_id=example-group" in message
    assert [call.args[0] for call in build.call_args_list] == [177, 178]
    assert start.call_args.args[1] == built
    assert start.call_args.kwargs["dispatch_async"] is True


def test_run_workflow_can_append_missing_tickets_to_live_group():
    built = [{"ticket_id": 178, "board_id": 12, "context": "Second", "run_metadata": {}}]
    with patch("distr.core.workflow.workflow_resolve.resolve_workflow_id", return_value=(369, None)), \
         patch("distr.core.workflow.ticket_dispatch.build_ticket_run_item", side_effect=built), \
         patch("distr.core.workflow.dispatcher.append_workflow_ticket_group", return_value={
             "appended_ticket_ids": [178], "queued_count": 14,
         }) as append:
        message = RunWorkflowTool()._run(workflow_id=369, ticket_ids=[178], append_to_run_id=104)

    assert "added 1 tickets" in message
    assert "14 tickets waiting" in message
    assert append.call_args.args == (104, built)
