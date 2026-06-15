"""Read-aloud fast actions should surface as expandable system activity."""

from distr.core.agent.tool_audit import _build_chat_tool_event, _chat_title


def test_read_aloud_tool_event_stores_full_text():
    long_text = "A" * 500
    event = _build_chat_tool_event(
        1,
        "read_aloud",
        long_text,
        "completed",
        "Read from clipboard",
        None,
        None,
        turn_chat_id=42,
    )
    assert event["tool_name"] == "read_aloud"
    assert event["title"] == "Read from clipboard"
    assert event["result_detail"] == long_text
    assert len(event["result_summary"]) < len(long_text)


def test_read_aloud_chat_title_uses_instruction_hint():
    assert _chat_title("read_aloud", "ignored", "Read aloud") == "Read aloud"
    assert _chat_title("read_aloud", "ignored", "Read from clipboard") == "Read from clipboard"
