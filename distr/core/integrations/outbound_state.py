"""Shared outbound queue singletons for Discord / Slack (TASK 16–17).

Callers enqueue dict payloads; an :class:`~distr.core.integrations.outbound_worker.IntegrationOutboundWorker`
drains with retries once a deliver function is wired (e.g. ``chat.postMessage``).
"""

from __future__ import annotations

from distr.core.integrations.discord.rate_limit import DiscordOutboundQueue
from distr.core.integrations.slack.rate_limit import SlackOutboundQueue

_discord_outbound: DiscordOutboundQueue | None = None
_slack_outbound: SlackOutboundQueue | None = None


def get_discord_outbound_queue() -> DiscordOutboundQueue:
    global _discord_outbound
    if _discord_outbound is None:
        _discord_outbound = DiscordOutboundQueue()
    return _discord_outbound


def get_slack_outbound_queue() -> SlackOutboundQueue:
    global _slack_outbound
    if _slack_outbound is None:
        _slack_outbound = SlackOutboundQueue()
    return _slack_outbound


def reset_outbound_queues_for_tests() -> None:
    """Clear singletons (tests only)."""
    global _discord_outbound, _slack_outbound
    _discord_outbound = None
    _slack_outbound = None
