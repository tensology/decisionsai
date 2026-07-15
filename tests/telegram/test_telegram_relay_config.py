import hashlib
import hmac
from datetime import datetime, timezone


def test_websocket_connect_keeps_tls_verification_and_does_not_log_jwt() -> None:
    import distr.core.integrations.telegram.manager as manager_mod

    class Socket:
        opened_url = None

        def isValid(self):
            return False

        def open(self, url):
            self.opened_url = url.toString()

        def setSslConfiguration(self, _config):
            raise AssertionError("connect must not weaken the platform TLS configuration")

    manager = manager_mod.TelegramWebSocketManager.__new__(manager_mod.TelegramWebSocketManager)
    manager.socket = Socket()
    manager.server_url = "wss://www.decisionsai.net/ws/telegram"
    manager.app_user_id = "local-ui"
    manager.telegram_user_id = 12345
    manager.short_code = None
    manager._active_disconnect = False
    manager._connect_failure_reason = None
    manager._fetch_ws_token = lambda: "secret-relay-jwt"
    manager._relay_endpoint_label = lambda: "www.decisionsai.net"
    detailed: list[str] = []
    manager._log_detailed = detailed.append

    manager_mod.TelegramWebSocketManager.connect(manager)

    assert "secret-relay-jwt" in manager.socket.opened_url
    assert detailed == ["CONNECTING: www.decisionsai.net"]
    assert all("secret-relay-jwt" not in row for row in detailed)


def test_ws_token_request_uses_env_file_relay_token_when_process_env_is_missing(monkeypatch):
    import distr.core.integrations.telegram.manager as manager_mod

    captured = {}

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"token": "relay-jwt"}

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.delenv("RELAY_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr(
        manager_mod,
        "relay_internal_token",
        lambda: "token-from-env-file",
        raising=False,
    )
    monkeypatch.setattr(manager_mod.requests, "post", fake_post)

    manager = manager_mod.TelegramWebSocketManager.__new__(manager_mod.TelegramWebSocketManager)
    manager.server_url = "wss://www.decisionsai.net/ws/telegram"
    manager.app_user_id = "local-ui"
    manager.telegram_user_id = 12345
    manager._connect_failure_reason = None
    manager._relay_endpoint_label = lambda: "www.decisionsai.net"
    manager._log_detailed = lambda message: None

    assert manager_mod.TelegramWebSocketManager._fetch_ws_token(manager) == "relay-jwt"
    assert captured["headers"]["X-Relay-Internal-Token"] == "token-from-env-file"
    assert captured["json"] == {"app_user_id": "local-ui", "telegram_user_id": 12345}


def test_remote_channel_hash_uses_env_file_secret_when_process_env_is_missing(monkeypatch):
    import distr.core.integrations.telegram.utils as telegram_utils

    monkeypatch.delenv("DECISIONSAI_REMOTE_CHANNEL_SECRET", raising=False)
    monkeypatch.delenv("RELAY_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("DECISIONSAI_HMAC_SECRET", raising=False)
    monkeypatch.setattr(
        telegram_utils,
        "env_file_value",
        lambda name: {"RELAY_INTERNAL_TOKEN": "token-from-env-file"}.get(name, ""),
        raising=False,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    expected = hmac.new(
        b"token-from-env-file",
        f"12345:{today}".encode(),
        hashlib.sha256,
    ).hexdigest()

    assert telegram_utils.hash_channel_id(12345) == expected


def test_whatsapp_relay_headers_use_env_file_token_when_process_env_is_missing(monkeypatch):
    import distr.core.integrations.whatsapp.manager as whatsapp_mod

    monkeypatch.delenv("RELAY_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr(
        whatsapp_mod,
        "relay_internal_token",
        lambda: "token-from-env-file",
        raising=False,
    )

    manager = whatsapp_mod.WhatsAppWebSocketManager.__new__(whatsapp_mod.WhatsAppWebSocketManager)
    manager._ws_auth_bundle = {}

    assert manager._relay_auth_headers("") == {"X-Relay-Internal-Token": "token-from-env-file"}
