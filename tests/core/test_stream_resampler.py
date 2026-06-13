import numpy as np

from distr.core.audio.stream_resampler import LinearStreamResampler


def _sine(sample_rate: int, duration_seconds: float, frequency: float = 440.0) -> np.ndarray:
    samples = int(sample_rate * duration_seconds)
    t = np.arange(samples, dtype=np.float32) / sample_rate
    return (0.4 * np.sin(2.0 * np.pi * frequency * t)).astype(np.float32)


def test_stream_resampler_preserves_continuity_across_chunks():
    resampler = LinearStreamResampler()
    resampler.configure(24000, 44100)

    source = _sine(24000, 0.5)
    chunk_size = 480  # 20 ms @ 24 kHz — same size Kokoro emits
    outputs = []
    for start in range(0, len(source), chunk_size):
        outputs.append(resampler.process(source[start:start + chunk_size]))
    outputs.append(resampler.flush())

    merged = np.concatenate([part for part in outputs if len(part) > 0])
    expected_len = int(round(len(source) * 44100 / 24000))
    assert abs(len(merged) - expected_len) <= 2
    assert float(np.max(np.abs(merged))) > 0.05

    # Chunk-boundary glitches show up as large sample-to-sample jumps.
    diffs = np.abs(np.diff(merged))
    assert float(np.percentile(diffs, 99)) < 0.08

    # Resampling must preserve duration (same pitch, not slow-motion).
    input_duration = len(source) / 24000
    output_duration = len(merged) / 44100
    assert abs(input_duration - output_duration) < 0.02


def test_transport_decode_and_resample_uses_streaming_resampler():
    from distr.core.agent.transport import HotSwappableLocalAudioOutputTransport

    transport = HotSwappableLocalAudioOutputTransport.__new__(HotSwappableLocalAudioOutputTransport)
    transport._original_sample_rate = 44100
    transport._last_frame_sample_rate = 44100
    transport._pcm_resampler = LinearStreamResampler()
    transport._pcm_resampler.configure(24000, 44100)

    pcm = (_sine(24000, 0.05) * 32767.0).astype(np.int16).tobytes()
    first = transport._decode_and_resample_frame(pcm, 24000, 1)
    second = transport._decode_and_resample_frame(pcm, 24000, 1)

    assert len(first) > 0
    assert len(second) > 0
    assert first.dtype == np.float32
