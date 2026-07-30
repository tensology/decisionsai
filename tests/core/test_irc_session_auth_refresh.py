from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes import irc


ROOT = Path(__file__).resolve().parents[2]


class _Response:
    def __init__(self, status_code: int, data: dict):
        self.status_code = status_code
        self._data = data
        self.text = data.get("detail", "")

    def json(self) -> dict:
        return self._data


class _RelayClient:
    calls: list[dict] = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url: str, *, headers: dict, json: dict) -> _Response:
        self.calls.append({"url": url, "headers": headers, "json": json})
        if url.endswith("/api/telegram/ws-token"):
            return _Response(200, {"token": "fresh-bridge-token"})
        if headers.get("Authorization") == "Bearer stale-chat-token":
            return _Response(403, {"detail": "Unauthorized"})
        return _Response(
            200,
            {
                "token": "fresh-chat-token",
                "user": {"id": "user-1", "display_name": "Paulie Pie 2"},
                "rooms": [],
                "default_room": {"slug": "decisions-ai"},
            },
        )


def test_stale_chat_token_is_refreshed_before_session_join(monkeypatch) -> None:
    _RelayClient.calls = []
    monkeypatch.setattr(irc.httpx, "AsyncClient", _RelayClient)
    monkeypatch.setattr(irc, "_relay_base", lambda: "https://relay.example")
    monkeypatch.setattr(
        irc, "_relay_internal_headers", lambda: {"X-Relay-Internal-Token": "internal"}
    )
    monkeypatch.setattr(irc, "_relay_ws_url", lambda: "wss://relay.example/ws/chat")

    app = FastAPI()
    app.include_router(irc.create_routes(), prefix="/api")

    response = TestClient(app).post(
        "/api/irc/session",
        headers={"Authorization": "Bearer stale-chat-token"},
        json={
            "display_name": "Paulie Pie 2",
            "client_id": "browser-client",
            "update_display_name": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["token"] == "fresh-chat-token"
    assert response.json()["ws_url"] == "wss://relay.example/ws/chat"
    assert [call["url"].rsplit("/", 2)[-2:] for call in _RelayClient.calls] == [
        ["chat", "session"],
        ["telegram", "ws-token"],
        ["chat", "session"],
    ]
    assert _RelayClient.calls[-1]["headers"]["Authorization"] == "Bearer fresh-bridge-token"


def test_browser_discards_rejected_cached_chat_token_and_retries() -> None:
    script = (
        ROOT / "distr/gui/web/static/irc/js/irc.js"
    ).read_text(encoding="utf-8")

    assert "if (state.token && /unauthorized/i.test(message))" in script
    assert 'state.token = ""' in script
    assert "localStorage.removeItem(tokenKey)" in script
    assert "startSession(name, adminCode, updateDisplayName)" in script
