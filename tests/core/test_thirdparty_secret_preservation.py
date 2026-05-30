from types import SimpleNamespace
from unittest.mock import patch

from distr.gui.web.routes.settings._shared import resolve_secret_update


def _thirdparty_payload(**overrides):
    base = {
        "ollama_url": "http://localhost:11434/",
        "assemblyai_enabled": False,
        "assemblyai_key": "",
        "openai_enabled": True,
        "openai_key": "",
        "anthropic_enabled": False,
        "anthropic_key": "",
        "cursor_enabled": False,
        "cursor_key": "",
        "elevenlabs_enabled": False,
        "elevenlabs_key": "",
        "openrouter_enabled": False,
        "openrouter_key": "",
        "groq_enabled": False,
        "groq_key": "",
        "kilo_enabled": True,
        "kilo_key": "",
        "gemini_enabled": False,
        "gemini_key": "",
        "masko_enabled": False,
        "masko_key": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@patch("distr.core.services.settings_service._safe_emit")
@patch("distr.core.services.settings_service.save_settings_to_db")
@patch(
    "distr.core.services.settings_service.load_settings_from_db",
    return_value={
        "openai_enabled": True,
        "openai_key": "sk-existing-openai",
        "kilo_enabled": True,
        "kilo_key": "kilo-existing",
    },
)
def test_thirdparty_save_preserves_existing_keys_when_payload_keys_are_blank(
    _load,
    save_settings,
    _emit,
):
    from distr.core.services.settings_service import save_thirdparty_settings

    save_thirdparty_settings(_thirdparty_payload(), resolve_secret_update)

    saved = save_settings.call_args.args[0]
    assert saved["openai_enabled"] is True
    assert saved["openai_key"] == "sk-existing-openai"
    assert saved["kilo_enabled"] is True
    assert saved["kilo_key"] == "kilo-existing"


@patch("distr.core.services.settings_service._safe_emit")
@patch("distr.core.services.settings_service.save_settings_to_db")
@patch(
    "distr.core.services.settings_service.load_settings_from_db",
    return_value={
        "openai_enabled": True,
        "openai_key": "sk-existing-openai",
    },
)
def test_thirdparty_save_replaces_existing_key_when_new_key_is_submitted(
    _load,
    save_settings,
    _emit,
):
    from distr.core.services.settings_service import save_thirdparty_settings

    save_thirdparty_settings(
        _thirdparty_payload(openai_key="sk-new-openai"),
        resolve_secret_update,
    )

    saved = save_settings.call_args.args[0]
    assert saved["openai_key"] == "sk-new-openai"


@patch("distr.core.services.settings_service._safe_emit")
@patch("distr.core.services.settings_service.save_settings_to_db")
@patch(
    "distr.core.services.settings_service.load_settings_from_db",
    return_value={
        "openai_enabled": True,
        "openai_key": "sk-existing-openai",
    },
)
def test_thirdparty_save_clears_key_when_provider_is_disabled_with_blank_key(
    _load,
    save_settings,
    _emit,
):
    from distr.core.services.settings_service import save_thirdparty_settings

    save_thirdparty_settings(
        _thirdparty_payload(openai_enabled=False, openai_key=""),
        resolve_secret_update,
    )

    saved = save_settings.call_args.args[0]
    assert saved["openai_enabled"] is False
    assert saved["openai_key"] == ""


@patch("distr.core.services.settings_service._safe_emit")
@patch("distr.core.services.settings_service.save_settings_to_db")
@patch(
    "distr.core.services.settings_service.load_settings_from_db",
    return_value={
        "openai_enabled": True,
        "openai_key": "sk-existing-openai",
    },
)
def test_thirdparty_save_keeps_submitted_key_even_when_provider_is_disabled(
    _load,
    save_settings,
    _emit,
):
    from distr.core.services.settings_service import save_thirdparty_settings

    save_thirdparty_settings(
        _thirdparty_payload(openai_enabled=False, openai_key="sk-replacement"),
        resolve_secret_update,
    )

    saved = save_settings.call_args.args[0]
    assert saved["openai_enabled"] is False
    assert saved["openai_key"] == "sk-replacement"
