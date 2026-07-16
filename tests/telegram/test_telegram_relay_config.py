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
    manager._relay_endpoint_label = lambda: "www.decisionsai.net"
    detailed: list[str] = []
    manager._log_detailed = detailed.append

    manager_mod.TelegramWebSocketManager._open_websocket_with_token(
        manager,
        "secret-relay-jwt",
    )

    assert "secret-relay-jwt" in manager.socket.opened_url
    assert detailed == ["CONNECTING: www.decisionsai.net"]
    assert all("secret-relay-jwt" not in row for row in detailed)


def test_runtime_connect_fetches_relay_token_off_qt_thread(monkeypatch) -> None:
    import distr.core.integrations.telegram.manager as manager_mod

    class Socket:
        def isValid(self):
            return False

    started = []
    fetch_calls = []

    class DeferredThread:
        def __init__(self, *, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            started.append(self)

    monkeypatch.setattr(manager_mod.threading, "Thread", DeferredThread)
    manager = manager_mod.TelegramWebSocketManager.__new__(manager_mod.TelegramWebSocketManager)
    manager.socket = Socket()
    manager.server_url = "wss://www.decisionsai.net/ws/telegram"
    manager.app_user_id = "local-ui"
    manager.telegram_user_id = 12345
    manager.short_code = None
    manager._active_disconnect = False
    manager._ws_token_request_id = 0
    manager._ws_token_fetch_in_progress = False
    manager._fetch_ws_token = lambda: fetch_calls.append(True) or "relay-jwt"

    manager_mod.TelegramWebSocketManager.connect(manager)

    assert fetch_calls == []
    assert manager._ws_token_fetch_in_progress is True
    assert manager._ws_token_request_id == 1
    assert len(started) == 1
    assert started[0].name == "TelegramRelayToken"


def test_stale_relay_token_completion_cannot_open_socket() -> None:
    import distr.core.integrations.telegram.manager as manager_mod

    opened = []
    manager = manager_mod.TelegramWebSocketManager.__new__(manager_mod.TelegramWebSocketManager)
    manager._ws_token_request_id = 3
    manager._ws_token_fetch_in_progress = True
    manager._open_websocket_with_token = opened.append

    manager_mod.TelegramWebSocketManager._finish_ws_token_request(manager, 2, "stale")
    assert opened == []
    assert manager._ws_token_fetch_in_progress is True

    manager_mod.TelegramWebSocketManager._finish_ws_token_request(manager, 3, "fresh")
    assert opened == ["fresh"]
    assert manager._ws_token_fetch_in_progress is False


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
