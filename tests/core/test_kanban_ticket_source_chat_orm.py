"""ORM smoke: KanbanTicket.source_chat_id persists (migration + model alignment)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket


def test_ticket_source_chat_id_roundtrip_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    board = KanbanBoard(name="B")
    s.add(board)
    s.flush()
    lane = KanbanLane(board_id=board.id, name="Lane", position=0)
    s.add(lane)
    s.flush()
    ticket = KanbanTicket(
        lane_id=lane.id,
        title="T",
        description="",
        priority="medium",
        position=0,
        source_chat_id=42,
    )
    s.add(ticket)
    s.commit()
    tid = ticket.id
    s.expunge_all()
    row = s.query(KanbanTicket).filter(KanbanTicket.id == tid).first()
    assert row is not None
    assert row.source_chat_id == 42
    row.source_chat_id = None
    s.commit()
    s.expunge_all()
    cleared = s.query(KanbanTicket).filter(KanbanTicket.id == tid).first()
    assert cleared.source_chat_id is None
