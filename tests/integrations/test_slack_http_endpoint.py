"""HTTP wiring for Slack Events API → MessageBus (TASK 17)."""

from __future__ import annotations

from typing import Any

import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes.integrations_hooks import router


def _sign(secret: str, body: bytes, ts: str) -> str:
    basestring = f"v0:{ts}:{body.decode('utf-8')}"
    digest = hmac.new(
        secret.encode("utf-8"),
        basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v0={digest}"


@pytest.fixture()
def slack_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DECISIONSAI_SLACK_SIGNING_SECRET", "test-signing-secret")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_slack_url_verification(slack_client: TestClient) -> None:
    payload = {"type": "url_verification", "challenge": "done_challenge_xyz"}
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    sig = _sign("test-signing-secret", body, ts)
    r = slack_client.post(
        "/hooks/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )
    assert r.status_code == 200
    assert r.json() == {"challenge": "done_challenge_xyz"}


def test_slack_invalid_signature(slack_client: TestClient) -> None:
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode("utf-8")
    ts = str(int(time.time()))
    r = slack_client.post(
        "/hooks/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": "v0=" + ("a" * 64),
        },
    )
    assert r.status_code == 401


def test_slack_not_configured_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECISIONSAI_SLACK_SIGNING_SECRET", raising=False)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    r = client.post("/hooks/slack/events", content=b"{}")
    assert r.status_code == 503


def test_slack_message_event_calls_router(monkeypatch: pytest.MonkeyPatch, slack_client: TestClient) -> None:
    seen: list[dict[str, Any]] = []

    def capture(**kw: Any) -> None:
        seen.append(kw)

    monkeypatch.setattr(
        "distr.gui.web.routes.integrations_hooks.route_slack_inbound_to_agent",
        capture,
    )

    payload = {
        "token": "ignored",
        "team_id": "T1",
        "type": "event_callback",
        "event": {
            "type": "message",
            "channel": "C123",
            "user": "U456",
            "text": "ping slack hook",
            "ts": "1.2",
            "channel_type": "channel",
        },
        "event_id": "Ev1",
        "event_time": int(time.time()),
    }
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    sig = _sign("test-signing-secret", body, ts)
    r = slack_client.post(
        "/hooks/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(seen) == 1
    assert seen[0]["channel_id"] == "C123"
    assert seen[0]["user_id"] == "U456"
    assert seen[0]["text"] == "ping slack hook"


def test_slack_bot_message_skipped(monkeypatch: pytest.MonkeyPatch, slack_client: TestClient) -> None:
    seen: list[dict[str, Any]] = []

    def capture(**kw: Any) -> None:
        seen.append(kw)

    monkeypatch.setattr(
        "distr.gui.web.routes.integrations_hooks.route_slack_inbound_to_agent",
        capture,
    )

    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "subtype": "bot_message",
            "channel": "C123",
            "text": "beep",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    sig = _sign("test-signing-secret", body, ts)
    r = slack_client.post(
        "/hooks/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
        },
    )
    assert r.status_code == 200
    assert seen == []
