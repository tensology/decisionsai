"""Outbound staging for Slack Web/API retries (TASK 17)."""

from __future__ import annotations

from distr.core.integrations.outbound_queue import BoundedOutboundQueue


class SlackOutboundQueue(BoundedOutboundQueue[dict]):
    def __init__(self, max_items: int = 256) -> None:
        super().__init__(max_items=max_items)
