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

    _SPEC = importlib.util.spec_from_file_location("remote_control_mouse_test", _REMOTE_CONTROL_PATH)
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
        self.mouse_calls = []

    def _send_websocket_message(self, message):
        self.responses.append(message)

    def _set_mouse_position(self, x, y, screen_number=None, button="left", take_screenshot=False):
        self.mouse_calls.append(
            {
                "x": x,
                "y": y,
                "screen_number": screen_number,
                "button": button,
                "take_screenshot": take_screenshot,
            }
        )
        return True


def test_set_mouse_position_command_is_handled():
    host = _Host()
    host._handle_remote_control_command(
        {
            "command": "set_mouse_position",
            "request_id": "req_click_1",
            "data": {
                "x": 120,
                "y": 340,
                "screen_number": 1,
                "button": "left",
                "take_screenshot": False,
            },
        }
    )

    assert host.mouse_calls == [
        {
            "x": 120,
            "y": 340,
            "screen_number": 1,
            "button": "left",
            "take_screenshot": False,
        }
    ]
    assert host.responses == [
        {
            "type": "remote_control_response",
            "command": "set_mouse_position",
            "request_id": "req_click_1",
            "data": {"success": True, "x": 120, "y": 340},
        }
    ]
