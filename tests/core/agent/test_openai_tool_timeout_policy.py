from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService


def test_normal_tool_keeps_short_outer_ceiling():
    assert OpenAICompatibleLLMService._tool_execution_timeout_seconds(
        "create_ticket", {}
    ) == 90.0


def test_save_audio_allows_long_form_synthesis():
    assert OpenAICompatibleLLMService._tool_execution_timeout_seconds(
        "save_audio", {}
    ) == 1800.0


def test_pi_tool_has_cold_start_safe_fallback(monkeypatch):
    class BrokenSession:
        def __enter__(self):
            raise RuntimeError("database unavailable")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("distr.core.db.get_session", lambda: BrokenSession())

    assert OpenAICompatibleLLMService._tool_execution_timeout_seconds(
        "pi_agent", {}
    ) == 900.0


def test_ticket_decomposition_cannot_create_desktop_action():
    reason = OpenAICompatibleLLMService._tool_intent_block_reason(
        "create_action",
        "Break this project down into tickets and execute the workflow.",
    )

    assert "wrong-domain" in reason
    assert "create_ticket" in reason


def test_normal_desktop_action_is_not_blocked():
    assert OpenAICompatibleLLMService._tool_intent_block_reason(
        "create_action", "Create an action named Morning setup"
    ) == ""


def test_computer_use_requires_explicit_desktop_intent():
    reason = OpenAICompatibleLLMService._tool_intent_block_reason(
        "computer_use", "I don't know how you're holding up."
    )

    assert "unrequested computer use" in reason.lower()
    assert OpenAICompatibleLLMService._tool_intent_block_reason(
        "computer_use", "On my screen, first open Terminal, then type hello."
    ) == ""


def test_tool_building_requires_explicit_user_authorization():
    reason = OpenAICompatibleLLMService._tool_intent_block_reason(
        "build_tool", "First option."
    )

    assert "explicit" in reason.lower()
    assert OpenAICompatibleLLMService._tool_intent_block_reason(
        "build_tool", "Build a reusable tool for moving Terminal"
    ) == ""


def test_system_info_requires_a_system_or_model_question():
    assert OpenAICompatibleLLMService._tool_intent_block_reason(
        "system_info", "I want you to perform the first option."
    )
    assert OpenAICompatibleLLMService._tool_intent_block_reason(
        "system_info", "Which model are you using?"
    ) == ""


def test_raw_window_bounds_cannot_bypass_numbered_display_resolver():
    reason = OpenAICompatibleLLMService._tool_intent_block_reason(
        "set_window_bounds", "Move the terminal to the third screen."
    )

    assert "window_management" in reason


def test_provider_tool_cap_prioritizes_forced_tool():
    class Tool:
        def __init__(self, name):
            self.name = name

    tools = [Tool(f"tool_{index}") for index in range(130)]
    window_tool = Tool("window_management")
    tools.append(window_tool)

    capped = OpenAICompatibleLLMService._cap_provider_tools(
        tools, "Move the terminal to the third screen"
    )

    assert len(capped) == 128
    assert window_tool in capped


def test_generic_done_is_replaced_by_last_tool_evidence():
    service = object.__new__(OpenAICompatibleLLMService)
    service._messages = [
        {"role": "tool", "name": "set_window_bounds", "content": "Moved Terminal to display 3 and verified its position."}
    ]

    assert service._ground_follow_up_content("Done.") == (
        "Moved Terminal to display 3 and verified its position."
    )


def test_generic_done_surfaces_last_tool_failure():
    service = object.__new__(OpenAICompatibleLLMService)
    service._messages = [
        {"role": "tool", "name": "set_window_bounds", "content": "Error: display 3 was not found"}
    ]

    assert service._ground_follow_up_content("Done.").startswith("I couldn't complete that")


def test_ticket_scope_blocks_worker_before_tickets_exist():
    reason = OpenAICompatibleLLMService._tool_intent_block_reason(
        "pi_agent",
        "Scope this into clear, independently executable tickets linked to the workflow.",
    )

    assert "premature worker dispatch" in reason
    assert "Create and verify" in reason


def test_ticket_scope_allows_worker_after_ticket_evidence():
    assert OpenAICompatibleLLMService._tool_intent_block_reason(
        "pi_agent",
        "Scope this into clear, independently executable tickets linked to the workflow.",
        tickets_verified=True,
    ) == ""


def test_only_concrete_ticket_result_counts_as_evidence():
    assert OpenAICompatibleLLMService._is_verified_ticket_result(
        "create_ticket", "Created ticket #42: Research the artist"
    )
    assert not OpenAICompatibleLLMService._is_verified_ticket_result(
        "ticket_board", "Sent ticket to orchestrator"
    )
    assert not OpenAICompatibleLLMService._is_verified_ticket_result(
        "create_ticket", "Failed to create ticket #42"
    )
