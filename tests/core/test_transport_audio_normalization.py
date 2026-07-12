import asyncio
import numpy as np
from types import SimpleNamespace

from distr.core.agent.transport import HotSwappableLocalAudioOutputTransport


def _sine_pcm16(sample_rate: int, duration_seconds: float, frequency: float = 440.0) -> bytes:
    samples = int(sample_rate * duration_seconds)
    t = np.arange(samples, dtype=np.float32) / sample_rate
    wave = 0.4 * np.sin(2.0 * np.pi * frequency * t)
    return (wave * 32767.0).astype(np.int16).tobytes()


def test_transport_decodes_pcm16_mono():
    decoded = HotSwappableLocalAudioOutputTransport._decode_pcm16_mono(
        _sine_pcm16(44100, 1.0),
        source_channels=1,
    )

    assert decoded.dtype == np.float32
    assert abs(len(decoded) - 44100) <= 1
    assert float(np.max(np.abs(decoded))) > 0.1


def test_transport_downmixes_stereo_before_resampling():
    left = np.frombuffer(_sine_pcm16(44100, 0.5, 440.0), dtype=np.int16)
    right = np.frombuffer(_sine_pcm16(44100, 0.5, 660.0), dtype=np.int16)
    stereo = np.column_stack([left, right]).astype(np.int16).tobytes()

    decoded = HotSwappableLocalAudioOutputTransport._decode_pcm16_mono(
        stereo,
        source_channels=2,
    )

    assert abs(len(decoded) - 22050) <= 1
    assert decoded.ndim == 1


class _FakeStream:
    def __init__(self, active=True):
        self._active = active

    def is_active(self):
        return self._active

    def start_stream(self):
        self._active = True

    def stop_stream(self):
        self._active = False

    def close(self):
        self._active = False


class _FakePyAudio:
    def __init__(self, default_index):
        self.default_index = default_index
        self.opened_indices = []
        self.opened_channels = []
        self.fail_indices = set()

    def get_default_output_device_info(self):
        return {"index": self.default_index, "name": f"Device {self.default_index}"}

    def get_device_info_by_index(self, index):
        return {"index": index, "name": f"Device {index}", "maxOutputChannels": 2}

    def get_device_count(self):
        return 3

    def get_format_from_width(self, width):
        return width

    def open(self, **kwargs):
        index = kwargs.get("output_device_index")
        self.opened_indices.append(index)
        self.opened_channels.append(kwargs.get("channels"))
        if index in self.fail_indices:
            raise OSError("device failed")
        return _FakeStream(active=True)


def _use_fake_py_audio_default(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.config_loader.resolve_system_default_output_device",
        lambda: (None, None),
    )


def test_transport_refreshes_when_system_default_output_changes(monkeypatch):
    _use_fake_py_audio_default(monkeypatch)
    transport = HotSwappableLocalAudioOutputTransport.__new__(HotSwappableLocalAudioOutputTransport)
    transport._py_audio = _FakePyAudio(default_index=2)
    transport._params = SimpleNamespace(output_device_index=1, audio_out_channels=1)
    transport._output_device_name = "System Default"
    transport._resolved_output_device_index = 1
    transport._resolved_default_output_name = "Built-in Output"
    transport._last_opened_default_output_name = "Built-in Output"
    transport._out_stream = _FakeStream(active=True)
    transport._sample_rate = 24000
    transport._original_sample_rate = 24000

    reopened = []

    def fake_reopen(device_index):
        reopened.append(device_index)
        transport._resolved_output_device_index = device_index
        return True

    transport._reopen_output_stream = fake_reopen

    transport._ensure_output_stream_for_configured_device(reason="test")

    assert reopened == [2]
    assert transport._params.output_device_index == 2


def test_transport_refreshes_when_system_default_name_changes_at_same_index(monkeypatch):
    _use_fake_py_audio_default(monkeypatch)
    transport = HotSwappableLocalAudioOutputTransport.__new__(HotSwappableLocalAudioOutputTransport)
    transport._py_audio = _FakePyAudio(default_index=2)
    transport._params = SimpleNamespace(output_device_index=2, audio_out_channels=1)
    transport._output_device_name = "System Default"
    transport._resolved_output_device_index = 2
    transport._resolved_default_output_name = "AirPods Pro"
    transport._last_opened_default_output_name = "Built-in Output"
    transport._out_stream = _FakeStream(active=True)
    transport._sample_rate = 24000
    transport._original_sample_rate = 24000

    reopened = []

    def fake_reopen(device_index):
        reopened.append(device_index)
        transport._resolved_output_device_index = device_index
        return True

    transport._reopen_output_stream = fake_reopen

    transport._ensure_output_stream_for_configured_device(reason="default-name-changed")

    assert reopened == [2]


def test_transport_uses_fresh_resolver_for_output_missing_from_cached_list(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.config_loader.resolve_device_index",
        lambda device_name, is_input, sd_module=None: 4
        if device_name == "JBL TUNE510BT" and is_input is False
        else None,
    )
    transport = HotSwappableLocalAudioOutputTransport.__new__(HotSwappableLocalAudioOutputTransport)
    transport._py_audio = _FakePyAudio(default_index=2)
    transport._params = SimpleNamespace(output_device_index=2, audio_out_channels=1)
    transport._output_device_name = "JBL TUNE510BT"
    transport._resolved_default_output_name = "Device 2"
    transport._out_stream = _FakeStream(active=True)

    assert transport._resolve_configured_output_device_index() == 4


def test_transport_reopens_active_stream_when_output_channel_count_changes(monkeypatch):
    _use_fake_py_audio_default(monkeypatch)
    transport = HotSwappableLocalAudioOutputTransport.__new__(HotSwappableLocalAudioOutputTransport)
    transport._py_audio = _FakePyAudio(default_index=2)
    transport._params = SimpleNamespace(output_device_index=2, audio_out_channels=1)
    transport._output_device_name = "System Default"
    transport._resolved_output_device_index = 2
    transport._resolved_default_output_name = "Device 2"
    transport._last_opened_default_output_name = "Device 2"
    transport._out_stream = _FakeStream(active=True)
    transport._sample_rate = 44100
    transport._original_sample_rate = 44100
    transport._output_channels = 1

    reopened = []

    def fake_reopen(device_index):
        reopened.append(device_index)
        transport._resolved_output_device_index = device_index
        transport._output_channels = 2
        transport._params.audio_out_channels = 2
        return True

    transport._reopen_output_stream = fake_reopen

    transport._ensure_output_stream_for_configured_device(reason="channel refresh")

    assert reopened == [2]
    assert transport._output_channels == 2


def test_transport_defers_confirmed_tts_started_until_output_stream_is_active():
    transport = HotSwappableLocalAudioOutputTransport.__new__(HotSwappableLocalAudioOutputTransport)
    transport._out_stream = _FakeStream(active=False)

    refresh_reasons = []

    async def fake_ensure_output_stream_ready_async(*, reason):
        refresh_reasons.append(reason)

    transport._ensure_output_stream_ready_async = fake_ensure_output_stream_ready_async

    assert asyncio.run(transport._ready_to_emit_confirmed_tts_started()) is False
    assert refresh_reasons == ["audible audio before tts_started"]

    transport._out_stream = _FakeStream(active=False)

    async def fake_recovering_ensure_output_stream_ready_async(*, reason):
        refresh_reasons.append(reason)
        transport._out_stream = _FakeStream(active=True)

    transport._ensure_output_stream_ready_async = fake_recovering_ensure_output_stream_ready_async

    assert asyncio.run(transport._ready_to_emit_confirmed_tts_started()) is True


def test_transport_reports_active_output_route_for_confirmed_playback():
    transport = HotSwappableLocalAudioOutputTransport.__new__(HotSwappableLocalAudioOutputTransport)
    transport._py_audio = _FakePyAudio(default_index=2)
    transport._params = SimpleNamespace(output_device_index=2, audio_out_channels=2)
    transport._output_device_name = "System Default"
    transport._resolved_output_device_index = 2
    transport._resolved_default_output_name = "Device 2"
    transport._output_channels = 2
    transport._out_stream = _FakeStream(active=True)

    route = transport._active_output_route()

    assert route == {
        "configured_output": "System Default",
        "output_device_index": 2,
        "output_device_name": "Device 2",
        "system_default_output": "Device 2",
        "output_channels": 2,
        "stream_active": True,
    }


def test_transport_opens_stereo_output_and_duplicates_mono_samples():
    transport = HotSwappableLocalAudioOutputTransport.__new__(HotSwappableLocalAudioOutputTransport)
    py_audio = _FakePyAudio(default_index=2)
    transport._py_audio = py_audio
    transport._params = SimpleNamespace(output_device_index=2, audio_out_channels=1)
    transport._out_stream = _FakeStream(active=False)
    transport._sample_rate = 44100
    transport._original_sample_rate = 44100
    transport._stream_error_count = 0
    transport._stream_error_logged = False
    transport._resolved_output_device_index = None
    transport._failed_output_device_index = None
    transport._output_device_name = "System Default"
    transport._resolved_default_output_name = "JBL TUNE510BT"
    transport._output_channels = 1

    assert transport._reopen_output_stream(2) is True

    assert py_audio.opened_indices == [2]
    assert py_audio.opened_channels == [2]
    assert transport._params.audio_out_channels == 2

    mono = np.array([0.25, -0.25], dtype=np.float32)
    encoded = np.frombuffer(transport._pcm16_bytes_for_output_channels(mono), dtype=np.int16)

    assert encoded.tolist() == [8191, 8191, -8191, -8191]


def test_transport_falls_back_when_resolved_output_device_will_not_open(monkeypatch):
    _use_fake_py_audio_default(monkeypatch)
    transport = HotSwappableLocalAudioOutputTransport.__new__(HotSwappableLocalAudioOutputTransport)
    py_audio = _FakePyAudio(default_index=2)
    py_audio.fail_indices.add(2)
    transport._py_audio = py_audio
    transport._params = SimpleNamespace(output_device_index=2, audio_out_channels=1)
    transport._output_device_name = "System Default"
    transport._resolved_output_device_index = 2
    transport._resolved_default_output_name = "JBL TUNE510BT"
    transport._last_opened_default_output_name = "JBL TUNE510BT"
    transport._out_stream = _FakeStream(active=False)
    transport._sample_rate = 24000
    transport._original_sample_rate = 24000
    transport._stream_error_count = 0
    transport._stream_error_logged = False

    transport._ensure_output_stream_for_configured_device(reason="test fallback")

    assert py_audio.opened_indices == [2, 0]
    assert transport._output_stream_is_active() is True
    assert transport._params.output_device_index == 0
    assert transport._resolved_output_device_index == 0
    assert transport._failed_output_device_index == 2

    transport._ensure_output_stream_for_configured_device(reason="next audio frame")

    assert py_audio.opened_indices == [2, 0]
