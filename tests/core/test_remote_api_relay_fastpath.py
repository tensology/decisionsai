import importlib.util
import sys
import threading
import types
from pathlib import Path


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
