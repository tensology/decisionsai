from unittest.mock import patch

from distr.core.agent import command_handler
from distr.core.audio.utils import (
    is_system_default_device_name,
    restore_locked_devices,
)


class _Loop:
    def is_running(self):
        return False


class _Runner:
    _loop = _Loop()


class _InputEndpoint:
    def __init__(self):
        self.refresh_calls = 0

    def refresh_system_default(self):
        self.refresh_calls += 1


class _OutputEndpoint:
    def __init__(self):
        self.swap_calls = []

    def set_device(self, device_index, device_name=None):
        self.swap_calls.append((device_index, device_name))


class _Transport:
    def __init__(self):
        self._input = _InputEndpoint()
        self._output = _OutputEndpoint()

    def input(self):
        return self._input

    def output(self):
        return self._output


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _Session:
    def __init__(self):
        self.logger = _Logger()
        self.runner = _Runner()
        self.transport = _Transport()
        self.config = {"audio": {}}
        self.input_device = "System Default"
        self.output_device = "System Default"


def test_is_system_default_device_name():
    assert is_system_default_device_name("System Default") is True
    assert is_system_default_device_name("system_default") is True
    assert is_system_default_device_name("AirPods Pro") is False


def test_restore_locked_devices_skips_switchaudiosource_for_system_default():
    settings = {
        "lock_sound": True,
        "locked_output": "System Default",
        "locked_input": "System Default",
    }
    with patch("distr.core.audio.utils.query_native_devices", return_value=([], [])):
        with patch("distr.core.audio.utils.set_system_default_device") as mock_set:
            result = restore_locked_devices(settings)

    assert result == {"output_restored": True, "input_restored": True}
    mock_set.assert_not_called()


def test_restore_locked_devices_restores_named_device_when_present():
    settings = {
        "lock_sound": True,
        "locked_output": "AirPods Pro",
        "locked_input": None,
    }
    outputs = [{"name": "AirPods Pro", "id": "AirPods Pro", "type": "Bluetooth"}]
    with patch("distr.core.audio.utils.query_native_devices", return_value=(outputs, [])):
        with patch("distr.core.audio.utils.set_system_default_device", return_value=True) as mock_set:
            result = restore_locked_devices(settings)

    assert result["output_restored"] is True
    mock_set.assert_called_once_with("AirPods Pro", is_output=True)


def test_update_audio_devices_refreshes_unchanged_system_default_input():
    session = _Session()
    session.config["audio"] = {
        "input_device": "System Default",
        "output_device": "System Default",
    }

    command_handler._cmd_update_audio_devices(
        session,
        {"input_device": "System Default", "output_device": "System Default"},
    )

    assert session.transport.input().refresh_calls == 1
    assert session.transport.output().swap_calls == [(None, "System Default")]
