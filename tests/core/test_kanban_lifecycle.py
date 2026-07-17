from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.kanban.lifecycle import DELIVERY_LANES, ensure_delivery_lanes


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
