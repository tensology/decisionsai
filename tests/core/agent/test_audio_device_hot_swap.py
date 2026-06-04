from distr.core.agent import command_handler


class _Loop:
    def is_running(self):
        return False


class _Runner:
    _loop = _Loop()


class _Endpoint:
    def __init__(self):
        self.device_index = None
        self.swap_calls = []
        self.swap_names = []

    def set_device(self, device_index, device_name=None):
        self.swap_calls.append(device_index)
        self.swap_names.append(device_name)
        self.device_index = device_index


class _Transport:
    def __init__(self):
        self._input = _Endpoint()
        self._output = _Endpoint()

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
        self.input_device = None
        self.output_device = None

    def _get_device_index(self, device_name, is_input=False):
        if device_name == "System Default":
            return None
        if device_name == "Studio Mic":
            return 11
        if device_name == "USB Speakers":
            return 22
        if device_name == "New Speakers":
            return 33
        return 11 if is_input else 22


def test_update_audio_devices_does_not_send_tts_interrupt(monkeypatch):
    session = _Session()

    def fail_interrupt(*_args, **_kwargs):
        raise AssertionError("output device hot-swap must not send a TTS interrupt")

    monkeypatch.setattr(command_handler, "_cmd_interrupt_tts", fail_interrupt)

    command_handler._cmd_update_audio_devices(
        session,
        {"input_device": "Studio Mic", "output_device": "USB Speakers"},
    )

    assert session.input_device == "Studio Mic"
    assert session.output_device == "USB Speakers"
    assert session.config["audio"]["input_device"] == "Studio Mic"
    assert session.config["audio"]["output_device"] == "USB Speakers"
    assert session.transport.input().device_index == 11
    assert session.transport.output().device_index == 22


def test_update_audio_devices_only_swaps_changed_endpoint():
    """Changing output alone must not restart the mic stream (breaks live STT/PTT)."""
    session = _Session()
    session.config["audio"] = {
        "input_device": "Studio Mic",
        "output_device": "USB Speakers",
    }
    session.input_device = "Studio Mic"
    session.output_device = "USB Speakers"

    command_handler._cmd_update_audio_devices(
        session,
        {"input_device": "Studio Mic", "output_device": "New Speakers"},
    )

    assert session.config["audio"]["output_device"] == "New Speakers"
    assert session.transport.input().swap_calls == []
    assert session.transport.output().swap_calls == [33]


def test_update_audio_devices_refreshes_unchanged_system_default_output():
    session = _Session()
    session.config["audio"] = {
        "input_device": "Studio Mic",
        "output_device": "System Default",
    }
    session.input_device = "Studio Mic"
    session.output_device = "System Default"

    command_handler._cmd_update_audio_devices(
        session,
        {"input_device": "Studio Mic", "output_device": "System Default"},
    )

    assert session.transport.input().swap_calls == []
    assert session.transport.output().swap_calls == [None]
    assert session.transport.output().swap_names == ["System Default"]
