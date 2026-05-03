"""Discord → ``IntegrationMessageBus`` normalization (TASK 16).

Uses plain strings/dicts so tests run without ``discord.py``.
"""

from __future__ import annotations

from distr.core.integrations.bus import IncomingMessage, get_integration_message_bus

PLATFORM_ID = "discord"


def discord_message_to_incoming(
    *,
    channel_id: str,
    author_id: str | None,
    content: str,
    attachment_paths: list[str] | None = None,
    raw: dict | None = None,
    speak: bool | None = None,
) -> IncomingMessage:
    """Build ``IncomingMessage`` from gateway/webhook-style fields."""
    return IncomingMessage(
        platform=PLATFORM_ID,
        thread_id=str(channel_id),
        sender_id=str(author_id) if author_id else None,
        text=(content or "").strip(),
        attachments=list(attachment_paths or []),
        raw=dict(raw or {}),
        speak=speak,
    )


def route_discord_inbound_to_agent(
    *,
    channel_id: str,
    author_id: str | None,
    content: str,
    attachment_paths: list[str] | None = None,
    raw: dict | None = None,
    speak: bool | None = None,
) -> None:
    """Enqueue normalized Discord text into the same sink path as Telegram."""
    msg = discord_message_to_incoming(
        channel_id=channel_id,
        author_id=author_id,
        content=content,
        attachment_paths=attachment_paths,
        raw=raw,
        speak=speak,
    )
    get_integration_message_bus().ingest_incoming(msg)
