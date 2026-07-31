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
