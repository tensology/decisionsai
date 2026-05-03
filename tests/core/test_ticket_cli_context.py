"""Tests for full Kanban ticket → CLI instruction assembly (§2.5)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, WhatsAppMessage
from distr.core.db.kanban import (
    KanbanBoard,
    KanbanLane,
    KanbanTicket,
    KanbanTicketFile,
    KanbanTicketLink,
    KanbanTicketTodo,
)
from distr.core.kanban.ticket_cli_context import (
    CLI_INSTRUCTION_MAX_CHARS,
    build_kanban_ticket_cli_instruction,
)


def _memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_cli_instruction_includes_board_lane_checklist_links_files():
    s = _memory_session()
    try:
        board = KanbanBoard(name="Ops Board")
        s.add(board)
        s.flush()
        lane = KanbanLane(board_id=board.id, name="Current", position=0)
        s.add(lane)
        s.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Fix login",
            description="OAuth redirect broken.",
            priority="high",
            position=0,
            time_estimate="2h",
        )
        s.add(ticket)
        s.flush()
        s.add(KanbanTicketTodo(ticket_id=ticket.id, text="Repro bug", done=False, position=0))
        s.add(KanbanTicketTodo(ticket_id=ticket.id, text="Patch", done=True, position=1))
        s.add(KanbanTicketLink(ticket_id=ticket.id, title="Spec", url="https://example.com/spec"))
        s.add(
            KanbanTicketFile(
                ticket_id=ticket.id,
                filename="screenshot.png",
                file_path="kanban_uploads/snap.png",
            )
        )
        s.commit()

        text = build_kanban_ticket_cli_instruction(
            s,
            ticket.id,
            project_name="DecisionsAI",
            project_folder="/tmp/decisionsai",
            project_id=42,
        )

        assert "Ticket ID: {}".format(ticket.id) in text
        assert "Ops Board" in text
        assert "Current" in text
        assert "OAuth redirect broken." in text
        assert "[ ] Repro bug" in text
        assert "[x] Patch" in text
        assert "https://example.com/spec" in text
        assert "screenshot.png" in text
        assert "kanban_uploads/snap.png" in text
        assert "Linked project: DecisionsAI" in text
        assert "/tmp/decisionsai" in text
        assert "--- PRIMARY TASK ---" in text
    finally:
        s.close()


def test_cli_instruction_includes_whatsapp_source():
    s = _memory_session()
    try:
        board = KanbanBoard(name="WA Board")
        s.add(board)
        s.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        s.add(lane)
        s.flush()
        wm = WhatsAppMessage(
            message_id="msg-uniq-1",
            jid="27634000000@s.whatsapp.net",
            jid_phone="27634000000",
            text="Customer says login fails",
            media_type="photo",
            media_local_path="whatsapp_media/photo_1.jpg",
        )
        s.add(wm)
        s.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="From WhatsApp",
            description="Follow up.",
            position=0,
            whatsapp_message_id=wm.id,
        )
        s.add(ticket)
        s.commit()

        text = build_kanban_ticket_cli_instruction(s, ticket.id, project_name="P", project_folder="/x", project_id=1)

        assert "WhatsApp source:" in text
        assert "27634000000" in text
        assert "whatsapp_media/photo_1.jpg" in text
        assert "Customer says login fails" in text
    finally:
        s.close()


def test_cli_instruction_truncates_when_huge():
    s = _memory_session()
    try:
        board = KanbanBoard(name="B")
        s.add(board)
        s.flush()
        lane = KanbanLane(board_id=board.id, name="L", position=0)
        s.add(lane)
        s.flush()
        huge = "x" * (CLI_INSTRUCTION_MAX_CHARS + 5000)
        ticket = KanbanTicket(lane_id=lane.id, title="T", description=huge, position=0)
        s.add(ticket)
        s.commit()

        text = build_kanban_ticket_cli_instruction(
            s,
            ticket.id,
            max_total_chars=4000,
            project_name="",
            project_folder="",
            project_id=None,
        )
        assert len(text) <= 4000 + 100  # small slack for truncation banner
        assert "truncated" in text.lower()
    finally:
        s.close()


def test_cli_instruction_missing_ticket():
    s = _memory_session()
    try:
        out = build_kanban_ticket_cli_instruction(s, 99999)
        assert "not found" in out.lower()
    finally:
        s.close()
