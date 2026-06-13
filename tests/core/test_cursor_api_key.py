"""Cursor API key resolution for CLI backends."""

from distr.core.project_cli_backends.registry import _cursor_api_key


def test_cursor_api_key_uses_settings_key_even_when_disabled(monkeypatch):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"cursor_enabled": False, "cursor_key": "sk-test-cursor"},
    )
    assert _cursor_api_key() == "sk-test-cursor"


def test_cursor_api_key_prefers_environment(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "env-key")
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"cursor_enabled": True, "cursor_key": "db-key"},
    )
    assert _cursor_api_key() == "env-key"
