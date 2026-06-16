"""Resolve third-party API keys: Decisions Settings DB first, env vars as fallback."""

from __future__ import annotations

import os


def _from_settings(*field_names: str) -> str:
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db() or {}
        for name in field_names:
            value = (settings.get(name) or "").strip()
            if value:
                return value
    except Exception:
        pass
    return ""


def settings_secret(
    *env_names: str,
    settings_fields: tuple[str, ...] = (),
) -> str:
    """Read a secret from env, then from encrypted settings columns."""
    for name in env_names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return _from_settings(*settings_fields)


def composio_api_key() -> str:
    """Composio Connect MCP project API key (DB column: rube_token)."""
    return settings_secret(
        "COMPOSIO_API_KEY",
        "COMPOSIO_KEY",
        settings_fields=("rube_token",),
    )


def composio_enabled() -> bool:
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db() or {}
        if settings.get("rube_enabled"):
            return True
        return bool(composio_api_key())
    except Exception:
        return bool(composio_api_key())


def fal_api_key() -> str:
    return settings_secret("FAL_KEY", settings_fields=("fal_key",))


def exa_api_key() -> str:
    return settings_secret("EXA_API_KEY", settings_fields=("exa_key",))
