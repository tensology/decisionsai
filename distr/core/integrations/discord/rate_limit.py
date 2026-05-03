"""Outbound staging for Discord API retries (TASK 16 — minimal queue)."""

from __future__ import annotations

from distr.core.integrations.outbound_queue import BoundedOutboundQueue


class DiscordOutboundQueue(BoundedOutboundQueue[dict]):
    """Holds pending outbound payloads (e.g. ``{"channel_id": ..., "content": ...}``)."""

    def __init__(self, max_items: int = 256) -> None:
        super().__init__(max_items=max_items)
