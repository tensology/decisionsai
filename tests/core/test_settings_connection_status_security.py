from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from urllib.parse import parse_qs, urlparse

from distr.gui.web.routes.settings import advanced


def test_connection_status_never_returns_connected_account_secrets(monkeypatch):
    accounts = [
        {
            "provider": "jira",
            "name": "Jira",
            "server_url": "https://example.atlassian.net",
            "email": "person@example.com",
            "api_token": "jira-secret",
            "is_valid": True,
        },
        {
            "provider": "trello",
            "name": "Trello",
            "api_key": "trello-key",
            "api_token": "trello-secret",
            "is_valid": True,
        },
    ]
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"connected_accounts": accounts},
    )

    router = APIRouter()
    advanced.register_routes(router, None)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    response = TestClient(app).get("/api/advanced/connection-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["jira_has_valid"] is True
    assert payload["trello_has_valid"] is True
    rendered = response.text
    assert "jira-secret" not in rendered
    assert "trello-secret" not in rendered
    assert "trello-key" not in rendered
    assert "api_token" not in payload["jira_accounts"][0]
    assert "api_token" not in payload["trello_accounts"][0]
    assert "api_key" not in payload["trello_accounts"][0]
    assert payload["jira_accounts"][0]["has_api_token"] is True
    assert payload["trello_accounts"][0]["has_api_token"] is True


def test_google_disconnect_removes_only_google_tokens_and_preserves_oauth_config(monkeypatch, tmp_path):
    oauth_config = tmp_path / "google-oauth-client.json"
    oauth_config.write_text('{"installed": {"client_id": "test"}}', encoding="utf-8")
    accounts = [
        {"provider": "google", "name": "Google", "refresh_token": "google-secret"},
        {"provider": "jira", "name": "Jira", "api_token": "jira-secret"},
    ]
    saved = {}

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"connected_accounts": accounts},
    )
    monkeypatch.setattr(
        "distr.core.settings.save_settings_to_db",
        lambda payload: saved.update(payload),
    )
    monkeypatch.setattr("distr.core.paths.GOOGLE_OAUTH_SECRET_PATH", str(oauth_config))

    router = APIRouter()
    advanced.register_routes(router, None)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    response = TestClient(app).post("/api/advanced/google/disconnect")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert saved["connected_accounts"] == [accounts[1]]
    assert oauth_config.is_file()


def test_google_connection_status_requires_healthy_refresh(monkeypatch):
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"connected_accounts": [{"provider": "google", "access_token": "expired"}]},
    )
    monkeypatch.setattr("distr.gui.web.oauth.load_google_oauth_config", lambda: {"web": {"client_id": "client"}})
    monkeypatch.setattr(
        "distr.core.agent.services.integrations.google_workspace.GoogleWorkspaceConnector.connection_health",
        lambda self: (False, "Google authentication expired. Reconnect the account."),
    )

    router = APIRouter()
    advanced.register_routes(router, None)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    payload = TestClient(app).get("/api/advanced/connection-status").json()

    assert payload["google_connected"] is False
    assert payload["google_error"] == "Google authentication expired. Reconnect the account."


def test_google_oauth_returns_to_launching_page_and_preserves_refresh_token(monkeypatch):
    accounts = [{"provider": "google", "access_token": "old-access", "refresh_token": "keep-refresh"}]
    saved = {}
    monkeypatch.setattr(
        "distr.gui.web.oauth.load_google_oauth_config",
        lambda: {"web": {"client_id": "client-id", "client_secret": "client-secret"}},
    )
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {"connected_accounts": accounts})
    monkeypatch.setattr("distr.core.settings.save_settings_to_db", lambda payload: saved.update(payload))

    class TokenResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"access_token": "new-access", "expires_in": 3600, "token_type": "Bearer"}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: TokenResponse())
    router = APIRouter()
    advanced.register_routes(router, None)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    oauth = client.get(
        "/api/advanced/google/oauth-url",
        params={"return_to": "/development/incoming/"},
    ).json()
    state = parse_qs(urlparse(oauth["url"]).query)["state"][0]
    callback = client.get(
        "/api/advanced/google/callback",
        params={"code": "google-code", "state": state},
        follow_redirects=False,
    )

    assert callback.status_code == 302
    assert callback.headers["location"] == "/development/incoming/?google=connected"
    assert saved["connected_accounts"][0]["access_token"] == "new-access"
    assert saved["connected_accounts"][0]["refresh_token"] == "keep-refresh"


def test_whatsapp_connect_starts_pairing_through_relay(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 202

        @staticmethod
        def json():
            return {"status": "connecting", "qr_code": None}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(advanced, "_whatsapp_relay_base_url", lambda: "https://relay.example/api/whatsapp")
    monkeypatch.setattr(advanced, "_relay_headers", lambda: {"X-Relay-Internal-Token": "test-token"})

    router = APIRouter()
    advanced.register_routes(router, None)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    response = TestClient(app).post("/api/advanced/whatsapp/connect")

    assert response.status_code == 202
    assert response.json() == {"status": "connecting", "qr_code": None}
    assert captured == {
        "timeout": 8.0,
        "url": "https://relay.example/api/whatsapp/connect",
        "headers": {"X-Relay-Internal-Token": "test-token"},
    }
