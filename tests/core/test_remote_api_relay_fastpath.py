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
    def emit(self, *_args):
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
