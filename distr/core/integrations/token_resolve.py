"""Resolve Discord / Slack credentials from env or ``connected_accounts`` (Advanced settings)."""

from __future__ import annotations

import json
import os
from typing import Any

PROVIDER_DISCORD_BOT = "discord_bot"
PROVIDER_SLACK_APP = "slack_app"


def integration_accounts_from_settings(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if settings is None:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
    raw = settings.get("connected_accounts")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def resolve_discord_bot_token() -> str | None:
    env_t = (os.environ.get("DECISIONSAI_DISCORD_BOT_TOKEN") or "").strip()
    if env_t:
        return env_t
    for acc in integration_accounts_from_settings():
        if acc.get("provider") == PROVIDER_DISCORD_BOT:
            t = (acc.get("bot_token") or "").strip()
            return t or None
    return None


def resolve_slack_bot_token() -> str | None:
    env_t = (os.environ.get("DECISIONSAI_SLACK_BOT_TOKEN") or "").strip()
    if env_t:
        return env_t
    for acc in integration_accounts_from_settings():
        if acc.get("provider") == PROVIDER_SLACK_APP:
            t = (acc.get("bot_token") or "").strip()
            return t or None
    return None


def resolve_slack_signing_secret() -> str | None:
    env_t = (os.environ.get("DECISIONSAI_SLACK_SIGNING_SECRET") or "").strip()
    if env_t:
        return env_t
    for acc in integration_accounts_from_settings():
        if acc.get("provider") == PROVIDER_SLACK_APP:
            t = (acc.get("signing_secret") or "").strip()
            return t or None
    return None
