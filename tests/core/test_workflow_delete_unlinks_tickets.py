"""Deleting a workflow must unlink queued board tickets."""

import contextlib
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.automation import Automation
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorValidationRecord
from distr.core.db.workflow import AutoWorkflow, DevelopmentWorkItem
from distr.core.workflow.service import delete_workflow


def _make_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


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


def _patch_service_session(factory):
    return patch(
        "distr.core.workflow.service.get_session",
        lambda: _session_ctx(factory),
    )


def test_delete_workflow_unlinks_linked_tickets():
    factory = _make_session_factory()
    session = factory()
    board = KanbanBoard(name="Board")
    session.add(board)
    session.flush()
    lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
    session.add(lane)
    session.flush()
    workflow = AutoWorkflow(name="Flow", workflow_type="manual")
    session.add(workflow)
    session.flush()
    ticket = KanbanTicket(
        title="Queued",
        lane_id=lane.id,
        linked_workflow_id=workflow.id,
        workflow_queue_position=2,
    )
    session.add(ticket)
    session.commit()
    workflow_id = workflow.id
    ticket_id = ticket.id
    session.close()

    with _patch_service_session(factory):
        assert delete_workflow(workflow_id) is True

    session = factory()
    assert session.query(AutoWorkflow).count() == 0
    row = session.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
    assert row is not None
    assert row.linked_workflow_id is None
    assert row.workflow_queue_position == 0
    session.close()


def test_delete_workflow_removes_scoped_orchestrator_evidence():
    factory = _make_session_factory()
    session = factory()
    workflow = AutoWorkflow(name="Disposable flow", workflow_type="manual")
    session.add(workflow)
    session.flush()
    workflow_id = workflow.id
    session.add(
        OrchestratorEvent(
            event_uid="delete-workflow-event",
            source="workflow",
            event_type="workflow_step_completed",
            workflow_id=workflow_id,
        )
    )
    session.add(
        OrchestratorValidationRecord(
            workflow_id=workflow_id,
            validation_type="browser_ui",
            verdict="pass",
        )
    )
    session.commit()
    session.close()

    with _patch_service_session(factory):
        assert delete_workflow(workflow_id) is True

    session = factory()
    assert session.query(OrchestratorEvent).filter_by(workflow_id=workflow_id).count() == 0
    assert session.query(OrchestratorValidationRecord).filter_by(workflow_id=workflow_id).count() == 0
    session.close()


def test_delete_workflow_detaches_board_thread_and_automation_consumers():
    factory = _make_session_factory()
    session = factory()
    workflow = AutoWorkflow(name="Reusable flow", workflow_type="manual")
    chat = Chat(title="Persistent automation thread")
    session.add_all([workflow, chat])
    session.flush()
    board = KanbanBoard(name="Board", default_workflow_id=workflow.id)
    automation = Automation(
        name="Persistent automation",
        linked_workflow_id=workflow.id,
        thread_chat_id=chat.id,
        action_config=f'{{"development_workflow_id": {workflow.id}}}',
    )
    work_item = DevelopmentWorkItem(
        chat_id=chat.id,
        identity_key="source:automation:auto_test",
        source_type="automation",
        workflow_id=workflow.id,
    )
    session.add_all([board, automation, work_item])
    session.commit()
    workflow_id = int(workflow.id)
    chat_id = int(chat.id)
    automation_id = int(automation.id)
    work_item_id = int(work_item.id)
    board_id = int(board.id)
    session.close()

    with _patch_service_session(factory):
        assert delete_workflow(workflow_id) is True

    session = factory()
    assert session.get(Chat, chat_id) is not None
    assert session.get(DevelopmentWorkItem, work_item_id).workflow_id is None
    stored_automation = session.get(Automation, automation_id)
    assert stored_automation is not None
    assert stored_automation.linked_workflow_id is None
    assert "development_workflow_id" not in stored_automation.action_config
    assert session.get(KanbanBoard, board_id).default_workflow_id is None
    session.close()
