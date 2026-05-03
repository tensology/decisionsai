"""
Discord connector (TASK 16 / R12) — bridge + outbound queue + optional ``discord.py`` hook.
"""

from __future__ import annotations

from distr.core.integrations.discord.bridge import (
    PLATFORM_ID,
    discord_message_to_incoming,
    route_discord_inbound_to_agent,
)
from distr.core.integrations.discord.rate_limit import DiscordOutboundQueue
from distr.core.integrations.discord.runner import start_discord_bot_background

__all__ = [
    "PLATFORM_ID",
    "DiscordOutboundQueue",
    "discord_message_to_incoming",
    "route_discord_inbound_to_agent",
    "start_discord_bot_background",
]
