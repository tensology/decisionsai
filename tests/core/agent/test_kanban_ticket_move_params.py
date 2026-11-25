import contextlib
from queue import Queue

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.agent.tools.integrations.kanban_ticket import (
    KanbanTicketInput,
    KanbanTicketTool,
    missing_move_ticket_params_error,
    parse_move_ticket_instruction,
)
from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket


def test_schema_describes_ticket_id_and_lane_together():
    ticket_field = KanbanTicketInput.model_fields["ticket_id"]
    lane_field = KanbanTicketInput.model_fields["lane_name"]
    ticket_desc = (ticket_field.description or "").lower()
    lane_desc = (lane_field.description or "").lower()
    assert "together" in ticket_desc and "call" in ticket_desc
    assert "same call" in lane_desc or ("together" in lane_desc and "call" in lane_desc)
    assert "move #123 to qa" in (ticket_field.description or "").lower() or "move #" in (
        ticket_field.description or ""
    ).lower()


def test_parse_move_ticket_instruction_from_text():
    ticket_id, lane = parse_move_ticket_instruction("Please move #345 to QA")
    assert ticket_id == 345
    assert lane.lower() == "qa"
    ticket_id, lane = parse_move_ticket_instruction(
        "move ticket 12 from Backlog to In Progress"
    )
    assert ticket_id == 12
    assert "progress" in lane.lower()
    # Explicit fields win over text.
    ticket_id, lane = parse_move_ticket_instruction("move #1 to QA", ticket_id=9, lane_name="Backlog")
    assert ticket_id == 9
    assert lane == "Backlog"


def test_missing_params_error_lists_both_fields():
    message = missing_move_ticket_params_error(345, "")
    assert "ticket_id=345" in message
    assert "lane_name=(missing)" in message
    assert "move #345 to QA" in message or "lane_name='QA'" in message


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

    monkeypatch.setattr(KanbanTicketTool, "_get_session", lambda self: get_session())
    return get_session


def test_move_ticket_text_form_and_split_calls(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        board = KanbanBoard(name="Move Board", source="database")
        session.add(board)
        session.flush()
        backlog = KanbanLane(board_id=board.id, name="Backlog", position=0)
        qa = KanbanLane(board_id=board.id, name="QA", position=1)
        session.add_all([backlog, qa])
        session.flush()
        ticket = KanbanTicket(lane_id=backlog.id, title="E2E move me (safe to delete)", position=0)
        other = KanbanTicket(lane_id=backlog.id, title="Earlier ticket", position=1)
        session.add_all([ticket, other])
        session.flush()
        ids = {"ticket": ticket.id, "other": other.id, "qa": qa.id, "board": board.id, "backlog": backlog.id}

    queue = Queue()
    tool = KanbanTicketTool(event_queue=queue)
    missing = tool._run(action="move_ticket", ticket_id=ids["other"], lane_name="", text="")
    assert "ticket_id=" in missing and "lane_name=" in missing
    assert "same call" in missing.lower()

    moved = tool._run(action="move_ticket", text=f"move #{ids['ticket']} to QA")
    assert "Moved ticket" in moved or "moved" in moved.lower()
    assert queue.get_nowait() == ("kanban_ticket_moved", {
        "ticket_id": ids["ticket"],
        "source_board_id": ids["board"],
        "target_board_id": ids["board"],
    })
    assert "lane_name=(missing)" in tool._run(action="move_ticket", ticket_id=ids["other"])
    assert queue.empty()
    with get_session() as session:
        row = session.get(KanbanTicket, ids["ticket"])
        assert int(row.lane_id) == ids["qa"]
        assert session.get(KanbanTicket, ids["other"]).lane_id == ids["backlog"]


def test_move_ticket_accepts_fuzzy_lane_and_split_followup(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        board = KanbanBoard(name="Fuzzy Board", source="database")
        session.add(board)
        session.flush()
        backlog = KanbanLane(board_id=board.id, name="Backlog", position=0)
        progress = KanbanLane(board_id=board.id, name="In Progress", position=1)
        session.add_all([backlog, progress])
        session.flush()
        ticket = KanbanTicket(lane_id=backlog.id, title="Need a move", position=0)
        session.add(ticket)
        session.flush()
        ids = {"ticket": int(ticket.id), "progress": int(progress.id)}

    tool = KanbanTicketTool()
    first = tool._run(action="move_ticket", ticket_id=ids["ticket"], lane_name="")
    assert "lane_name=(missing)" in first
    # A follow-up that only has the lane can still complete because the tool
    # remembered the ticket id, but the first call already explained both params.
    second = tool._run(action="move_ticket", ticket_id=0, lane_name="in-progress")
    assert "Moved ticket" in second or "moved" in second.lower()
    with get_session() as session:
        row = session.get(KanbanTicket, ids["ticket"])
        assert int(row.lane_id) == ids["progress"]
