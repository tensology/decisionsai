"""Regression tests for vision API token-limit parameter selection and fallback.

OpenAI GPT-5 / o-series vision models (e.g. gpt-5.6-sol, gpt-5.4-mini) reject
``max_tokens`` and require ``max_completion_tokens``.
"""

from types import SimpleNamespace

from distr.core.agent.tools.vision.vision_api import (
    call_openai_vision,
    openai_token_limit_param,
    swap_token_limit_param,
)


def test_openai_token_limit_param_selects_max_completion_tokens_for_gpt5_and_o_series():
    assert openai_token_limit_param("gpt-5.6-sol") == "max_completion_tokens"
    assert openai_token_limit_param("gpt-5.4-mini") == "max_completion_tokens"
    assert openai_token_limit_param("openai/gpt-5.4-mini") == "max_completion_tokens"
    assert openai_token_limit_param("o3") == "max_completion_tokens"
    assert openai_token_limit_param("o4-mini") == "max_completion_tokens"
    assert openai_token_limit_param("gpt-4.1-mini") == "max_tokens"
    assert openai_token_limit_param("claude-sonnet-4") == "max_tokens"


def test_swap_token_limit_param_swaps_both_directions():
    payload = {"model": "gpt-5.6-sol", "max_tokens": 2000}
    assert swap_token_limit_param(
        payload,
        "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.",
    )
    assert "max_tokens" not in payload
    assert payload["max_completion_tokens"] == 2000

    payload2 = {"model": "legacy-model", "max_completion_tokens": 2000}
    assert swap_token_limit_param(
        payload2,
        "Unsupported parameter: 'max_completion_tokens' is not supported; use 'max_tokens'.",
    )
    assert "max_completion_tokens" not in payload2
    assert payload2["max_tokens"] == 2000


def test_call_openai_vision_uses_max_completion_tokens_and_json_mode_for_gpt5(monkeypatch):
    seen_kwargs = []

    class FakeCompletions:
        def create(self, **kwargs):
            seen_kwargs.append(dict(kwargs))
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"summary": "ok"}'))]
            )

    class FakeOpenAI:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "openai_key": "sk-test",
            "vision_llm_provider": "openai",
            "vision_llm_model": "gpt-5.6-sol",
        },
    )

    out = call_openai_vision(["AAAA"], "Describe this screenshot", "gpt-5.6-sol", False)
    assert out == '{"summary": "ok"}'
    assert len(seen_kwargs) == 1
    assert seen_kwargs[0]["model"] == "gpt-5.6-sol"
    assert seen_kwargs[0]["max_completion_tokens"] == 2000
    assert "max_tokens" not in seen_kwargs[0]
    assert seen_kwargs[0]["response_format"] == {"type": "json_object"}


def test_call_openai_vision_retries_on_token_param_mismatch(monkeypatch):
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(dict(kwargs))
            if "max_tokens" in kwargs:
                raise RuntimeError("Unsupported parameter: 'max_tokens'; use 'max_completion_tokens'")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"summary": "recovered"}'))]
            )

    class FakeOpenAI:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "openai_key": "sk-test",
            "vision_llm_provider": "openai",
            "vision_llm_model": "custom-future-model",
        },
    )

    out = call_openai_vision(["AAAA"], "Describe this screenshot", "custom-future-model", False)
    assert out == '{"summary": "recovered"}'
    assert len(calls) == 2
    assert "max_tokens" in calls[0]
    assert "max_completion_tokens" in calls[1]
