"""Integration token resolution (env vs connected_accounts)."""

from __future__ import annotations

import pytest

from distr.core.integrations.token_resolve import (
    PROVIDER_DISCORD_BOT,
    PROVIDER_SLACK_APP,
    integration_accounts_from_settings,
    resolve_discord_bot_token,
    resolve_slack_bot_token,
    resolve_slack_signing_secret,
)


def test_resolve_discord_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECISIONSAI_DISCORD_BOT_TOKEN", "env-discord")
    settings = {
        "connected_accounts": [
            {"provider": PROVIDER_DISCORD_BOT, "bot_token": "db-discord"},
        ]
    }
    assert resolve_discord_bot_token() == "env-discord"
    assert integration_accounts_from_settings(settings)[0]["bot_token"] == "db-discord"


def test_resolve_discord_from_settings_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECISIONSAI_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "connected_accounts": [{"provider": PROVIDER_DISCORD_BOT, "bot_token": "tok"}],
        },
    )
    assert resolve_discord_bot_token() == "tok"


def test_resolve_slack_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECISIONSAI_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DECISIONSAI_SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "connected_accounts": [
                {"provider": PROVIDER_SLACK_APP, "signing_secret": "shhh", "bot_token": "xoxb-1"},
            ],
        },
    )
    assert resolve_slack_signing_secret() == "shhh"
    assert resolve_slack_bot_token() == "xoxb-1"
