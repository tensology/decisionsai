"""Settings + bearer token for MCP server mode. Never log or return the token."""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

SETTING_ENABLED = "mcp_server_enabled"          # default False
SETTING_RELAY = "mcp_server_relay_vendors"      # default True (only matters when enabled)
ENV_ENABLED = "DECISIONS_MCP_SERVER_ENABLED"
ENV_TOKEN = "DECISIONS_MCP_SERVER_TOKEN"
ENV_TOKEN_FILE = "DECISIONS_MCP_SERVER_TOKEN_FILE"

_TRUE = {"1", "true", "yes", "on"}


def _settings() -> dict:
    try:
        from distr.core.settings import load_settings_from_db

        data = load_settings_from_db()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in _TRUE


def is_enabled(settings: dict | None = None) -> bool:
    env = os.environ.get(ENV_ENABLED)
    if env is not None and env.strip() != "":
        return _truthy(env)
    s = settings if settings is not None else _settings()
    return _truthy(s.get(SETTING_ENABLED, False))


def relay_vendors_enabled(settings: dict | None = None) -> bool:
    s = settings if settings is not None else _settings()
    raw = s.get(SETTING_RELAY, True)
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return True  # mcpcols: empty column = default on
    return _truthy(raw) if not isinstance(raw, bool) else raw


def token_path() -> Path:
    configured = (os.environ.get(ENV_TOKEN_FILE) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".decisionsai" / "mcp_server_token"


def get_or_create_token() -> str:
    """Dedicated MCP-server token (separate from the internal web API token)."""
    env = (os.environ.get(ENV_TOKEN) or "").strip()
    if env:
        return env
    path = token_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            return existing
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    return token


def token_matches(presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(presented.strip().encode(), get_or_create_token().encode())


def token_is_env_override() -> bool:
    return bool((os.environ.get(ENV_TOKEN) or "").strip())


def regenerate_token() -> str:
    """Write a fresh token file. Takes effect for /mcp immediately (read per request)."""
    if token_is_env_override():
        raise RuntimeError("token is set by environment variable; cannot regenerate")
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    os.replace(tmp, path)
    return token


def mask_token(token: str) -> str:
    return ("\u2022" * 12 + token[-4:]) if token else ""


def set_enabled(value: bool) -> None:
    from distr.core.settings import load_settings_from_db, save_settings_to_db

    s = load_settings_from_db()
    s[SETTING_ENABLED] = bool(value)
    save_settings_to_db(s)
