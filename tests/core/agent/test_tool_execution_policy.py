from types import SimpleNamespace

from distr.core.agent.services.llm.tool_execution_policy import (
    is_mutating_tool_call,
    no_action_requested,
    remember_successful_tool_call,
    sanitized_tool_arguments,
    tool_execution_block_reason,
)


def _owner(user_text: str):
    return SimpleNamespace(
        chat_manager=None,
        _messages=[{"role": "user", "content": user_text}],
    )


def test_explicit_no_action_blocks_mutation_but_allows_read() -> None:
    text = "Don't try to do anything. Try and suss out what's going on."
    owner = _owner(text)

    assert no_action_requested(text)
    assert tool_execution_block_reason(
        owner,
        "google_workspace",
        {"action": "create_calendar_event"},
        text,
    ).startswith("Blocked mutation")
    assert tool_execution_block_reason(
        owner,
        "google_workspace",
        {"action": "get_calendar_events"},
        text,
    ) == ""


def test_positive_dont_forget_request_is_not_treated_as_no_action() -> None:
    text = "Don't forget to create the calendar event."
    assert not no_action_requested(text)


def test_successful_single_mutation_cannot_repeat_in_same_turn() -> None:
    text = "Create the calendar event."
    owner = _owner(text)
    first = {
        "action": "create_calendar_event",
        "params": {"start_time": "2026-10-07T12:30:00"},
    }
    changed = {
        "action": "create_calendar_event",
        "params": {"start_time": "2026-10-07T14:30:00"},
    }

    assert tool_execution_block_reason(owner, "google_workspace", first, text) == ""
    remember_successful_tool_call(owner, "google_workspace", first, text, "Event created")

    assert "exact tool call" in tool_execution_block_reason(
        owner, "google_workspace", first, text
    )
    assert "already succeeded" in tool_execution_block_reason(
        owner, "google_workspace", changed, text
    )


def test_explicit_multiple_request_allows_distinct_mutations() -> None:
    text = "Create two calendar events."
    owner = _owner(text)
    first = {"action": "create_calendar_event", "params": {"summary": "One"}}
    second = {"action": "create_calendar_event", "params": {"summary": "Two"}}

    remember_successful_tool_call(owner, "google_workspace", first, text, "Event created")

    assert tool_execution_block_reason(owner, "google_workspace", second, text) == ""
    assert "exact tool call" in tool_execution_block_reason(
        owner, "google_workspace", first, text
    )


def test_mutation_classification_covers_multiplexed_and_named_tools() -> None:
    assert not is_mutating_tool_call("google_workspace", {"action": "check_inbox"})
    assert is_mutating_tool_call("google_workspace", {"action": "send_email"})
    assert not is_mutating_tool_call("list_projects", {})
    assert is_mutating_tool_call("create_ticket", {})


def test_audit_arguments_redact_secrets_and_free_text() -> None:
    clean = sanitized_tool_arguments(
        {
            "action": "send_email",
            "api_key": "secret",
            "params": {"to": "person@example.com", "body": "hello"},
            "last_user_message": "send it",
        }
    )

    assert clean["api_key"] == "<redacted>"
    assert clean["params"]["body"] == "<redacted 5 chars>"
    assert clean["last_user_message"] == "<redacted 7 chars>"
