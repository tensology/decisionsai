"""Shared utilities for the Telegram integration package."""

import hashlib
import hmac
import os
from datetime import datetime, timezone
from pathlib import Path


def env_file_value(name: str) -> str:
    """Read a single DecisionsAI .env value without requiring shell export."""
    env_path = Path(__file__).resolve().parents[4] / ".env"
    try:
        for line in env_path.read_text().splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            if key.strip() == name:
                return value.strip().strip("'\"")
    except OSError:
        return ""
    return ""


def relay_internal_token() -> str:
    """Relay auth token from process env, falling back to the project .env file."""
    return (os.getenv("RELAY_INTERNAL_TOKEN") or env_file_value("RELAY_INTERNAL_TOKEN") or "").strip()


def remote_channel_secret() -> str:
    return (
        os.getenv("DECISIONSAI_REMOTE_CHANNEL_SECRET")
        or env_file_value("DECISIONSAI_REMOTE_CHANNEL_SECRET")
        or os.getenv("RELAY_INTERNAL_TOKEN")
        or env_file_value("RELAY_INTERNAL_TOKEN")
        or os.getenv("DECISIONSAI_HMAC_SECRET")
        or env_file_value("DECISIONSAI_HMAC_SECRET")
        or ""
    ).strip()


def hash_channel_id(raw_channel_id) -> str:
    """Derive a daily-rotating keyed channel ID from a raw channel/chat ID.

    Identical logic lives in the relay. The key prevents channel guessing,
    while the UTC date keeps captured channel IDs short-lived.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    secret = remote_channel_secret()
    if not secret:
        # Local development fallback only; production should configure a shared secret.
        secret = "decisionsai-local-remote-channel"
    return hmac.new(secret.encode(), f"{raw_channel_id}:{today}".encode(), hashlib.sha256).hexdigest()
