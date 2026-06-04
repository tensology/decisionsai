import numpy as np
from types import SimpleNamespace

from distr.core.agent.transport import HotSwappableLocalAudioOutputTransport


def _sine_pcm16(sample_rate: int, duration_seconds: float, frequency: float = 440.0) -> bytes:
    samples = int(sample_rate * duration_seconds)
    t = np.arange(samples, dtype=np.float32) / sample_rate
    wave = 0.4 * np.sin(2.0 * np.pi * frequency * t)
    return (wave * 32767.0).astype(np.int16).tobytes()


def test_transport_resamples_elevenlabs_rate_to_output_rate():
    decoded = HotSwappableLocalAudioOutputTransport._decode_pcm16_mono(
        _sine_pcm16(44100, 1.0),
        source_rate=44100,
        source_channels=1,
        target_rate=24000,
    )

    assert decoded.dtype == np.float32
    assert abs(len(decoded) - 24000) <= 1
    assert float(np.max(np.abs(decoded))) > 0.1


def test_transport_downmixes_stereo_before_resampling():
    left = np.frombuffer(_sine_pcm16(44100, 0.5, 440.0), dtype=np.int16)
    right = np.frombuffer(_sine_pcm16(44100, 0.5, 660.0), dtype=np.int16)
    stereo = np.column_stack([left, right]).astype(np.int16).tobytes()

    decoded = HotSwappableLocalAudioOutputTransport._decode_pcm16_mono(
        stereo,
        source_rate=44100,
        source_channels=2,
        target_rate=24000,
    )

    assert abs(len(decoded) - 12000) <= 1
    assert decoded.ndim == 1


class _FakeStream:
    def __init__(self, active=True):
        self._active = active

    def is_active(self):
        return self._active


class _FakePyAudio:
    def __init__(self, default_index):
        self.default_index = default_index

    def get_default_output_device_info(self):
        return {"index": self.default_index, "name": f"Device {self.default_index}"}

    def get_device_info_by_index(self, index):
        return {"index": index, "name": f"Device {index}", "maxOutputChannels": 2}

    def get_device_count(self):
        return 3


def test_transport_refreshes_when_system_default_output_changes():
    transport = HotSwappableLocalAudioOutputTransport.__new__(HotSwappableLocalAudioOutputTransport)
    transport._py_audio = _FakePyAudio(default_index=2)
    transport._params = SimpleNamespace(output_device_index=1)
    transport._output_device_name = "System Default"
    transport._resolved_output_device_index = 1
    transport._out_stream = _FakeStream(active=True)

    reopened = []

    def fake_reopen(device_index):
        reopened.append(device_index)
        transport._resolved_output_device_index = device_index

    transport._reopen_output_stream = fake_reopen

    transport._ensure_output_stream_for_configured_device(reason="test")

    assert reopened == [2]
    assert transport._params.output_device_index == 2
