"""Tests for TTS-friendly tool completion phrases."""

from distr.core.agent.services.llm.text_utils import brief_tool_completion_message


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
