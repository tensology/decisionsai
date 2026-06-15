from distr.core.kanban.ticket_context_notes import (
    _compact_note,
    append_ticket_context_note,
    format_context_notes_block,
    register_ticket_chat_context,
)


def test_append_ticket_context_note_adds_timestamped_line():
    from unittest.mock import MagicMock, patch

    from distr.core.db.kanban import KanbanTicket

    ticket = KanbanTicket()
    ticket.context_notes = ""
    session = MagicMock()
    with patch("distr.core.db.orm_compat.orm_get_by_id", return_value=ticket):
        assert append_ticket_context_note(session, 1, "User asked about PDF attachments", source="orchestrator")
    assert "[orchestrator]" in (ticket.context_notes or "")
    assert "PDF attachments" in (ticket.context_notes or "")


def test_compact_note_truncates_long_text():
    text = "x" * 400
    assert len(_compact_note(text)) <= 320


def test_format_context_notes_block_empty():
    assert format_context_notes_block("") == ""


def test_register_ticket_chat_context():
    register_ticket_chat_context(9, 124)
    from distr.core.kanban.ticket_context_notes import get_registered_ticket_for_chat

    assert get_registered_ticket_for_chat(9) == 124
