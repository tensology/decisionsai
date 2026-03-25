"""Shared utilities for the Telegram integration package."""

import hashlib
from datetime import datetime, timezone


def hash_channel_id(raw_channel_id) -> str:
    """Derive a daily-rotating MD5 hash from a raw channel/chat ID.

    Identical logic lives in the server's security.py so both sides
    produce the same hash independently.  The UTC date component means
    a captured hash is useless the next day.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return hashlib.md5(f"{raw_channel_id}:{today}".encode()).hexdigest()
