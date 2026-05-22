from distr.core.agent import command_handler


class _Loop:
    def is_running(self):
        return False


class _Runner:
    _loop = _Loop()


class _Endpoint:
    def __init__(self):
        self.device_index = None

    def set_device(self, device_index):
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
