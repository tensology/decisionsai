from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
import pytest

from distr.core.kanban.lifecycle import (
    DELIVERY_LANES,
    ensure_delivery_lanes,
    move_ticket_to_delivery_lane,
)


def test_delivery_lifecycle_migrates_scoped_work_without_losing_tickets():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        board = KanbanBoard(name="Pizza House", source="database")
        session.add(board)
        session.flush()
        scoped = KanbanLane(board_id=board.id, name="Scoped work", position=0)
        session.add(scoped)
        session.flush()
        ticket = KanbanTicket(lane_id=scoped.id, title="Change the green button")
        session.add(ticket)
        session.flush()
        original_lane_id = scoped.id

        lanes = ensure_delivery_lanes(
            session,
            board.id,
            legacy_source_names=("Scoped work",),
        )
        ensure_delivery_lanes(session, board.id, legacy_source_names=("Scoped work",))
        session.flush()

        actual = (
            session.query(KanbanLane)
            .filter(KanbanLane.board_id == board.id)
            .order_by(KanbanLane.position, KanbanLane.id)
            .all()
        )
        assert [lane.name for lane in actual] == list(DELIVERY_LANES)
        assert lanes["Backlog"].id == original_lane_id
        assert ticket.lane_id == original_lane_id
        assert len(actual) == len(DELIVERY_LANES)
    finally:
        session.close()
        engine.dispose()


def test_automation_moves_backlog_to_in_progress_then_qa_but_never_complete():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        board = KanbanBoard(name="Delivery", source="database")
        session.add(board)
        session.flush()
        lanes = ensure_delivery_lanes(session, board.id)
        ticket = KanbanTicket(lane_id=lanes["Backlog"].id, title="Ship the player")
        session.add(ticket)
        session.flush()

        assert move_ticket_to_delivery_lane(session, ticket.id, "In Progress") is True
        assert ticket.lane_id == lanes["In Progress"].id
        assert move_ticket_to_delivery_lane(session, ticket.id, "In Progress") is False
        assert move_ticket_to_delivery_lane(session, ticket.id, "QA") is True
        assert ticket.lane_id == lanes["QA"].id
        with pytest.raises(ValueError, match="Only a human"):
            move_ticket_to_delivery_lane(session, ticket.id, "Complete")
        assert ticket.lane_id == lanes["QA"].id

        assert move_ticket_to_delivery_lane(session, ticket.id, "In Progress") is True
        assert ticket.lane_id == lanes["In Progress"].id
    finally:
        session.close()
        engine.dispose()


def test_automation_cannot_skip_in_progress_or_move_back_to_backlog():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        board = KanbanBoard(name="Delivery", source="database")
        session.add(board)
        session.flush()
        lanes = ensure_delivery_lanes(session, board.id)
        ticket = KanbanTicket(lane_id=lanes["Backlog"].id, title="Respect order")
        session.add(ticket)
        session.flush()

        with pytest.raises(ValueError, match="Backlog → In Progress → QA"):
            move_ticket_to_delivery_lane(session, ticket.id, "QA")
        assert ticket.lane_id == lanes["Backlog"].id

        move_ticket_to_delivery_lane(session, ticket.id, "In Progress")
        with pytest.raises(ValueError, match="only move tickets"):
            move_ticket_to_delivery_lane(session, ticket.id, "Backlog")
        assert ticket.lane_id == lanes["In Progress"].id
    finally:
        session.close()
        engine.dispose()
