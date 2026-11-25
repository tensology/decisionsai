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


def test_call_openai_vision_refuses_an_unconfigured_model(monkeypatch):
    """No silent 'gpt-5.4-mini' substitution: an empty model must raise."""
    import distr.core.settings as settings_module
    from distr.core.agent.tools.vision import vision_api

    monkeypatch.setattr(settings_module, "load_settings_from_db", lambda: {"openai_key": "sk-test"})

    with pytest.raises(ValueError) as excinfo:
        vision_api.call_openai_vision(
            base64_images=["AAAA"],
            enhanced_prompt="describe",
            vision_model="",
            is_action_request=False,
        )
    assert "No vision model configured" in str(excinfo.value)


def test_call_openai_vision_reports_the_model_before_the_key(monkeypatch):
    """An empty model + no key should blame the model, not send the user chasing a key."""
    import distr.core.settings as settings_module
    from distr.core.agent.tools.vision import vision_api

    monkeypatch.setattr(settings_module, "load_settings_from_db", lambda: {})

    with pytest.raises(ValueError) as excinfo:
        vision_api.call_openai_vision(
            base64_images=["AAAA"],
            enhanced_prompt="describe",
            vision_model="",
            is_action_request=False,
        )
    assert "No vision model configured" in str(excinfo.value)
