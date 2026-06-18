"""Relay auth uses project .env and WhatsApp device fallback."""

from __future__ import annotations


def test_relay_auth_headers_uses_env_file_token(tmp_path, monkeypatch):
    from distr.core.integrations import relay_auth

    env_path = tmp_path / ".env"
    env_path.write_text("RELAY_INTERNAL_TOKEN=from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(relay_auth, "_project_env_path", lambda: env_path)
    monkeypatch.delenv("RELAY_INTERNAL_TOKEN", raising=False)
    relay_auth._env_loaded = False

    headers = relay_auth.relay_auth_headers()
    assert headers == {"X-Relay-Internal-Token": "from-dotenv"}


def test_relay_auth_headers_falls_back_to_device_bearer(monkeypatch):
    from distr.core.integrations import relay_auth

    monkeypatch.setattr(relay_auth, "ensure_relay_env_loaded", lambda: None)
    monkeypatch.setattr(
        "distr.core.integrations.telegram.utils.relay_internal_token",
        lambda: "",
    )
    monkeypatch.setattr(
        "distr.core.integrations.whatsapp.relay_client.relay_request_headers",
        lambda force_refresh=False: {"Authorization": "Bearer ws-jwt"},
    )

    assert relay_auth.relay_auth_headers() == {"Authorization": "Bearer ws-jwt"}
