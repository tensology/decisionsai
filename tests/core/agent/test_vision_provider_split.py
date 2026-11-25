"""A vision model saved as 'provider/model' must reach the API client bare."""

import pytest

from distr.core.agent.tools.vision.vision_api import (
    resolve_vision_llm_config,
    split_provider_model,
)


@pytest.mark.parametrize(
    "provider,model,expected",
    [
        # The reported bug: a prefixed name must lose the prefix.
        ("openai", "openai/gpt-5.6-sol", ("openai", "gpt-5.6-sol")),
        # Already bare: unchanged.
        ("openai", "gpt-5.6-sol", ("openai", "gpt-5.6-sol")),
        # Case-insensitive provider match.
        ("OpenAI", "openai/gpt-5.6-sol", ("OpenAI", "gpt-5.6-sol")),
        # Multi-vendor names that legitimately contain a slash must survive.
        ("openrouter", "google/gemini-2.0-flash", ("openrouter", "google/gemini-2.0-flash")),
        ("ollama", "meta-llama/llama-3-70b", ("ollama", "meta-llama/llama-3-70b")),
        # A prefix that does not match the provider is not a provider prefix.
        ("openai", "deepseek/deepseek-v4.1-flash", ("openai", "deepseek/deepseek-v4.1-flash")),
        # No provider configured: do not guess.
        ("", "openai/gpt-5.6-sol", ("", "openai/gpt-5.6-sol")),
        # Degenerate inputs.
        ("openai", "", ("openai", "")),
        ("openai", "openai/", ("openai", "openai/")),
        ("openai", "/gpt-5.6-sol", ("openai", "/gpt-5.6-sol")),
    ],
)
def test_split_provider_model(provider, model, expected):
    assert split_provider_model(provider, model) == expected


def test_resolve_vision_llm_config_strips_the_prefix():
    settings = {"vision_llm_provider": "openai", "vision_llm_model": "openai/gpt-5.6-sol"}
    assert resolve_vision_llm_config(settings) == ("openai", "gpt-5.6-sol")


def test_resolve_vision_llm_config_bare_model_unchanged():
    settings = {"vision_llm_provider": "openai", "vision_llm_model": "gpt-5.6-sol"}
    assert resolve_vision_llm_config(settings) == ("openai", "gpt-5.6-sol")


def test_resolve_vision_llm_config_falls_back_to_conversational():
    settings = {
        "conversational_llm_provider": "openrouter",
        "conversational_llm_model": "deepseek/deepseek-v4.1-flash",
    }
    assert resolve_vision_llm_config(settings) == ("openrouter", "deepseek/deepseek-v4.1-flash")


def test_resolve_vision_model_strips_the_prefix():
    from distr.core.agent.services.llm.image_utils import resolve_vision_model

    settings = {"vision_llm_provider": "openai", "vision_llm_model": "openai/gpt-5.6-sol"}
    assert resolve_vision_model(settings) == ("openai", "gpt-5.6-sol")
