"""Slack Events API request verification (TASK 17 — signing secret)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

logger = logging.getLogger(__name__)


def verify_slack_signature(
    *,
    signing_secret: str,
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    skew_seconds: int = 300,
) -> bool:
    """Verify ``X-Slack-Signature`` for raw POST body (Events API / interactivity).

    ``body`` must be the exact bytes Slack signed (no JSON re-serialization).
    """
    if not signing_secret or not timestamp_header or not signature_header:
        return False
    try:
        ts = int(float(timestamp_header))
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    if abs(now - ts) > skew_seconds:
        logger.warning("Slack signature rejected: timestamp outside skew window")
        return False
    try:
        body_text = body.decode("utf-8")
    except UnicodeDecodeError:
        body_text = body.decode("latin-1")
    basestring = f"v0:{timestamp_header}:{body_text}"
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    expected_sig = f"v0={digest}"
    if not signature_header.startswith("v0="):
        return False
    return hmac.compare_digest(expected_sig, signature_header)
