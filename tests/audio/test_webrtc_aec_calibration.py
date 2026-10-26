#!/usr/bin/env python3
"""Deterministic (no-hardware) tests for WebRTCAECFilter calibration.

Covers the two capabilities ported back from the quarantined NLMS filter:
  * real reference correlation (was hardcoded to 0.0)
  * per-device speaker→mic delay estimation (was a fixed guess)

Run:
    python -m pytest tests/audio/test_webrtc_aec_calibration.py -v
"""

import os
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

pytest.importorskip("pipecat.audio.filters.base_audio_filter")

from distr.core.audio.echo_canceller import ReferenceBuffer, WebRTCAECFilter

SR = 16000
CHUNK = 320  # 20 ms


def _filter_with_reference(ref: np.ndarray, *, active: bool = True) -> WebRTCAECFilter:
    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=SR)
    for i in range(0, len(ref), CHUNK):
        ref_buf.push(ref[i:i + CHUNK])
    if active:
        ref_buf.set_active(True)
        # Pretend playback started long enough ago to allow calibration.
        ref_buf._activated_at = time.time() - 1.0
    return WebRTCAECFilter(ref_buf, output_sample_rate=SR, auto_calibrate=True)


def test_correlation_high_for_identical_low_for_independent():
    rng = np.random.default_rng(0)
    a = rng.standard_normal(320).astype(np.float32) * 0.1
    b = rng.standard_normal(320).astype(np.float32) * 0.1
    assert WebRTCAECFilter._correlation(a, a) > 0.99
    assert WebRTCAECFilter._correlation(a, b) < 0.2


def test_alignment_finds_zero_delay():
    rng = np.random.default_rng(1)
    ref = rng.standard_normal(SR).astype(np.float32) * 0.2
    aec = _filter_with_reference(ref)
    # Mic hears the most recent reference window -> delay 0.
    mic = ref[-CHUNK:]
    delay, corr = aec._find_reference_alignment(mic)
    assert delay == 0.0, f"expected 0ms, got {delay}"
    assert corr > 0.9


def test_alignment_finds_known_delay():
    rng = np.random.default_rng(2)
    ref = rng.standard_normal(SR).astype(np.float32) * 0.2
    aec = _filter_with_reference(ref)
    # Mic hears the reference delayed by 100 ms (1600 samples).
    delay_ms = 100
    delay_samples = delay_ms * SR // 1000
    end = len(ref) - delay_samples
    mic = ref[end - CHUNK:end]
    delay, corr = aec._find_reference_alignment(mic)
    assert delay == float(delay_ms), f"expected {delay_ms}ms, got {delay}"
    assert corr > 0.9


def test_auto_calibration_adopts_measured_delay_after_observations():
    rng = np.random.default_rng(3)
    ref = rng.standard_normal(SR).astype(np.float32) * 0.2
    aec = _filter_with_reference(ref)
    assert aec.calibrated is False

    mic = ref[-CHUNK:]
    for _ in range(3):
        aec._auto_calibrate_delay(mic)

    assert aec.calibrated is True
    assert aec.reference_delay_ms == 0.0


def test_manual_set_is_authoritative_and_marks_calibrated():
    ref = np.zeros(SR, dtype=np.float32)
    aec = _filter_with_reference(ref, active=False)
    assert aec.calibrated is False
    aec.set_reference_delay_ms(42.0)
    assert aec.calibrated is True
    assert aec.reference_delay_ms == 42.0


def test_calibration_gives_up_when_mic_is_silent():
    ref = np.zeros(SR, dtype=np.float32)
    aec = _filter_with_reference(ref)
    # No correlation possible (silence) -> estimator returns None until limit.
    aec._calibration_attempts = 80
    aec._auto_calibrate_delay(np.zeros(CHUNK, dtype=np.float32))
    assert aec.calibrated is True


def test_filter_logs_aec_summary_once_per_response():
    """Diagnostic summary fires on the active->inactive transition only."""
    import asyncio
    from types import SimpleNamespace

    ref = SimpleNamespace(is_active=True)
    aec = WebRTCAECFilter(ref, output_sample_rate=SR)
    aec._enabled = False  # skip processing; exercise transition detection only
    calls = []
    aec._log_session_summary = lambda: calls.append(True)
    aec._was_active = True

    # Still active -> no summary.
    asyncio.run(aec.filter(b"\x00\x00"))
    assert calls == []

    # Response ended -> one summary, and the flag is cleared.
    ref.is_active = False
    asyncio.run(aec.filter(b"\x00\x00"))
    assert calls == [True]
    assert aec._was_active is False

    # Remaining inactive frames must not log again.
    asyncio.run(aec.filter(b"\x00\x00"))
    assert calls == [True]
