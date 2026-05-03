"""Optional ``discord.py`` bot hook — install ``discord`` extra to use."""

from __future__ import annotations


def ensure_discord_py_installed() -> None:
    """Raise ``ImportError`` with install hint if ``discord.py`` is missing."""
    try:
        import discord  # noqa: F401
    except ImportError as e:
        raise ImportError(
            'Discord bot integration requires `discord.py`. Install with: pip install "discord.py"'
        ) from e


def create_bot_stub(_token: str) -> None:  # pragma: no cover
    """Deprecated hint — use ``discord.runner.start_discord_bot_background()`` + ``DECISIONSAI_DISCORD_BOT_TOKEN``."""
    ensure_discord_py_installed()
    raise NotImplementedError(
        "Use DECISIONSAI_DISCORD_BOT_TOKEN and distr.core.integrations.discord.runner.start_discord_bot_background"
    )
