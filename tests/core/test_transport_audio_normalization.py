import numpy as np

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
