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
