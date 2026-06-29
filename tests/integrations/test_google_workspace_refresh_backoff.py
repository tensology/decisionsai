from datetime import datetime, timedelta

from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector


def test_google_workspace_refresh_failure_uses_cooldown(monkeypatch):
    connector = GoogleWorkspaceConnector.__new__(GoogleWorkspaceConnector)
    connector.access_token = "expired-access-token"
    connector.refresh_token = "bad-refresh-token"
    connector.token_expires_at = datetime.utcnow() - timedelta(minutes=1)
    connector.client_id = "client-id"
    connector.client_secret = "client-secret"
    connector._google_refresh_retry_after = datetime.utcnow() + timedelta(minutes=10)
    connector._load_credentials = lambda: True

    called = {"count": 0}

    def refresh():
        called["count"] += 1
        return False

    connector._refresh_access_token = refresh

    assert connector._ensure_valid_token() is False
    assert called["count"] == 0
