from distr.core.agent.ticket_intent import (
    classify_ticket_intent,
    draft_ticket_from_request,
    format_skill_recommendations_markdown,
    is_weak_ticket_title,
    recommend_skills_for_ticket,
)
from distr.core.agent.tools.integrations.create_cursor_ticket import CreateCursorTicketTool


def test_generic_create_ticket_routes_to_kanban(monkeypatch):
    monkeypatch.setenv("DEBUG", "False")
    intent = classify_ticket_intent("create a ticket for the workflow getting stuck")

    assert intent.kind == "kanban_ticket"
    assert intent.confidence >= 0.9


def test_explicit_cursor_ticket_routes_to_cursor():
    intent = classify_ticket_intent("tell cursor to fix the login bug")

    assert intent.kind == "cursor_ticket"
    assert intent.confidence >= 0.9


def test_conversational_cursor_planning_does_not_create_handoff():
    cursor = classify_ticket_intent(
        "Can we talk through what work should be done in Cursor before sending anything?"
    )
    codex = classify_ticket_intent(
        "Can we talk through what work should be done in Codex before sending anything?"
    )

    assert cursor.kind == "ide_conversation"
    assert codex.kind == "ide_conversation"


def test_cursor_ticket_tool_rejects_conversational_planning():
    tool = CreateCursorTicketTool()

    result = tool._run("Can we talk through what work should be done in Cursor before sending anything?")

    assert "conversation" in result.lower()
    assert "handoff" in result.lower()


def test_remote_ticket_requests_are_classified_separately():
    jira = classify_ticket_intent("create a Jira ticket for the OAuth callback failure")
    trello = classify_ticket_intent("make a Trello card for the landing page bug")

    assert jira.kind == "external_ticket"
    assert trello.kind == "external_ticket"


def test_ticket_requested_in_downloads_routes_to_file_creation():
    intent = classify_ticket_intent("create a ticket in Downloads for the onboarding bug")

    assert intent.kind == "ticket_file"
    assert intent.confidence >= 0.9


def test_type_out_ticket_routes_to_typing_not_board_creation():
    intent = classify_ticket_intent("type out a ticket for the login bug in the focused editor")

    assert intent.kind == "type_text"
    assert intent.confidence >= 0.9


def test_decisions_ticket_routes_to_debug_project_tickets_when_debug_enabled(monkeypatch):
    monkeypatch.setenv("DEBUG", "True")

    intent = classify_ticket_intent("make a ticket for DecisionsAI to fix the flaky Telegram status")

    assert intent.kind == "debug_decisions_ticket"
    assert intent.confidence >= 0.9


def test_decisions_ticket_routes_to_kanban_when_debug_disabled(monkeypatch):
    monkeypatch.setenv("DEBUG", "False")

    intent = classify_ticket_intent("make a ticket for decisions to fix the flaky Telegram status")

    assert intent.kind == "kanban_ticket"


def test_cursor_ticket_tool_rejects_generic_ticket_creation():
    tool = CreateCursorTicketTool()

    result = tool._run("create a ticket for the UI not responding properly")

    assert "Ticket Board" in result
    assert "create_ticket" in result


def test_cursor_ticket_tool_writes_decisionsai_cursor_handoff_in_debug_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("DEBUG", "True")
    monkeypatch.setattr(
        "distr.core.agent.tools.integrations.create_cursor_ticket._decisionsai_project_root",
        lambda: str(tmp_path),
    )

    tool = CreateCursorTicketTool()
    monkeypatch.setattr(
        tool,
        "_generate_cleaned_ticket",
        lambda raw_content, is_clipboard=False, is_conversation=False: (
            "Title: Fix Telegram status\nEnsure Telegram delivery status is reliable."
        ),
    )

    result = tool._run("make a ticket for DecisionsAI to fix the flaky Telegram status")

    handoffs_dir = tmp_path / ".decisions" / "cursor-handoffs"
    created = list(handoffs_dir.glob("*.md"))
    assert len(created) == 1
    assert "Location:" in result
    assert str(handoffs_dir) in result
    assert "DecisionsAI" in result
    assert "Ensure Telegram delivery status is reliable." in created[0].read_text()


def test_cursor_ticket_tool_describes_plugin_handoff_not_legacy_extension():
    tool = CreateCursorTicketTool()

    assert "Cursor plugin handoff" in tool.description
    assert "legacy Cursor/.tickets" not in tool.description
    assert "VS Code extension" not in tool.description


def test_ticket_skill_recommendations_include_relevant_available_skills():
    recs = recommend_skills_for_ticket(
        "Fix the frontend UI bug, add Playwright regression tests, and validate the workflow result."
    )
    names = {rec.name for rec in recs}

    assert "webapp-testing" in names
    assert "test-driven-development" in names

    rendered = format_skill_recommendations_markdown(recs)
    assert "## Recommended Skills" in rendered
    assert "`webapp-testing`" in rendered


def test_ticket_draft_strips_meta_command_and_project_target():
    draft = draft_ticket_from_request(
        "make a ticket for DecisionsAI to fix the flaky Telegram delivery status"
    )

    assert draft.title == "Fix the flaky Telegram delivery status"
    assert draft.description == "Fix the flaky Telegram delivery status."
    assert draft.project_hint == "DecisionsAI"


def test_weak_ticket_title_detection_catches_meta_titles():
    assert is_weak_ticket_title("Instruction from user")
    assert is_weak_ticket_title("Create a ticket")
    assert not is_weak_ticket_title("Fix flaky Telegram delivery status")


def test_kanban_ticket_summary_replaces_llm_meta_title(monkeypatch):
    from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool

    tool = KanbanTicketTool()
    tool.llm_service = object()
    monkeypatch.setattr(
        tool,
        "_call_llm_sync",
        lambda _prompt: "Title: Instruction from user\nDescription: The user asked to create a ticket.",
    )

    summary = tool._summarise_for_ticket(
        "User instruction: make a ticket for DecisionsAI to fix workflow validation getting stuck"
    )

    assert summary["title"] == "Fix workflow validation getting stuck"
    assert summary["description"] == "Fix workflow validation getting stuck."
