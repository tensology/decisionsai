"""Tests for TTS-friendly tool completion phrases."""

from distr.core.agent.services.llm.text_utils import (
    brief_tool_completion_message,
    humanize_tool_completion,
)


def test_brief_tool_completion_known_tool():
    s = brief_tool_completion_message("open_page")
    assert s
    assert "done" not in s.lower()


def test_brief_tool_completion_unknown_tool():
    s = brief_tool_completion_message("some_unknown_tool_xyz")
    assert s
    assert s.lower() != "done"


def test_brief_tool_completion_empty_name():
    s = brief_tool_completion_message("")
    assert s
    assert s.lower() != "done"


def test_machine_tool_results_are_humanized():
    assert humanize_tool_completion(
        "type_text", "Typed 166 characters as keyboard input."
    ) == "I've typed that for you."
    assert humanize_tool_completion(
        "google_workspace", "Event created successfully (ID: abc123)"
    ) == "I've added that to your calendar."
    assert humanize_tool_completion(
        "media_control", "Executed media control action: nexttrack"
    ) == "I've skipped to the next track."


def test_meaningful_tool_result_is_not_replaced():
    assert humanize_tool_completion("execute_code", "Task finished successfully.") is None
