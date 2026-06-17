"""LLM provider availability gates (Settings → Chat dropdowns)."""

from distr.core.services.settings_service import thirdparty_llm_provider_ready


def test_valid_llm_providers_includes_nvidia():
    from distr.core.chat import valid_llm_providers

    assert "NVIDIA" in valid_llm_providers()


def test_thirdparty_llm_provider_ready_requires_enabled_and_key():
    settings = {
        "nvidia_enabled": True,
        "nvidia_key": "nvapi-test",
    }
    assert thirdparty_llm_provider_ready(settings, "nvidia_enabled", "nvidia_key")

    assert not thirdparty_llm_provider_ready(
        {"nvidia_enabled": False, "nvidia_key": "nvapi-test"},
        "nvidia_enabled",
        "nvidia_key",
    )
    assert not thirdparty_llm_provider_ready(
        {"nvidia_enabled": True, "nvidia_key": ""},
        "nvidia_enabled",
        "nvidia_key",
    )
    assert not thirdparty_llm_provider_ready(
        {"nvidia_enabled": 0, "nvidia_key": "nvapi-test"},
        "nvidia_enabled",
        "nvidia_key",
    )
