"""Slack → ``IntegrationMessageBus`` normalization (TASK 17).

Expects Events API–style dict slices (no ``slack_sdk`` required for routing tests).
"""

from __future__ import annotations

from distr.core.integrations.bus import IncomingMessage, get_integration_message_bus

PLATFORM_ID = "slack"


def slack_event_to_incoming(
    *,
    channel_id: str,
    user_id: str | None,
    text: str,
    attachment_paths: list[str] | None = None,
    raw: dict | None = None,
    speak: bool | None = None,
) -> IncomingMessage:
    return IncomingMessage(
        platform=PLATFORM_ID,
        thread_id=str(channel_id),
        sender_id=str(user_id) if user_id else None,
        text=(text or "").strip(),
        attachments=list(attachment_paths or []),
        raw=dict(raw or {}),
        speak=speak,
    )


def route_slack_inbound_to_agent(
    *,
    channel_id: str,
    user_id: str | None,
    text: str,
    attachment_paths: list[str] | None = None,
    raw: dict | None = None,
    speak: bool | None = None,
) -> None:
    msg = slack_event_to_incoming(
        channel_id=channel_id,
        user_id=user_id,
        text=text,
        attachment_paths=attachment_paths,
        raw=raw,
        speak=speak,
    )
    get_integration_message_bus().ingest_incoming(msg)
