"""Seamless pre-expiry Telegram relay JWT refresh and transient ws-token retries."""

import base64
import json
from types import SimpleNamespace

import pytest

from distr.core.integrations.relay_auth import (
    RELAY_TOKEN_REFRESH_FALLBACK_MS,
    RELAY_TOKEN_REFRESH_MIN_DELAY_MS,
    WS_TOKEN_RETRY_DELAYS_S,
    relay_jwt_exp_iat,
    relay_token_refresh_delay_ms,
    should_retry_ws_token_status,
)
import distr.core.integrations.telegram.manager as manager_mod


class _Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or ("" if payload else "<html>404</html>")

    def json(self):
        return self._payload


def _bare_manager(**overrides):
    manager = manager_mod.TelegramWebSocketManager.__new__(manager_mod.TelegramWebSocketManager)
    manager.server_url = "wss://decisions.tensology.com/ws/telegram"
    manager.app_user_id = "local-ui"
    manager.telegram_user_id = 12345
    manager._connect_failure_reason = None
    manager._relay_endpoint_label = lambda: "decisions.tensology.com"
    manager._log_detailed = lambda message: None
    manager._active_disconnect = False
    manager._ws_token_request_id = 0
    manager._ws_token_fetch_in_progress = False
    manager._planned_token_refresh = False
    manager._pending_refresh_token = None
    manager._is_auto_reconnecting = False
    for key, value in overrides.items():
        setattr(manager, key, value)
    return manager


def test_should_retry_only_transient_http_statuses():
    assert should_retry_ws_token_status(404) is True
    assert should_retry_ws_token_status(502) is True
    assert should_retry_ws_token_status(503) is True
    assert should_retry_ws_token_status(200) is False
    assert should_retry_ws_token_status(401) is False
    assert should_retry_ws_token_status("404") is True
    assert WS_TOKEN_RETRY_DELAYS_S == (1, 2, 4, 8)


def test_fetch_ws_token_retries_404_then_succeeds(monkeypatch):
    slept = []
    responses = iter([
        _Response(404),
        _Response(503),
        _Response(200, {"token": "relay-jwt"}),
    ])
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(url)
        return next(responses)

    monkeypatch.setattr(manager_mod.requests, "post", fake_post)
    monkeypatch.setattr(manager_mod, "relay_internal_token", lambda: "internal")
    monkeypatch.setattr(manager_mod, "ws_token_retry_sleep", lambda seconds: slept.append(seconds))

    manager = _bare_manager()
    assert manager_mod.TelegramWebSocketManager._fetch_ws_token(manager) == "relay-jwt"
    assert calls == ["https://decisions.tensology.com/api/telegram/ws-token"] * 3
    assert slept == [1, 2]
    assert manager._connect_failure_reason is None


def test_fetch_ws_token_gives_up_after_five_tries(monkeypatch):
    slept = []
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(url)
        return _Response(404, text="<html>404</html>")

    monkeypatch.setattr(manager_mod.requests, "post", fake_post)
    monkeypatch.setattr(manager_mod, "relay_internal_token", lambda: "internal")
    monkeypatch.setattr(manager_mod, "ws_token_retry_sleep", lambda seconds: slept.append(seconds))

    manager = _bare_manager()
    assert manager_mod.TelegramWebSocketManager._fetch_ws_token(manager) is None
    assert len(calls) == 5
    assert slept == [1, 2, 4, 8]
    assert "HTTP 404" in manager._connect_failure_reason


def test_fetch_ws_token_does_not_retry_unauthorized(monkeypatch):
    slept = []
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(url)
        return _Response(401, text="nope")

    monkeypatch.setattr(manager_mod.requests, "post", fake_post)
    monkeypatch.setattr(manager_mod, "relay_internal_token", lambda: "internal")
    monkeypatch.setattr(manager_mod, "ws_token_retry_sleep", lambda seconds: slept.append(seconds))

    manager = _bare_manager()
    assert manager_mod.TelegramWebSocketManager._fetch_ws_token(manager) is None
    assert calls == ["https://decisions.tensology.com/api/telegram/ws-token"]
    assert slept == []
    assert "HTTP 401" in manager._connect_failure_reason


def test_planned_refresh_fetches_token_before_closing_socket(monkeypatch):
    started = []

    class DeferredThread:
        def __init__(self, *, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            started.append(self)

    class Socket:
        def __init__(self):
            self.closed = 0

        def isValid(self):
            return True

        def close(self):
            self.closed += 1

    monkeypatch.setattr(manager_mod.threading, "Thread", DeferredThread)
    manager = _bare_manager(
        socket=Socket(),
        _relay_token_refresh_timer=SimpleNamespace(stop=lambda: None, start=lambda *_a, **_k: None),
        _ws_token_ready_signal=SimpleNamespace(emit=lambda *a: None),
    )
    manager.is_connected = lambda: True
    manager._stop_relay_token_refresh = lambda: None
    manager._stop_socket_heartbeat = lambda: None
    logs = []
    manager._log_detailed = logs.append

    manager_mod.TelegramWebSocketManager._refresh_relay_ws_token(manager)

    assert manager._planned_token_refresh is True
    assert manager._ws_token_fetch_in_progress is True
    assert manager.socket.closed == 0
    assert started[0].name == "TelegramRelayTokenRefresh"
    assert logs == ["RELAY TOKEN REFRESH: fetching new JWT"]


def test_planned_refresh_failed_fetch_keeps_current_socket():
    retried = []
    manager = _bare_manager(
        socket=SimpleNamespace(isValid=lambda: True, close=lambda: (_ for _ in ()).throw(AssertionError("must not close"))),
        _planned_token_refresh=True,
        _relay_token_refresh_timer=SimpleNamespace(start=lambda ms: retried.append(ms)),
    )
    manager.is_connected = lambda: True
    logs = []
    manager._log_detailed = logs.append
    announced = []
    manager._announce_connect_failure = announced.append

    manager_mod.TelegramWebSocketManager._finish_planned_token_refresh(manager, "")

    assert manager._planned_token_refresh is False
    assert retried == [30_000]
    assert logs == ["RELAY TOKEN REFRESH: fetch failed, keeping current socket"]
    assert announced == []


def test_planned_refresh_disconnect_reopens_without_announcing():
    opened = []
    announced = []
    scheduled = []
    logs = []
    manager = _bare_manager(
        _planned_token_refresh=True,
        _pending_refresh_token="fresh-jwt",
        _active_disconnect=False,
        _outage_announced=False,
    )
    manager._stop_relay_token_refresh = lambda: None
    manager._release_sleep = lambda: None
    manager._stop_socket_heartbeat = lambda: None
    manager._log_detailed = logs.append
    manager._announce_connect_failure = announced.append
    manager._schedule_reconnect = scheduled.append
    manager._open_websocket_with_token = opened.append

    manager_mod.TelegramWebSocketManager._on_disconnected(manager)

    assert opened == ["fresh-jwt"]
    assert announced == []
    assert scheduled == []
    assert manager._planned_token_refresh is False
    assert manager._pending_refresh_token is None
    assert logs == ["RELAY TOKEN REFRESH: reopening with fresh JWT"]


def test_planned_refresh_socket_error_is_silent():
    logs = []
    announced = []
    scheduled = []
    manager = _bare_manager(_planned_token_refresh=True)
    manager.socket = SimpleNamespace(errorString=lambda: "The TLS/SSL connection has been closed")
    manager._log_detailed = logs.append
    manager._announce_connect_failure = announced.append
    manager._schedule_reconnect = scheduled.append
    manager._reconnect_timer = SimpleNamespace(isActive=lambda: False, start=lambda *_a: scheduled.append("timer"))

    manager_mod.TelegramWebSocketManager._on_error(manager, "RemoteHostClosedError")

    assert announced == []
    assert scheduled == []
    assert logs == ["SOCKET ERROR (planned refresh): The TLS/SSL connection has been closed (code=RemoteHostClosedError)"]


def test_finish_planned_refresh_closes_only_after_token_arrives():
    closed = []
    opened = []
    manager = _bare_manager(
        _planned_token_refresh=True,
        _ws_token_request_id=4,
        _ws_token_fetch_in_progress=True,
        socket=SimpleNamespace(isValid=lambda: True, close=lambda: closed.append(True)),
    )
    manager._stop_socket_heartbeat = lambda: None
    logs = []
    manager._log_detailed = logs.append
    manager._open_websocket_with_token = opened.append

    manager_mod.TelegramWebSocketManager._finish_ws_token_request(manager, 4, "fresh-jwt")

    assert closed == [True]
    assert opened == []
    assert manager._pending_refresh_token == "fresh-jwt"
    assert manager._planned_token_refresh is True
    assert logs == ["RELAY TOKEN REFRESH: swapping socket"]
    assert manager._ws_token_fetch_in_progress is False


def _unsigned_jwt(**claims: object) -> str:
    def _b64(obj: object) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}.sig"


def test_relay_jwt_exp_iat_returns_only_those_claims():
    token = _unsigned_jwt(exp=1_700_001_800, iat=1_700_000_000, sub="do-not-return")
    exp, iat = relay_jwt_exp_iat(token)
    assert exp == 1_700_001_800
    assert iat == 1_700_000_000
    assert exp - iat == 1800


def test_relay_jwt_exp_iat_garbage_is_none():
    assert relay_jwt_exp_iat("not-a-jwt") == (None, None)
    assert relay_jwt_exp_iat("") == (None, None)
    assert relay_jwt_exp_iat(None) == (None, None)  # type: ignore[arg-type]


def test_refresh_delay_is_expiry_minus_sixty_seconds():
    now = 1_700_000_000
    assert relay_token_refresh_delay_ms(now + 1800, now=now) == 1_740_000
    assert relay_token_refresh_delay_ms(now + 300, now=now) == 240_000
    assert relay_token_refresh_delay_ms(None, now=now) == RELAY_TOKEN_REFRESH_FALLBACK_MS
    assert relay_token_refresh_delay_ms(now - 10, now=now) == RELAY_TOKEN_REFRESH_MIN_DELAY_MS


def test_schedule_relay_token_refresh_reads_exp_not_hardcoded_four_minutes(monkeypatch):
    monkeypatch.setattr("distr.core.integrations.relay_auth.time.time", lambda: 1_700_000_000)
    started = []
    logs = []
    manager = _bare_manager(_relay_token_exp=1_700_001_800.0)
    manager._relay_token_refresh_timer = SimpleNamespace(start=started.append)
    manager._log_detailed = logs.append
    manager_mod.TelegramWebSocketManager._schedule_relay_token_refresh(manager)
    assert started == [1_740_000]
    assert logs == ["RELAY TOKEN REFRESH: scheduled in 1740s"]


def test_remember_relay_token_expiry_feeds_the_refresh_timer(monkeypatch):
    monkeypatch.setattr("distr.core.integrations.relay_auth.time.time", lambda: 1_700_000_000)
    started = []
    manager = _bare_manager()
    manager._relay_token_refresh_timer = SimpleNamespace(start=started.append)
    token = _unsigned_jwt(exp=1_700_001_800, iat=1_700_000_000)
    manager_mod.TelegramWebSocketManager._remember_relay_token_expiry(manager, token)
    manager_mod.TelegramWebSocketManager._schedule_relay_token_refresh(manager)
    assert manager._relay_token_exp == 1_700_001_800
    assert started == [1_740_000]
