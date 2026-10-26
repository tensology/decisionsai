#!/usr/bin/env python3
"""Deterministic tests for the LiveKit APM AEC engine (no hardware).

The engine is opt-in (aec_engine='livekit'); these tests guard its public
surface: chunking, reverse-stream ingestion, metrics and delay control.
"""

import asyncio
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

pytest.importorskip("livekit.rtc")

from distr.core.audio.livekit_aec import LiveKitAECFilter


def _tone(freq, n, sr, amp=0.3):
    return (np.sin(2 * np.pi * freq * np.arange(n) / sr) * amp * 32767).astype(np.int16)


def test_filter_preserves_length_and_metrics():
    f = LiveKitAECFilter(input_sample_rate=16000, output_sample_rate=44100)
    f.push_reverse(_tone(300, 4410, 44100).tobytes(), 44100)
    near = _tone(300, 320, 16000)
    out = asyncio.run(f.filter(near.tobytes()))
    assert len(out) == len(near.tobytes())
    metrics = f.last_metrics
    for key in ("input_rms", "residual_rms", "reference_rms", "reference_correlation", "erle_db"):
        assert key in metrics
    assert metrics["reference_rms"] > 0


def test_set_reference_delay_ms_updates_and_clamps():
    f = LiveKitAECFilter(input_sample_rate=16000, output_sample_rate=44100)
    f.set_reference_delay_ms(120)
    assert f.reference_delay_ms == 120
    f.set_reference_delay_ms(9999)
    assert f.reference_delay_ms == 500.0


def test_empty_audio_is_returned_unchanged():
    f = LiveKitAECFilter(input_sample_rate=16000, output_sample_rate=44100)
    assert asyncio.run(f.filter(b"")) == b""


def test_reverse_without_playback_is_safe():
    f = LiveKitAECFilter(input_sample_rate=16000, output_sample_rate=44100)
    # No push_reverse beforehand -> correlation 0, still length-preserving.
    near = _tone(440, 160, 16000)
    out = asyncio.run(f.filter(near.tobytes()))
    assert len(out) == len(near.tobytes())
    assert f.calibrated is True


def test_push_reverse_accepts_float32_array():
    """The transport passes float32[-1,1] (processed_audio), not int16 bytes.

    Regression: push_reverse did np.frombuffer(..., int16) on an ndarray, which
    threw and was swallowed, leaving reference_rms=0 -> no echo cancellation
    (live log showed ref=0.000 on every frame).
    """
    f = LiveKitAECFilter(input_sample_rate=16000, output_sample_rate=44100)
    far = (np.sin(2 * np.pi * 300 * np.arange(4410) / 44100) * 0.3).astype(np.float32)
    f.push_reverse(far, 44100)
    out = asyncio.run(f.filter(_tone(300, 320, 16000).tobytes()))
    assert len(out) == 640
    assert f.last_metrics["reference_rms"] > 0.0
