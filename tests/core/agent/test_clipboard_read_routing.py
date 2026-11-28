"""Phrase routing and selection-read capture for remote/desktop read requests."""

from unittest.mock import AsyncMock, patch

import pytest

from distr.core.agent.services.llm.fast_action_detector import ActionType, detect_fast_action
from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService
from distr.core.agent.services.llm.tool_format import convert_tools_to_openai_format
from distr.core.agent.tool_intents import forced_tool_names_for_text
from distr.core.agent.tools.clipboard.clipboard_actions import (
    ClipboardActionTool,
    get_selected_text_for_reading,
    is_existing_clipboard_request,
    is_selection_read_request,
)


CLIPBOARD_SPEAK_PHRASES = [
    "read from clipboard",
    "read from clipboard.",
    "Read from the clipboard please",
    "read the clipboard",
    "read my clipboard",
    "From Clipboard.",
    "from the clipboard",
    "from clipboard",
    "what's in the clipboard",  # discussion? wait - this is "what's in" which detector maps to GET/llm
    "read what's in the clipboard",
    "read what I copied",
    "read what I copied please",
]

# "what's in the clipboard" historically shows content to the LLM rather than auto-TTS.
CLIPBOARD_SPEAK_PHRASES_TTS = [
    "read from clipboard",
    "read from clipboard.",
    "Read from the clipboard please",
    "read the clipboard",
    "read my clipboard",
    "From Clipboard.",
    "from the clipboard",
    "from clipboard",
    "read what's in the clipboard",
    "read what I copied",
    "read what I copied please",
    "can you read from clipboard",
    "please read the clipboard",
]

SELECTION_PHRASES = [
    "read this",
    "read this.",
    "read this please",
    "read this,",
    "Read that",
    "read that.",
    "read selected",
    "read selected.",
    "read selected please",
    "read the selection",
    "read the selection.",
    "read selected text",
    "read the selected text please",
    "can you read this?",
    "please read the selection",
]


DISCUSS_PHRASES = [
    "go and read the clipboard and let's talk about it",
    "check the clipboard and tell me what you think",
]


@pytest.mark.parametrize("phrase", CLIPBOARD_SPEAK_PHRASES_TTS)
def test_detector_clipboard_phrases_speak_existing_clipboard(phrase):
    action = detect_fast_action(phrase)
    assert action is not None, phrase
    assert action.action_type == ActionType.CLIPBOARD_READ, phrase
    assert action.tool_name == "clipboard_action"
    assert action.tool_args.get("action") == "get"
    assert action.response_type == "tts_clipboard"


@pytest.mark.parametrize("phrase", SELECTION_PHRASES)
def test_detector_selection_phrases(phrase):
    action = detect_fast_action(phrase)
    assert action is not None, phrase
    assert action.action_type == ActionType.CLIPBOARD_READ, phrase
    assert action.tool_args.get("action") == "read"
    assert action.response_type == "tts"


@pytest.mark.parametrize("phrase", DISCUSS_PHRASES)
def test_detector_clipboard_discussion_stays_llm(phrase):
    action = detect_fast_action(phrase)
    assert action is not None, phrase
    assert action.action_type == ActionType.CLIPBOARD_GET
    assert action.response_type == "llm_response"


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("From Clipboard.", True),
        ("read what I copied", True),
        ("read this", False),
        ("read the selection", False),
        ("talk about the clipboard", False),
    ],
)
def test_is_existing_clipboard_request(phrase, expected):
    assert is_existing_clipboard_request(phrase) is expected


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("read this please", True),
        ("read selected", True),
        ("read the selection.", True),
        ("read selected text", True),
        ("From Clipboard.", False),
        ("read from clipboard", False),
    ],
)
def test_is_selection_read_request(phrase, expected):
    assert is_selection_read_request(phrase) is expected


def test_read_clipboard_uses_pbpaste_not_hotkey():
    tool = ClipboardActionTool()
    with (
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions.get_clipboard_content",
            return_value="already copied",
        ) as pbpaste,
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions._copy_selection_via_hotkey"
        ) as hotkey,
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions.get_selected_text_via_accessibility"
        ) as ax,
        patch("distr.core.agent.tools.clipboard.clipboard_actions.pyautogui") as pag,
    ):
        result = tool._run(action="read_clipboard")
        result_from_text = tool._run(text="read from clipboard")

    assert result == "READ_ACTION:already copied"
    assert result_from_text == "READ_ACTION:already copied"
    assert pbpaste.call_count >= 2
    hotkey.assert_not_called()
    ax.assert_not_called()
    pag.keyDown.assert_not_called()


def test_get_action_still_returns_context_wrapper():
    tool = ClipboardActionTool()
    with patch(
        "distr.core.agent.tools.clipboard.clipboard_actions.get_clipboard_content",
        return_value="notes",
    ), patch("distr.core.agent.tools.clipboard.clipboard_actions.pyautogui") as pag:
        result = tool._run(action="get")
    assert result.startswith("CLIPBOARD CONTENT:")
    assert "notes" in result
    pag.keyDown.assert_not_called()


def test_selection_read_prefers_ax_and_skips_clipboard():
    tool = ClipboardActionTool()
    with (
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions.get_selected_text_via_accessibility",
            return_value="selected in TextEdit",
        ) as ax,
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions._copy_selection_via_hotkey"
        ) as hotkey,
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions.snapshot_pasteboard"
        ) as snap,
        patch("distr.core.agent.tools.clipboard.clipboard_actions.pyautogui") as pag,
    ):
        result = tool._run(action="read", text="read this")

    assert result == "READ_ACTION:selected in TextEdit"
    ax.assert_called()
    hotkey.assert_not_called()
    snap.assert_not_called()
    pag.keyDown.assert_not_called()


def test_selection_read_fallback_saves_and_restores_pasteboard():
    tool = ClipboardActionTool()
    snapshot = [{"public.utf8-plain-text": b"original", "public.png": b"\x89PNG"}]
    with (
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions.get_selected_text_via_accessibility",
            return_value=None,
        ),
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions.snapshot_pasteboard",
            return_value=snapshot,
        ) as snap,
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions._copy_selection_via_hotkey",
            return_value="copied selection",
        ) as hotkey,
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions.restore_pasteboard",
            return_value=True,
        ) as restore,
    ):
        result = tool._run(action="read", text="read the selection")

    assert result == "READ_ACTION:copied selection"
    snap.assert_called_once()
    hotkey.assert_called_once()
    restore.assert_called_once_with(snapshot)


def test_get_selected_text_for_reading_restores_after_hotkey_error():
    snapshot = [{"public.utf8-plain-text": b"keep me"}]
    with (
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions.get_selected_text_via_accessibility",
            return_value=None,
        ),
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions.snapshot_pasteboard",
            return_value=snapshot,
        ),
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions._copy_selection_via_hotkey",
            side_effect=RuntimeError("hotkey failed"),
        ),
        patch(
            "distr.core.agent.tools.clipboard.clipboard_actions.restore_pasteboard",
            return_value=True,
        ) as restore,
    ):
        with pytest.raises(RuntimeError, match="hotkey failed"):
            get_selected_text_for_reading()
    restore.assert_called_once_with(snapshot)


def test_rewrite_maps_clipboard_and_selection_intents():
    assert OpenAICompatibleLLMService._rewrite_tool_for_intent(
        "clipboard_action", {"action": "get"}, "From Clipboard."
    ) == ("clipboard_action", {"action": "read_clipboard", "text": "From Clipboard."})
    assert OpenAICompatibleLLMService._rewrite_tool_for_intent(
        "clipboard_action", {"action": "get"}, "read this please"
    ) == ("clipboard_action", {"action": "read", "text": "read this please"})
    # explain/set are left alone
    assert OpenAICompatibleLLMService._rewrite_tool_for_intent(
        "clipboard_action", {"action": "explain"}, "explain this"
    ) == ("clipboard_action", {"action": "explain"})


@pytest.mark.asyncio
async def test_speak_clear_read_request_skips_follow_up():
    service = OpenAICompatibleLLMService.__new__(OpenAICompatibleLLMService)
    service._messages = [
        {"role": "user", "content": "From Clipboard."},
        {
            "role": "tool",
            "name": "clipboard_action",
            "content": "READ_ACTION:restart status rundown",
        },
    ]
    service._fa_deliver_spoken_response = AsyncMock()

    assert await service._speak_clear_read_request() is True
    service._fa_deliver_spoken_response.assert_awaited_once()
    spoken = service._fa_deliver_spoken_response.await_args.args[0]
    assert spoken == "restart status rundown"
    assert service._spoke_clear_read_request is True


@pytest.mark.asyncio
async def test_speak_clear_read_request_ignores_discussion():
    service = OpenAICompatibleLLMService.__new__(OpenAICompatibleLLMService)
    service._messages = [
        {"role": "user", "content": "check the clipboard and tell me what you think"},
        {
            "role": "tool",
            "name": "clipboard_action",
            "content": "CLIPBOARD CONTENT:\n\nnotes\n\nThis is the current clipboard content. x",
        },
    ]
    service._fa_deliver_spoken_response = AsyncMock()
    assert await service._speak_clear_read_request() is False
    service._fa_deliver_spoken_response.assert_not_called()


def test_clipboard_action_schema_includes_read_actions():
    tool = ClipboardActionTool()
    schemas = convert_tools_to_openai_format([tool])
    assert schemas
    enum = schemas[0]["function"]["parameters"]["properties"]["action"]["enum"]
    assert "read" in enum
    assert "read_clipboard" in enum


def test_clipboard_tool_is_forced_for_spoken_requests():
    for phrase in ("From Clipboard.", "read this", "read what I copied", "read selected"):
        assert "clipboard_action" in forced_tool_names_for_text(phrase), phrase
