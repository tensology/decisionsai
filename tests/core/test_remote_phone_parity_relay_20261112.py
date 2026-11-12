"""Phone parity relay (12 Nov 2026): multipart project-file upload + longer read/refresh budgets.

Every HTTP call is mocked; nothing reaches Decisions, Tensology or any channel.
"""
import base64
import importlib.util
import sys
import threading
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

_PATH = Path(__file__).resolve().parents[2] / "distr" / "core" / "integrations" / "telegram" / "remote_control.py"
_pkg = types.ModuleType("distr.core.integrations.telegram")
_pkg.__path__ = []
_utils = types.ModuleType("distr.core.integrations.telegram.utils")
_utils.hash_channel_id = lambda value: f"hash_{value}"
_utils.relay_internal_token = lambda: "test-token"
_KEYS = ("distr.core.integrations.telegram", "distr.core.integrations.telegram.utils")
_prior = {k: sys.modules[k] for k in _KEYS if k in sys.modules}
try:
    sys.modules[_KEYS[0]] = _pkg
    sys.modules[_KEYS[1]] = _utils
    _spec = importlib.util.spec_from_file_location("remote_control_phone_parity_test", _PATH)
    rc = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(rc)
finally:
    for _k in _KEYS:
        if _k in _prior:
            sys.modules[_k] = _prior[_k]
        else:
            sys.modules.pop(_k, None)


class _Host(rc.TelegramRemoteControlMixin):
    def __init__(self):
        self._remote_control_lock = threading.Lock()
        self.responses = []

    def _send_websocket_message(self, message):
        self.responses.append(message)


@pytest.fixture
def http(monkeypatch):
    ok = Mock(status_code=200)
    ok.json.return_value = {"success": True, "id": 5, "filename": "notes.txt"}
    calls = {"post": Mock(return_value=ok), "get": Mock(return_value=ok)}
    monkeypatch.setattr("requests.post", calls["post"])
    monkeypatch.setattr("requests.get", calls["get"])
    monkeypatch.setattr("distr.gui.web.server.get_unified_server", lambda: None)
    monkeypatch.setattr("distr.gui.web.security.get_internal_api_token", lambda: "fixture-token")
    return calls


def _relay(host, data):
    host._dispatch_api_relay({"command": "api_relay", "request_id": "r1", "data": data})
    return host.responses[-1]


def test_project_file_upload_is_rebuilt_as_multipart(http):
    from distr.gui.web.security import INTERNAL_AUTH_HEADER
    host = _Host()
    out = _relay(host, {"method": "POST", "path": "/api/projects/7/files", "body": None, "multipart": {
        "field": "file", "filename": "../../etc/notes.txt", "content_type": "text/plain",
        "data_base64": base64.b64encode(b"hello").decode(), "fields": {"description": " spec "}}})
    assert out["data"] == {"status": 200, "response": {"success": True, "id": 5, "filename": "notes.txt"}}
    args, kwargs = http["post"].call_args
    assert args == ("http://127.0.0.1:8765/api/projects/7/files",)
    assert kwargs["files"] == {"file": ("notes.txt", b"hello", "text/plain")}
    assert kwargs["data"] == {"description": "spec"}
    assert kwargs["timeout"] == 28
    assert kwargs["headers"] == {INTERNAL_AUTH_HEADER: "fixture-token"}  # no JSON content-type
    assert "json" not in kwargs


@pytest.mark.parametrize("path,method,multipart,message", [
    ("/api/advanced/files/upload", "POST", {"data_base64": "aGk="}, "only available for project files"),
    ("/api/projects/7/files/1", "POST", {"data_base64": "aGk="}, "only available for project files"),
    ("/api/projects/7/files", "PUT", {"data_base64": "aGk="}, "only available for project files"),
    ("/api/projects/7/files", "POST", {"data_base64": "not base64!"}, "could not be read"),
    ("/api/projects/7/files", "POST", {"data_base64": ""}, "empty"),
    ("/api/projects/7/files", "POST", {"field": "other", "data_base64": "aGk="}, "not valid"),
    ("/api/projects/7/files", "POST", {"data_base64": "A" * ((rc.RELAY_MULTIPART_MAX_BYTES * 4) // 3 + 12)}, "larger than 8 MB"),
])
def test_multipart_fails_closed(http, path, method, multipart, message):
    host = _Host()
    out = _relay(host, {"method": method, "path": path, "multipart": multipart})
    assert message in out["error"]
    http["post"].assert_not_called()


def test_read_and_forced_refresh_get_longer_budgets(http):
    host = _Host()
    _relay(host, {"method": "POST", "path": "/api/workflows/studio/incoming/proposals/read", "body": {"text": "x"}})
    assert http["post"].call_args.kwargs["timeout"] == 28
    _relay(host, {"method": "GET", "path": "/api/workflows/studio/incoming/proposals?limit=100&refresh=1"})
    assert http["get"].call_args.kwargs["timeout"] == 25
    _relay(host, {"method": "GET", "path": "/api/workflows/studio/incoming/proposals?limit=100"})
    assert http["get"].call_args.kwargs["timeout"] == 8
    _relay(host, {"method": "POST", "path": "/api/workflows/studio/incoming/proposals/dismiss", "body": {}})
    assert http["post"].call_args.kwargs["timeout"] == 15


def test_whatsapp_voice_transcribe_gets_read_budget(http):
    host = _Host()
    _relay(host, {"method": "POST", "path": "/api/tickets/whatsapp/messages/42/analyze-media", "body": None})
    assert http["post"].call_args.kwargs["timeout"] == 28
    assert http["post"].call_args.kwargs["json"] is None
    for path in ("/api/tickets/whatsapp/messages/0/analyze-media", "/api/tickets/whatsapp/messages/42/analyze-media?x=1"):
        _relay(host, {"method": "POST", "path": path, "body": None})
        assert http["post"].call_args.kwargs["timeout"] == 15
    _relay(host, {"method": "GET", "path": "/api/tickets/whatsapp/messages/42/analyze-media"})
    assert http["get"].call_args.kwargs["timeout"] == 8
