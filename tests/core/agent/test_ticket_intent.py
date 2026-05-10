from distr.core.agent.ticket_intent import (
    classify_ticket_intent,
    format_skill_recommendations_markdown,
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


def test_remote_ticket_requests_are_classified_separately():
    jira = classify_ticket_intent("create a Jira ticket for the OAuth callback failure")
    trello = classify_ticket_intent("make a Trello card for the landing page bug")

    assert jira.kind == "external_ticket"
    assert trello.kind == "external_ticket"


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


def test_cursor_ticket_tool_writes_decisionsai_ticket_in_debug_mode(monkeypatch, tmp_path):
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

    tickets_dir = tmp_path / ".tickets"
    created = list(tickets_dir.glob("*.md"))
    assert len(created) == 1
    assert "Location:" in result
    assert str(tickets_dir) in result
    assert "DecisionsAI" in result
    assert "Ensure Telegram delivery status is reliable." in created[0].read_text()


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
