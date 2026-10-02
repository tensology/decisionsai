import base64
import threading
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from distr.core.integrations.whatsapp.manager import WhatsAppWebSocketManager


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, *values):
        self.values.append(values)


class _Socket:
    def __init__(self):
        self.opened = []

    def errorString(self):
        return "HTTP 404"

    def isValid(self):
        return False

    def open(self, url):
        self.opened.append(url)


class _Response:
    def __init__(self, status_code, payload=None, *, invalid_json=False):
        self.status_code = status_code
        self._payload = payload
        self._invalid_json = invalid_json
        self.text = "not found" if invalid_json else ""

    def json(self):
        if self._invalid_json:
            raise ValueError("invalid JSON")
        return self._payload


def test_invalid_internal_auth_response_falls_back_to_device_authentication(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    responses = iter([
        _Response(404, invalid_json=True),
        _Response(200, {"success": True, "challenge_id": "challenge", "challenge_message": "sign-me"}),
        _Response(200, {"success": True, "ws_token": "device-token"}),
    ])
    urls = []

    def post(url, **_kwargs):
        urls.append(url)
        return next(responses)

    monkeypatch.setattr("requests.post", post)
    manager = SimpleNamespace(
        api_base="https://example.test/api/whatsapp",
        _collect_subscribe_phones=lambda: [],
        _relay_auth_headers=lambda _payload: {"X-Relay-Internal-Token": "secret"},
        _load_or_create_device_identity=lambda: {
            "device_id": "device-id",
            "private_key": base64.b64encode(private_bytes).decode(),
        },
        _ws_auth_bundle=None,
    )

    bundle = WhatsAppWebSocketManager._request_ws_auth_bundle(manager)

    assert bundle["ws_token"] == "device-token"
    assert urls == [
        "https://example.test/api/whatsapp/ws-auth",
        "https://example.test/api/whatsapp/device/challenge",
        "https://example.test/api/whatsapp/device/ws-auth",
    ]


def test_connect_worker_does_not_open_socket_without_authentication():
    signal = _Signal()
    manager = SimpleNamespace(
        _request_ws_auth_bundle=lambda: None,
        _open_socket_requested=signal,
        _connect_lock=threading.Lock(),
        _connect_worker_running=True,
    )

    WhatsAppWebSocketManager._connect_worker(manager)

    assert signal.values == [("",)]
    assert manager._connect_worker_running is False


def test_authentication_failure_uses_reconnect_backoff_instead_of_opening_socket():
    socket = _Socket()
    status = _Signal()
    scheduled = []
    manager = SimpleNamespace(
        socket=socket,
        _active_disconnect=False,
        connection_status_changed=status,
        _schedule_reconnect=scheduled.append,
    )

    WhatsAppWebSocketManager._open_socket_on_main_thread(manager, "")

    assert socket.opened == []
    assert status.values == [(False, "Reconnecting...")]
    assert scheduled == ["WhatsApp"]


def test_socket_error_uses_exponential_reconnect_scheduler():
    scheduled = []
    manager = SimpleNamespace(
        socket=_Socket(),
        _active_disconnect=False,
        _schedule_reconnect=scheduled.append,
    )

    WhatsAppWebSocketManager._on_error(manager, "connection-refused")

    assert scheduled == ["WhatsApp"]
