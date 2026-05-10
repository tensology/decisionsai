from unittest.mock import patch

from distr.core.agent.tools.clipboard.clipboard_actions import ClipboardActionTool


def test_clipboard_action_extracts_set_content():
    assert ClipboardActionTool._extract_set_content("set clipboard to hello world") == "hello world"
    assert ClipboardActionTool._extract_set_content("write hello world into my clipboard") == "hello world"


def test_clipboard_action_set_writes_content():
    tool = ClipboardActionTool()

    with patch(
        "distr.core.agent.tools.clipboard.clipboard_actions.set_clipboard_content",
        return_value=True,
    ) as set_clipboard:
        result = tool._run(action="set", content="hello world")

    set_clipboard.assert_called_once_with("hello world")
    assert "Clipboard updated" in result


def test_clipboard_action_set_can_parse_text_request():
    tool = ClipboardActionTool()

    with patch(
        "distr.core.agent.tools.clipboard.clipboard_actions.set_clipboard_content",
        return_value=True,
    ) as set_clipboard:
        result = tool._run(text="set clipboard to hello world")

    set_clipboard.assert_called_once_with("hello world")
    assert "Clipboard updated" in result
