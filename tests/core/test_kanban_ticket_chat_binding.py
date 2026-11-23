"""A chat may raise more than one ticket.

The old unique index on kanban_tickets.source_chat_id made "create a ticket"
fail with a constraint error whenever the chat already owned one (Paul,
2026-11-23: the dashboard reorder could not be ticketed from the chat because
ticket #330 was already bound to it).
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_schema_has_no_unique_source_chat_index():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        rows = list(conn.execute(text("PRAGMA index_list('kanban_tickets')")))
    unique_on_source = [
        name for (_seq, name, unique, *_rest) in rows
        if unique and "source_chat" in str(name)
    ]
    assert unique_on_source == []


def test_a_chat_can_own_several_tickets():
    Session = _session()
    with Session() as db:
        board = KanbanBoard(name="B", source="database")
        db.add(board)
        db.flush()
        for i, name in enumerate(("Backlog", "In Progress", "QA", "Complete")):
            db.add(KanbanLane(board_id=board.id, name=name, position=i))
        db.flush()
        lane = db.query(KanbanLane).filter_by(board_id=board.id, name="Backlog").first()

        first = KanbanTicket(lane_id=lane.id, title="First", source_chat_id=1381)
        second = KanbanTicket(lane_id=lane.id, title="Second", source_chat_id=1381)
        db.add_all([first, second])
        db.flush()  # must not raise a UNIQUE constraint error

        assert db.query(KanbanTicket).filter_by(source_chat_id=1381).count() == 2
