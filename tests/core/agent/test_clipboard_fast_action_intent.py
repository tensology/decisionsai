from distr.core.agent.services.llm.fast_action_detector import ActionType, detect_fast_action


def test_clipboard_discussion_request_consumes_context_instead_of_reading_aloud():
    action = detect_fast_action("go and read the clipboard and let's talk about it")

    assert action.action_type == ActionType.CLIPBOARD_GET
    assert action.tool_name == "clipboard_action"
    assert action.tool_args["action"] == "get"
    assert action.response_type == "llm_response"


def test_clipboard_review_request_consumes_context():
    action = detect_fast_action("check the clipboard and tell me what you think")

    assert action.action_type == ActionType.CLIPBOARD_GET
    assert action.tool_args["action"] == "get"
    assert action.response_type == "llm_response"


def test_plain_read_from_clipboard_still_routes_to_direct_tts():
    action = detect_fast_action("can you read from clipboard")

    assert action.action_type == ActionType.CLIPBOARD_READ
    assert action.tool_name == "clipboard_action"
    assert action.tool_args["action"] == "get"
    assert action.response_type == "tts_clipboard"


def test_read_clipboard_out_loud_still_routes_to_direct_tts():
    action = detect_fast_action("read the clipboard out loud")

    assert action.action_type == ActionType.CLIPBOARD_READ
    assert action.response_type == "tts_clipboard"
