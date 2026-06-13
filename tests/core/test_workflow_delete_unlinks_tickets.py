"""Deleting a workflow must unlink queued board tickets."""

import contextlib
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import AutoWorkflow
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
