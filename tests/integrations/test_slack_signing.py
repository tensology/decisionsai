"""Slack Events API signing secret verification (TASK 17)."""

from __future__ import annotations

import hashlib
import hmac
import time

from distr.core.integrations.slack.signing import verify_slack_signature


def _sign(secret: str, body: bytes, ts: str) -> str:
    basestring = f"v0:{ts}:{body.decode('utf-8')}"
    digest = hmac.new(
        secret.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v0={digest}"


def test_verify_slack_signature_accepts_valid_request() -> None:
    secret = "signing-secret-test"
    body = b"token=abc&hello=world"
    ts = str(int(time.time()))
    sig = _sign(secret, body, ts)
    assert verify_slack_signature(
        signing_secret=secret,
        body=body,
        timestamp_header=ts,
        signature_header=sig,
    )


def test_verify_slack_signature_rejects_bad_hmac() -> None:
    secret = "signing-secret-test"
    body = b"x"
    ts = str(int(time.time()))
    bad = "v0=" + ("0" * 64)
    assert not verify_slack_signature(
        signing_secret=secret,
        body=body,
        timestamp_header=ts,
        signature_header=bad,
    )


def test_verify_slack_signature_rejects_stale_timestamp(monkeypatch) -> None:
    secret = "x"
    body = b"y"
    ts = "1000000000"
    sig = _sign(secret, body, ts)
    monkeypatch.setattr(time, "time", lambda: 1000000000 + 99999)
    assert not verify_slack_signature(
        signing_secret=secret,
        body=body,
        timestamp_header=ts,
        signature_header=sig,
        skew_seconds=300,
    )


def test_verify_slack_signature_missing_headers() -> None:
    assert not verify_slack_signature(
        signing_secret="s",
        body=b"",
        timestamp_header=None,
        signature_header="v0=abc",
    )
