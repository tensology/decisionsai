from distr.core.agent.services.llm.context_selection import select_messages_for_context


def test_context_selection_keeps_complete_recent_turns_and_system_prompt():
    messages = [
        {"role": "system", "content": "checkpoint: durable prior state"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "use the tool"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "x", "content": "tool result"},
        {"role": "assistant", "content": "tool answer"},
        {"role": "user", "content": "newest question"},
        {"role": "assistant", "content": "newest answer"},
    ]

    selected = select_messages_for_context(messages, max_tokens=55, reserve_tokens=8)

    assert selected[0] == messages[0]
    assert selected[-2:] == messages[-2:]
    assert not any(message.get("tool_call_id") == "call-1" for message in selected)


def test_context_selection_does_not_mutate_source_messages():
    messages = [
        {"role": "system", "content": "system"},
        *(
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"message {index}"}
            for index in range(80)
        ),
    ]
    snapshot = [dict(message) for message in messages]

    selected = select_messages_for_context(messages, max_tokens=80, reserve_tokens=10)

    assert messages == snapshot
    assert len(selected) < len(messages)
    assert selected[0]["role"] == "system"

