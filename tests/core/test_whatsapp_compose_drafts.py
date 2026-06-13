"""WhatsApp compose draft helpers and agent draft actions."""

from unittest.mock import patch

from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool
from distr.core.kanban.whatsapp_compose_drafts import (
    normalize_draft_text,
    sanitize_agent_draft_text,
)


def test_normalize_draft_text_strips_and_rejects_whitespace_only():
    assert normalize_draft_text("  hello  ") == "hello"
    assert normalize_draft_text("   \n\t  ") == ""
    assert normalize_draft_text(None) == ""


def test_sanitize_agent_draft_text_removes_em_dashes_and_semicolons():
    raw = "Quick update — we can ship today; let me know."
    cleaned = sanitize_agent_draft_text(raw)
    assert "—" not in cleaned
    assert ";" not in cleaned
    assert "ship today" in cleaned


def test_sanitize_agent_draft_text_empty_on_whitespace():
    assert sanitize_agent_draft_text("   ") == ""


@patch("distr.core.agent.tools.integrations.kanban_ticket.KanbanTicketTool._resolve_whatsapp_draft_target")
@patch("distr.core.kanban.whatsapp_compose_drafts.save_compose_draft")
def test_whatsapp_set_draft_saves_sanitized_agent_draft(mock_save, mock_resolve):
    mock_resolve.return_value = ("27123456789", "27123456789@s.whatsapp.net", 5, "Merrypak")
    mock_save.return_value = {
        "jid_phone": "27123456789",
        "text": "Hi, we got your order.",
        "source": "agent",
    }

    tool = KanbanTicketTool()
    result = tool._action_whatsapp_set_draft(
        jid_phone="27123456789",
        text="Hi — we got your order.",
    )

    mock_save.assert_called_once()
    kwargs = mock_save.call_args.kwargs
    assert kwargs["jid_phone"] == "27123456789"
    assert kwargs["source"] == "agent"
    assert kwargs["sanitize"] is True
    assert "Draft saved" in result


@patch("distr.core.agent.tools.integrations.kanban_ticket.KanbanTicketTool._resolve_whatsapp_draft_target")
@patch("distr.core.kanban.whatsapp_compose_drafts.get_compose_draft")
def test_whatsapp_get_draft_reports_agent_pending(mock_get, mock_resolve):
    mock_resolve.return_value = ("27123456789", None, None, "Merrypak")
    mock_get.return_value = {
        "jid_phone": "27123456789",
        "text": "Draft body",
        "source": "agent",
    }

    tool = KanbanTicketTool()
    result = tool._action_whatsapp_get_draft(jid_phone="27123456789")

    assert "awaiting user review" in result
    assert "Draft body" in result


@patch("distr.core.kanban.whatsapp_compose_drafts.list_compose_drafts")
def test_whatsapp_list_drafts_summarizes_pending(mock_list):
    mock_list.return_value = [
        {"jid_phone": "1", "contact_name": "Alice", "text": "Hi there", "source": "agent"},
        {"jid_phone": "2", "contact_name": "Bob", "text": "Thanks", "source": "user"},
    ]

    tool = KanbanTicketTool()
    result = tool._action_whatsapp_list_drafts()

    assert "2 WhatsApp drafts" in result
    assert "need your review" in result
    assert "Alice" in result
