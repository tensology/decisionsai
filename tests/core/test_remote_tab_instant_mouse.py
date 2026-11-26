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

    _SPEC = importlib.util.spec_from_file_location("remote_control_tab_test", _REMOTE_CONTROL_PATH)
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


def test_key_tab_presses_tab():
    host = _Host()
    host._press_key = Mock(return_value=True)
    host._handle_remote_control_command({"command": "key_tab", "request_id": "t1", "data": {"take_screenshot": False}})
    host._press_key.assert_called_once_with("tab", take_screenshot=False)
    assert host.responses[-1]["command"] == "key_tab"
    assert host.responses[-1]["data"] == {"success": True}


@pytest.mark.parametrize("command", ["mouse_move", "mouse_down"])
def test_instant_flag_reaches_move(command, monkeypatch):
    host = _Host()
    host._move_mouse_to = Mock()
    monkeypatch.setattr(_MODULE, "pyautogui", Mock())
    data = {"x": 10, "y": 20, "screen_number": 0, "instant": True, "take_screenshot": False}
    host._handle_remote_control_command({"command": command, "request_id": "m1", "data": data})
    host._move_mouse_to.assert_called_once_with(10, 20, 0, instant=True)
    host._move_mouse_to.reset_mock()
    host._handle_remote_control_command({"command": command, "request_id": "m2", "data": {**data, "instant": False}})
    host._move_mouse_to.assert_called_once_with(10, 20, 0, instant=False)


@pytest.mark.parametrize("instant,expect_zero", [(True, True), (False, False)])
def test_move_mouse_to_duration(instant, expect_zero, monkeypatch):
    fake = Mock()
    fake.position.return_value = (0, 0)
    monkeypatch.setattr(_MODULE, "pyautogui", fake)
    host = _Host()
    host._get_screens_list = Mock(return_value=[{"screen_number": 0, "geometry": {"x": 0, "y": 0, "width": 1600, "height": 1000}}])
    host._move_mouse_to(800, 500, 0, instant=instant)
    _, kwargs = fake.moveTo.call_args
    assert (kwargs["duration"] == 0) is expect_zero
