"""Auth-gap regression: board API, Slack hook signing, read-reply speech routes.

Run with DECISIONS_DB_DIR and HOME pointing at a /tmp copy.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-authgap-0123456789abcdef0123456789"
SECRET = "test-slack-signing-secret"
H = {"X-DecisionsAI-Internal-Token": TOKEN}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", TOKEN)
    import distr.gui.web.routes.integrations_hooks as hooks
    monkeypatch.setattr(hooks, "_slack_signing_secret", lambda: SECRET)
    monkeypatch.setattr(hooks, "route_slack_inbound_to_agent", lambda **k: None)
    hooks._seen_slack_signatures.clear()
    import distr.core.read_response as rr
    calls = []
    monkeypatch.setattr(rr, "speak_spoken_text", lambda text: calls.append(text) or True)
    from distr.gui.web.server import create_app
    c = TestClient(create_app(), client=("127.0.0.1", 50000))
    c.speak_calls = calls
    return c


@pytest.mark.parametrize("method,path", [
    ("GET", "/board/api/board"),
    ("GET", "/board/api/tickets/1"),
    ("POST", "/board/api/tickets"),
    ("PUT", "/board/api/columns/1"),
    ("DELETE", "/board/api/tickets/1"),
])
def test_board_api_requires_token(client, method, path):
    r = client.request(method, path + "?project_id=1", json={})
    assert r.status_code == 401


def test_board_api_read_with_token_ok(client):
    r = client.get("/board/api/board?project_id=1", headers=H)
    assert r.status_code != 401
    assert r.status_code in (200, 404, 500)  # auth passed; data depends on tmp DB


def _sign(body: bytes, ts: str, secret: str = SECRET) -> str:
    return "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()


def test_slack_valid_signature(client):
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    ts = str(int(time.time()))
    r = client.post("/hooks/slack/events", content=body,
                    headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": _sign(body, ts)})
    assert r.status_code == 200 and r.json() == {"challenge": "abc"}


def test_slack_invalid_signature(client):
    body = b'{"type":"url_verification","challenge":"abc"}'
    ts = str(int(time.time()))
    r = client.post("/hooks/slack/events", content=body,
                    headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": _sign(body, ts, "wrong")})
    assert r.status_code == 401
    r = client.post("/hooks/slack/events", content=body)
    assert r.status_code == 401


def test_slack_stale_and_replayed(client):
    body = b'{"type":"url_verification","challenge":"abc"}'
    old = str(int(time.time()) - 301)
    r = client.post("/hooks/slack/events", content=body,
                    headers={"X-Slack-Request-Timestamp": old, "X-Slack-Signature": _sign(body, old)})
    assert r.status_code == 401
    ts = str(int(time.time()))
    hdr = {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": _sign(body, ts)}
    assert client.post("/hooks/slack/events", content=body, headers=hdr).status_code == 200
    assert client.post("/hooks/slack/events", content=body, headers=hdr).status_code == 401


def test_slack_no_secret_rejects(client, monkeypatch):
    import distr.gui.web.routes.integrations_hooks as hooks
    monkeypatch.setattr(hooks, "_slack_signing_secret", lambda: "")
    body = b'{"type":"url_verification","challenge":"abc"}'
    ts = str(int(time.time()))
    r = client.post("/hooks/slack/events", content=body,
                    headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": _sign(body, ts)})
    assert r.status_code >= 400 and r.status_code != 200


def test_speech_routes_require_token(client):
    assert client.post("/api/chats/speak-spoken", json={"text": "hi"}).status_code == 401
    assert client.post("/api/chats/1/read-response", json={"text": "hi"}).status_code == 401
    assert client.speak_calls == []


def test_speech_route_with_token_uses_mock(client):
    r = client.post("/api/chats/speak-spoken", json={"text": "hi"}, headers=H)
    assert r.status_code == 200
    assert client.speak_calls == ["hi"]


def test_per_route_dependency_present():
    from distr.gui.web.server import create_app
    from distr.gui.web.security import require_internal_token_dependency
    app = create_app()
    for p in ("/api/chats/speak-spoken", "/api/chats/{chat_id}/read-response"):
        route = next(r for r in app.routes if getattr(r, "path", None) == p)
        assert any(d.call is require_internal_token_dependency for d in route.dependant.dependencies)
