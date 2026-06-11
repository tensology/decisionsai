"""Tests for ticket orchestrator engagement message building."""

from unittest.mock import MagicMock, patch

from distr.core.kanban.ticket_orchestrator_engagement import (
    build_agent_context,
    build_display_brief,
    build_orchestrator_messages,
    send_ticket_engagement_to_agent,
    strip_html,
)


def test_strip_html_removes_tags():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_build_display_brief_uses_title():
    brief = build_display_brief({"title": "Finish Player1Sport"})
    assert brief == 'Sent ticket "Finish Player1Sport" to the orchestrator.'


def test_build_orchestrator_messages_splits_display_and_agent():
    ticket = {
        "id": 10,
        "title": "Finish Player1Sport",
        "description": "Complete the remaining work.",
        "priority": "medium",
    }
    display, agent = build_orchestrator_messages(
        ticket,
        is_local=True,
        board_label="My Board",
        source="database",
    )
    assert display == 'Sent ticket "Finish Player1Sport" to the orchestrator.'
    assert "orchestrator engage this ticket" in agent
    assert "Finish Player1Sport" in agent
    assert "Local ticket id: 10" in agent
    assert "Priority: medium" in agent
    assert "Sent ticket" not in agent


def test_build_agent_context_includes_project_when_linked():
    ticket = {
        "id": 3,
        "title": "Ship it",
        "linked_project_id": 7,
        "linked_project_name": "DecisionsAI",
        "linked_project_folder": "/tmp/proj",
    }
    agent = build_agent_context(ticket, is_local=True, board_label="Board")
    assert "Project id: 7" in agent
    assert "DecisionsAI" in agent
    assert "/tmp/proj" in agent


def test_build_agent_context_includes_activation_block():
    ticket = {"id": 1, "title": "Fix login"}
    agent = build_agent_context(
        ticket,
        is_local=True,
        board_label="Sprint",
        board_data={
            "activated_board_id": 4,
            "activated_board_name": "Sprint",
            "activated_project_id": 9,
            "activated_project_name": "DecisionsAI",
        },
    )
    assert "Work context now active" in agent
    assert "Active board: Sprint" in agent
    assert "Active project: DecisionsAI" in agent


@patch("distr.core.kanban.ticket_orchestrator_engagement.record_ticket_engagement_activity")
@patch("distr.core.signals.signal_manager")
def test_send_ticket_engagement_records_activity_and_loads_chat_in_order(
    mock_signal_manager, mock_record_activity
):
    mock_signal_manager.web_load_chat_and_process_requested = MagicMock()
    send_ticket_engagement_to_agent(
        12,
        'Sent ticket "Fix login" to the orchestrator.',
        "[Ticket Board — orchestrator engage this ticket]\nFull context",
        speak=True,
    )
    mock_record_activity.assert_called_once_with(
        12,
        'Sent ticket "Fix login" to the orchestrator.',
        board_label="",
    )
    mock_signal_manager.web_load_chat_and_process_requested.emit.assert_called_once_with(
        12,
        "[Ticket Board — orchestrator engage this ticket]\nFull context",
        True,
        True,
    )
