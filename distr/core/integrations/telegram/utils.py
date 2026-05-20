"""Shared utilities for the Telegram integration package."""

import hashlib
import hmac
import os
from datetime import datetime, timezone


def hash_channel_id(raw_channel_id) -> str:
    """Derive a daily-rotating keyed channel ID from a raw channel/chat ID.

    Identical logic lives in the relay. The key prevents channel guessing,
    while the UTC date keeps captured channel IDs short-lived.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    secret = (
        os.getenv("DECISIONSAI_REMOTE_CHANNEL_SECRET")
        or os.getenv("RELAY_INTERNAL_TOKEN")
        or os.getenv("DECISIONSAI_HMAC_SECRET")
        or ""
    ).strip()
    if not secret:
        # Local development fallback only; production should configure a shared secret.
        secret = "decisionsai-local-remote-channel"
    return hmac.new(secret.encode(), f"{raw_channel_id}:{today}".encode(), hashlib.sha256).hexdigest()
