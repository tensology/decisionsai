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


def test_google_workspace_refresh_backoff_survives_new_connector_instances(monkeypatch):
    monkeypatch.setattr(
        GoogleWorkspaceConnector,
        "_google_refresh_retry_after_global",
        None,
        raising=False,
    )
    first = GoogleWorkspaceConnector.__new__(GoogleWorkspaceConnector)
    first.access_token = "expired-access-token"
    first.refresh_token = "bad-refresh-token"
    first.token_expires_at = datetime.utcnow() - timedelta(minutes=1)
    first.client_id = "client-id"
    first.client_secret = "client-secret"
    first._google_refresh_retry_after = None
    first._load_credentials = lambda: True

    def failed_refresh():
        retry_after = datetime.utcnow() + timedelta(minutes=10)
        first._google_refresh_retry_after = retry_after
        GoogleWorkspaceConnector._google_refresh_retry_after_global = retry_after
        return False

    first._refresh_access_token = failed_refresh
    assert first._ensure_valid_token() is False

    second = GoogleWorkspaceConnector.__new__(GoogleWorkspaceConnector)
    second.access_token = "expired-access-token"
    second.refresh_token = "bad-refresh-token"
    second.token_expires_at = datetime.utcnow() - timedelta(minutes=1)
    second.client_id = "client-id"
    second.client_secret = "client-secret"
    second._google_refresh_retry_after = None
    second._load_credentials = lambda: True

    called = {"count": 0}

    def refresh():
        called["count"] += 1
        return False

    second._refresh_access_token = refresh

    assert second._ensure_valid_token() is False
    assert called["count"] == 0
