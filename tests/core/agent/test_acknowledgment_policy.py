"""Tests for intent-aware acknowledgment policy in OpenAI-compatible services."""

from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService


def _tool_call(name: str) -> dict:
    return {"function": {"name": name, "arguments": {}}}


def test_short_direct_request_does_not_trigger_acknowledgment():
    should = OpenAICompatibleLLMService._should_speak_acknowledgment(
        "open chrome",
        [_tool_call("smart_open")],
    )
    assert should is False


def test_long_running_tool_triggers_acknowledgment():
    should = OpenAICompatibleLLMService._should_speak_acknowledgment(
        "please investigate why the workflow keeps failing and fix it",
        [_tool_call("run_workflow")],
    )
    assert should is True


def test_long_research_intent_triggers_acknowledgment():
    should = OpenAICompatibleLLMService._should_speak_acknowledgment(
        "can you analyze why this bug only happens in production and trace it",
        [_tool_call("execute_code")],
    )
    assert should is True
