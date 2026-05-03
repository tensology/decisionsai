"""
Slack connector (TASK 17 / R13) — bridge + outbound queue + Events API signing.
"""

from __future__ import annotations

from distr.core.integrations.slack.bridge import (
    PLATFORM_ID,
    route_slack_inbound_to_agent,
    slack_event_to_incoming,
)
from distr.core.integrations.slack.outbound import (
    post_slack_chat_message,
    slack_bot_token_from_env,
    start_slack_outbound_worker_background,
)
from distr.core.integrations.slack.rate_limit import SlackOutboundQueue
from distr.core.integrations.slack.signing import verify_slack_signature

__all__ = [
    "PLATFORM_ID",
    "SlackOutboundQueue",
    "post_slack_chat_message",
    "slack_bot_token_from_env",
    "slack_event_to_incoming",
    "route_slack_inbound_to_agent",
    "start_slack_outbound_worker_background",
    "verify_slack_signature",
]
