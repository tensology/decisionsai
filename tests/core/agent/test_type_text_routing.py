import pytest

from distr.core.agent.services.llm.fast_action_detector import ActionType, detect_fast_action
from distr.core.agent.tools.base import fast_tool_matcher
from distr.core.agent.tools.input.type_text import TypeTextTool


def test_story_typing_request_reaches_llm_but_literal_typing_stays_fast():
    tool = TypeTextTool()
    request = "Can you type out a story about a dog named Spot?"

    detected = detect_fast_action(request)
    assert detected.action_type == ActionType.CONVERSATIONAL
    assert fast_tool_matcher(request, [tool], {tool.name: tool}) is None

    literal = detect_fast_action("type hello world")
    assert literal.action_type == ActionType.TYPE_TEXT
    assert literal.tool_args == {"text": "hello world"}


@pytest.mark.parametrize(
    "utterance",
    [
        "paste last dictation",
        "Paste at last dictation",
        "Paste, last, dictation.",
        "paste the lost dictation",
        "can you paste my latest dictation?",
    ],
)
def test_paste_last_dictation_routes_to_chat_backed_typing(utterance):
    detected = detect_fast_action(utterance)

    assert detected.action_type == ActionType.TYPE_TEXT
    assert detected.tool_name == "type_text"
    assert detected.tool_args == {"source": "last_dictation"}


def test_paste_last_dictation_complaint_does_not_execute_the_action():
    detected = detect_fast_action("Paste last dictation doesn't work")

    assert not (
        detected.action_type == ActionType.TYPE_TEXT
        and detected.tool_args == {"source": "last_dictation"}
    )


def test_paste_last_dictation_uses_direct_insertion_without_clipboard(monkeypatch):
    class ChatManager:
        def get_current_chat(self):
            return 42

    inserted = []
    monkeypatch.setattr(
        "distr.core.agent.tool_audit.load_context_notes",
        lambda chat_id: ["older dictation", "latest dictation"],
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.input.type_text.insert_text",
        lambda text, **kwargs: inserted.append((text, kwargs)) or True,
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.input.type_text.get_clipboard_content",
        lambda: (_ for _ in ()).throw(AssertionError("clipboard was read")),
    )

    result = TypeTextTool(chat_manager=ChatManager())._run(source="last_dictation")

    assert result == "Pasted 16 characters as keyboard input."
    assert inserted == [("latest dictation", {"instant": True, "press_enter": False})]
