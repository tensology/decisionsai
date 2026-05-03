"""Discord optional runner — no discord.py / token required for unit tests."""

from __future__ import annotations

import pytest

from distr.core.integrations.discord.runner import discord_bot_token_from_env, start_discord_bot_background


def test_start_discord_without_env_token_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECISIONSAI_DISCORD_BOT_TOKEN", raising=False)
    assert start_discord_bot_background() is False


def test_discord_bot_token_from_env_none_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECISIONSAI_DISCORD_BOT_TOKEN", "   ")
    assert discord_bot_token_from_env() is None
