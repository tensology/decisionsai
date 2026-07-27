from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

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
