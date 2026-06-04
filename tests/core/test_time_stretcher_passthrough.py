import numpy as np

from distr.core.audio.time_stretcher import TimeStretcher


def test_time_stretcher_passthrough_returns_chunks_immediately_at_unity_speed():
    """At 1.0x, audio must not sit in a 100ms buffer (causes audible crackle)."""
    stretcher = TimeStretcher(sample_rate=24000, buffer_duration_ms=100, overlap_duration_ms=20)
    chunk = np.ones(480, dtype=np.float32) * 0.1  # 20ms @ 24kHz

    out = stretcher.process(chunk, 1.0)

    assert len(out) == len(chunk)
    np.testing.assert_allclose(out, chunk, rtol=1e-5)


def test_time_stretcher_flush_drains_pending_at_unity_speed():
    stretcher = TimeStretcher(sample_rate=24000, buffer_duration_ms=100, overlap_duration_ms=20)
    pending = np.linspace(-0.2, 0.2, 120, dtype=np.float32)
    stretcher.input_buffer = pending.copy()

    flushed = stretcher.flush()

    np.testing.assert_allclose(flushed, pending, rtol=1e-5)
    assert len(stretcher.input_buffer) == 0
