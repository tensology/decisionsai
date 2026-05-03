"""Slack outbound text via ``chat.postMessage`` (bot token) + retry worker (TASK 17)."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from threading import Lock

from distr.core.integrations.outbound_state import get_slack_outbound_queue
from distr.core.integrations.outbound_worker import IntegrationOutboundWorker

logger = logging.getLogger(__name__)

_ENV_BOT_TOKEN = "DECISIONSAI_SLACK_BOT_TOKEN"

_slack_worker_lock = Lock()
_slack_worker_started = False


def slack_bot_token_from_env() -> str | None:
    from distr.core.integrations.token_resolve import resolve_slack_bot_token

    return resolve_slack_bot_token()


def post_slack_chat_message(*, bot_token: str, channel_id: str, text: str) -> None:
    """POST ``chat.postMessage``. Raises on transport or Slack ``ok: false``."""
    body = json.dumps(
        {"channel": channel_id, "text": text[:40000]},
        separators=(",", ":"),
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=body,
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"slack_http_{e.code}") from e
    except OSError as e:
        raise RuntimeError(f"slack_transport_{e}") from e

    payload = json.loads(raw)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "slack_api_error")


def start_slack_outbound_worker_background() -> bool:
    """Start a daemon worker draining :func:`~distr.core.integrations.outbound_state.get_slack_outbound_queue`.

    Idempotent. Requires ``DECISIONSAI_SLACK_BOT_TOKEN``.
    """

    global _slack_worker_started
    token = slack_bot_token_from_env()
    if not token:
        return False

    with _slack_worker_lock:
        if _slack_worker_started:
            return True
        _slack_worker_started = True

    def deliver(item: dict) -> None:
        cid = str(item.get("channel_id") or "").strip()
        txt = (item.get("text") or "").strip()
        if not cid or not txt:
            return
        post_slack_chat_message(bot_token=token, channel_id=cid, text=txt)

    worker = IntegrationOutboundWorker(
        get_slack_outbound_queue(),
        deliver,
        thread_name="slack-outbound",
    )
    worker.start_daemon()
    logger.info("Slack outbound worker started (%s)", _ENV_BOT_TOKEN)
    return True


def reset_slack_outbound_worker_state_for_tests() -> None:
    global _slack_worker_started
    with _slack_worker_lock:
        _slack_worker_started = False
