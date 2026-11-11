import importlib.util
import sys
import threading
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

_REMOTE_CONTROL_PATH = (
    Path(__file__).resolve().parents[2]
    / "distr"
    / "core"
    / "integrations"
    / "telegram"
    / "remote_control.py"
)

_fake_tg_pkg = types.ModuleType("distr.core.integrations.telegram")
_fake_tg_pkg.__path__ = []
_fake_tg_utils = types.ModuleType("distr.core.integrations.telegram.utils")
_fake_tg_utils.hash_channel_id = lambda value: f"hash_{value}"
_fake_tg_utils.relay_internal_token = lambda: "test-token"

_TG_KEYS = ("distr.core.integrations.telegram", "distr.core.integrations.telegram.utils")
_prior_tg = {k: sys.modules[k] for k in _TG_KEYS if k in sys.modules}
try:
    sys.modules["distr.core.integrations.telegram"] = _fake_tg_pkg
    sys.modules["distr.core.integrations.telegram.utils"] = _fake_tg_utils

    _SPEC = importlib.util.spec_from_file_location("remote_control_fastpath_test", _REMOTE_CONTROL_PATH)
    _MODULE = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(_MODULE)
    TelegramRemoteControlMixin = _MODULE.TelegramRemoteControlMixin
finally:
    for _k in _TG_KEYS:
        if _k in _prior_tg:
            sys.modules[_k] = _prior_tg[_k]
        else:
            sys.modules.pop(_k, None)


class _SignalCollector:
    def __init__(self):
        self.calls = 0

    def emit(self, *_args):
        self.calls += 1
        return None


class _Host(TelegramRemoteControlMixin):
    def __init__(self):
        self.remote_control_command_received = _SignalCollector()
        self._remote_control_lock = threading.Lock()
        self.responses = []

    def _send_websocket_message(self, message):
        self.responses.append(message)

    def _send_websocket_binary(self, _payload):
        self.responses.append({"binary": True})


def test_api_relay_runs_without_waiting_on_remote_control_lock(monkeypatch):
    host = _Host()
    host._remote_control_lock.acquire()

    calls = []

    def _fake_dispatch(data):
        calls.append(data.get("data", {}).get("path"))

    monkeypatch.setattr(host, "_dispatch_api_relay", _fake_dispatch)

    host._handle_remote_control_command({
        "command": "api_relay",
        "request_id": "req_test",
        "data": {"method": "GET", "path": "/api/snippets/summary"},
    })

    host._remote_control_lock.release()
    assert calls == ["/api/snippets/summary"]


def test_forced_screen_refresh_returns_cache_without_polling(monkeypatch):
    screen = {
        "screen_number": 1,
        "screen_name": "Primary",
        "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080},
    }
    fake_screen_utils = types.ModuleType("distr.core.screen_utils")
    fake_screen_utils._screen_info_cache = {"screens": [screen]}
    monkeypatch.setitem(sys.modules, "distr.core.screen_utils", fake_screen_utils)
    host = _Host()
    host._request_screen_update_signal = _SignalCollector()

    assert host._get_screens_list(force_update=True) == [screen]
    assert host._request_screen_update_signal.calls == 1
    assert host._get_screens_list(force_update=True) == [screen]
    assert host._request_screen_update_signal.calls == 1


@pytest.mark.parametrize("suffix", ["", "/conversation"])
def test_planning_scope_and_conflict_survive_api_relay(monkeypatch, suffix):
    from distr.gui.web.security import INTERNAL_AUTH_HEADER

    host = _Host()
    origin = "http://127.0.0.1:8765"
    path = f"/api/workflows/studio/plan-workspaces/11{suffix}?board_id=3&project_id=7"
    detail = {"detail": "The board or project changed. Go back to Plans and refresh the list."}
    response = Mock(status_code=409)
    response.json.return_value = detail
    http_get = Mock(return_value=response)
    monkeypatch.setattr("requests.get", http_get)
    monkeypatch.setattr("distr.gui.web.server.get_unified_server", lambda: None)
    monkeypatch.setattr("distr.gui.web.security.get_internal_api_token", lambda: "fixture-token")

    host._handle_remote_control_command({
        "command": "api_relay",
        "request_id": "plan-scope-check",
        "data": {"method": "GET", "path": path},
    })

    http_get.assert_called_once_with(origin + path, timeout=8, headers={
        "Content-Type": "application/json", INTERNAL_AUTH_HEADER: "fixture-token",
    })
    assert host.responses == [{
        "type": "remote_control_response", "command": "api_relay",
        "request_id": "plan-scope-check", "data": {"status": 409, "response": detail},
    }]


@pytest.mark.parametrize("suffix", ["", "&chat_id=17", "&source_key=" + "b" * 32,
                                    "&chat_id=17&source_key=" + "b" * 32])
def test_relay_keeps_the_proposal_source_read_window(monkeypatch, suffix):
    host = _Host()
    path = "/api/workflows/studio/incoming/proposals/source?review_key=" + "a" * 64 + suffix
    get = Mock(return_value=Mock(status_code=200, json=Mock(return_value={})))
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr("distr.gui.web.server.get_unified_server", lambda: None)
    monkeypatch.setattr("distr.gui.web.security.get_internal_api_token", lambda: "fixture-token")
    host._dispatch_api_relay({"request_id": "source", "data": {"method": "GET", "path": path}})
    assert get.call_args.args[0].endswith(path)
    assert get.call_args.kwargs["timeout"] == 25
    assert host.responses[0]["data"]["status"] == 200


@pytest.mark.parametrize("path, expected_timeout", [
    ("/api/workflows/studio/tasks/17/source-replies", 90),
    ("/api/workflows/studio/tasks/17/source-replies/" + "a" * 32 + "/approve", 90),
    ("/api/workflows/studio/tasks/17/artifacts", 15),
])
def test_relay_only_extends_the_timeout_for_source_reply_writes(monkeypatch, path, expected_timeout):
    host = _Host()
    post = Mock(return_value=Mock(status_code=200, json=Mock(return_value={})))
    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr("distr.gui.web.server.get_unified_server", lambda: None)
    monkeypatch.setattr("distr.gui.web.security.get_internal_api_token", lambda: "fixture-token")
    host._dispatch_api_relay({
        "request_id": "reply",
        "data": {"method": "POST", "path": path, "body": {}},
    })
    assert post.call_args.kwargs["timeout"] == expected_timeout
    assert host.responses[0]["data"]["status"] == 200
