"""clear_ticket_workflow_links management helper."""

import contextlib
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import AutoWorkflow
from distr.core.workflow.service import clear_ticket_workflow_links


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


def _seed_ticket(session, *, linked_workflow_id=None, queue_pos=0, status=None):
    board = KanbanBoard(name="Board")
    session.add(board)
    session.flush()
    lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
    session.add(lane)
    session.flush()
    ticket = KanbanTicket(
        title="Ticket",
        lane_id=lane.id,
        linked_workflow_id=linked_workflow_id,
        workflow_queue_position=queue_pos,
        workflow_status=status,
    )
    session.add(ticket)
    session.flush()
    return ticket


def test_clear_all_ticket_workflow_links():
    factory = _make_session_factory()
    session = factory()
    workflow = AutoWorkflow(name="Flow", workflow_type="manual")
    session.add(workflow)
    session.flush()
    linked = _seed_ticket(session, linked_workflow_id=workflow.id, queue_pos=3, status="running")
    stale_queue = _seed_ticket(session, linked_workflow_id=None, queue_pos=2)
    clean = _seed_ticket(session)
    session.commit()
    linked_id = linked.id
    stale_id = stale_queue.id
    clean_id = clean.id
    session.close()

    with _patch_service_session(factory):
        result = clear_ticket_workflow_links(dry_run=False)

    assert result["updated"] == 2
    session = factory()
    linked_row = session.query(KanbanTicket).filter(KanbanTicket.id == linked_id).first()
    stale_row = session.query(KanbanTicket).filter(KanbanTicket.id == stale_id).first()
    clean_row = session.query(KanbanTicket).filter(KanbanTicket.id == clean_id).first()
    assert linked_row.linked_workflow_id is None
    assert linked_row.workflow_queue_position == 0
    assert linked_row.workflow_status is None
    assert stale_row.workflow_queue_position == 0
    assert clean_row.linked_workflow_id is None
    session.close()


def test_clear_orphaned_ticket_workflow_links_only():
    factory = _make_session_factory()
    session = factory()
    workflow = AutoWorkflow(name="Flow", workflow_type="manual")
    session.add(workflow)
    session.flush()
    valid = _seed_ticket(session, linked_workflow_id=workflow.id, queue_pos=1)
    orphan = _seed_ticket(session, linked_workflow_id=9999, queue_pos=4, status="failed")
    session.commit()
    valid_id = valid.id
    orphan_id = orphan.id
    workflow_id = workflow.id
    session.close()

    with _patch_service_session(factory):
        result = clear_ticket_workflow_links(orphaned_only=True, dry_run=False)

    assert result["updated"] == 1
    session = factory()
    valid_row = session.query(KanbanTicket).filter(KanbanTicket.id == valid_id).first()
    orphan_row = session.query(KanbanTicket).filter(KanbanTicket.id == orphan_id).first()
    assert valid_row.linked_workflow_id == workflow_id
    assert orphan_row.linked_workflow_id is None
    assert orphan_row.workflow_queue_position == 0
    session.close()
