#!/usr/bin/env python3
"""
Synthetic AEC + Echo Gate tests — no hardware required.

Tests:
  1. Echo suppression:     AEC reduces echo when ref and mic are correlated
  2. Reference buffer:     Ring buffer stores/retrieves audio correctly
  3. Echo gate logic:      _check_bargein_energy suppresses low-energy, passes high
  4. Speech over echo:     Real speech on top of echo passes barge-in gate
  5. Silence passthrough:  AEC passes silence through unchanged when inactive
  6. Grace period:         Barge-in suppressed during first 500ms after TTS start
  7. Adaptive echo floor:  Echo floor tracker adapts to measured residual level
  8. Deferred barge-in:    Pending barge-in fires when energy builds up over time

Run:
    python -m pytest tests/audio/test_aec_synthetic.py -v
"""

import asyncio
import sys
import os
import time
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from distr.core.audio.echo_canceller import ReferenceBuffer, NLMSEchoCanceller


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rms(arr: np.ndarray) -> float:
    return float(np.sqrt(np.mean(arr ** 2))) if len(arr) > 0 else 0.0


def make_tone(freq: float, duration_s: float, sr: int = 16000, amplitude: float = 0.3) -> np.ndarray:
    """Generate a sine tone as float32 in [-1, 1]."""
    t = np.arange(int(sr * duration_s), dtype=np.float32) / sr
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def f32_to_int16_bytes(arr: np.ndarray) -> bytes:
    return (np.clip(arr, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def int16_bytes_to_f32(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.int16).astype(np.float32) / 32768.0


CHUNK_MS = 20
SR = 16000
CHUNK_SAMPLES = int(SR * CHUNK_MS / 1000)  # 320 samples per chunk


def _make_stt_stub(ref_buf):
    """Create a minimal BaseSTTService instance for testing echo gate logic.

    BaseSTTService is abstract (run_stt), so we create a concrete stub.
    We only need the barge-in / echo gate methods, not the full STT pipeline.
    """
    from collections import deque
    from distr.core.agent.services.stt.base import BaseSTTService

    class _STTStub(BaseSTTService):
        async def run_stt(self, audio):
            pass

    # Bypass __init__ — we only need the echo gate fields
    stt = object.__new__(_STTStub)
    stt._aec_ref_buf = ref_buf
    stt._pre_buffer = deque(maxlen=15)
    stt._echo_floor_rms = 0.035
    stt._echo_floor_alpha = 0.08
    stt._echo_floor_multiplier = 1.8
    stt._echo_floor_min = 0.04
    stt._echo_floor_samples = 0
    stt._bargein_consecutive_required = 10
    stt._bargein_consecutive_count = 0
    return stt


# ---------------------------------------------------------------------------
# Test 1: Echo suppression (interleaved push + filter, chunk by chunk)
# ---------------------------------------------------------------------------

def test_echo_suppression():
    """AEC should reduce echo when reference and mic contain the same signal.

    Key: we interleave ref_buf.push() and aec.filter() chunk-by-chunk,
    simulating real-time audio flow. The NLMS filter needs this lockstep
    to converge — pushing all reference first then filtering doesn't work.
    """
    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=SR)
    aec = NLMSEchoCanceller(
        reference_buffer=ref_buf, filter_length=800, mu=0.5,
        output_sample_rate=SR,
    )
    asyncio.get_event_loop().run_until_complete(aec.start(SR))

    # Generate 1.5s of echo signal (same tone in ref and mic)
    duration = 1.5
    echo_signal = make_tone(440.0, duration, SR, amplitude=0.3)
    num_chunks = len(echo_signal) // CHUNK_SAMPLES

    ref_buf.set_active(True)

    raw_rms_values = []
    aec_rms_values = []

    for i in range(num_chunks):
        chunk = echo_signal[i * CHUNK_SAMPLES:(i + 1) * CHUNK_SAMPLES]

        # Push reference (what the speaker is playing)
        ref_buf.push(chunk)

        # Mic picks up the same signal (echo)
        mic_bytes = f32_to_int16_bytes(chunk)

        # AEC filters it
        filtered_bytes = asyncio.get_event_loop().run_until_complete(aec.filter(mic_bytes))
        filtered = int16_bytes_to_f32(filtered_bytes)

        raw_rms_values.append(rms(chunk))
        aec_rms_values.append(rms(filtered))

    ref_buf.set_active(False)

    # Check the last 0.5s (after convergence) — AEC should have reduced echo
    convergence_chunks = int(0.5 / (CHUNK_MS / 1000))  # last 25 chunks
    tail_raw = np.mean(raw_rms_values[-convergence_chunks:])
    tail_aec = np.mean(aec_rms_values[-convergence_chunks:])

    reduction_db = 20 * np.log10(tail_aec / tail_raw) if tail_raw > 1e-8 and tail_aec > 1e-8 else -60
    print(f"  Echo suppression: raw={tail_raw:.4f}, aec={tail_aec:.4f}, reduction={reduction_db:.1f}dB")

    # Expect at least 6dB reduction after convergence
    assert reduction_db < -6.0, f"Expected >6dB echo reduction, got {reduction_db:.1f}dB"


# ---------------------------------------------------------------------------
# Test 2: Reference buffer ring behavior
# ---------------------------------------------------------------------------

def test_reference_buffer():
    """Ring buffer should store and retrieve audio correctly."""
    ref_buf = ReferenceBuffer(max_duration_secs=0.5, sample_rate=SR)

    # Push 0.3s of known data
    data = make_tone(300.0, 0.3, SR, amplitude=0.5)
    ref_buf.push(data)

    # Pull same length — should match
    pulled = ref_buf.pull(len(data))
    assert np.allclose(pulled, data, atol=1e-6), "Pull should return what was pushed"

    # Pull more than available — should zero-pad from the front
    pulled_long = ref_buf.pull(len(data) + 1000)
    assert len(pulled_long) == len(data) + 1000

    # Active state
    assert not ref_buf.is_active
    ref_buf.set_active(True)
    assert ref_buf.is_active
    ref_buf.set_active(False)
    assert not ref_buf.is_active

    print("  Reference buffer: OK")


# ---------------------------------------------------------------------------
# Test 3: Echo gate — _check_bargein_energy
# ---------------------------------------------------------------------------

def test_echo_gate_logic():
    """Echo gate should suppress low-energy (echo) and pass high-energy (speech)."""
    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=SR)
    stt = _make_stt_stub(ref_buf)

    # Simulate TTS active with activation time well in the past (past grace period)
    ref_buf.set_active(True)
    ref_buf._activated_at = time.time() - 2.0  # 2s ago, well past grace

    # Low energy pre-buffer (echo residual ~0.01 RMS — well below threshold)
    low_energy = make_tone(440.0, 0.02, SR, amplitude=0.01)
    stt._pre_buffer = list(stt._pre_buffer)  # convert deque for list assignment
    stt._pre_buffer.extend([f32_to_int16_bytes(low_energy)] * 15)
    assert not stt._check_bargein_energy(), "Low energy should be suppressed"

    # High energy pre-buffer (speech ~0.25 RMS — well above threshold)
    high_energy = make_tone(440.0, 0.02, SR, amplitude=0.25)
    stt._pre_buffer = [f32_to_int16_bytes(high_energy)] * 15
    stt._bargein_consecutive_count = 0  # reset
    stt._pre_buffer = [f32_to_int16_bytes(high_energy)] * 15
    assert stt._check_bargein_energy(), "High energy should pass barge-in gate"

    ref_buf.set_active(False)
    print("  Echo gate logic: OK")


# ---------------------------------------------------------------------------
# Test 4: Speech over echo — barge-in should fire
# ---------------------------------------------------------------------------

def test_speech_over_echo():
    """When real speech is mixed with echo, AEC should preserve speech and
    the barge-in gate should detect it."""
    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=SR)
    aec = NLMSEchoCanceller(
        reference_buffer=ref_buf, filter_length=800, mu=0.5,
        output_sample_rate=SR,
    )
    asyncio.get_event_loop().run_until_complete(aec.start(SR))

    duration = 1.5
    # Echo = 440Hz tone, Speech = 880Hz tone (different frequency)
    echo_signal = make_tone(440.0, duration, SR, amplitude=0.3)
    speech_signal = make_tone(880.0, duration, SR, amplitude=0.2)
    num_chunks = len(echo_signal) // CHUNK_SAMPLES

    ref_buf.set_active(True)

    aec_rms_values = []

    for i in range(num_chunks):
        echo_chunk = echo_signal[i * CHUNK_SAMPLES:(i + 1) * CHUNK_SAMPLES]
        speech_chunk = speech_signal[i * CHUNK_SAMPLES:(i + 1) * CHUNK_SAMPLES]

        # Speaker plays echo
        ref_buf.push(echo_chunk)

        # Mic picks up echo + speech
        mic_chunk = echo_chunk + speech_chunk
        mic_bytes = f32_to_int16_bytes(mic_chunk)

        filtered_bytes = asyncio.get_event_loop().run_until_complete(aec.filter(mic_bytes))
        filtered = int16_bytes_to_f32(filtered_bytes)
        aec_rms_values.append(rms(filtered))

    ref_buf.set_active(False)

    # After convergence, the filtered signal should still have significant energy
    # (the speech component that AEC can't cancel because it's not in the reference)
    convergence_chunks = int(0.5 / (CHUNK_MS / 1000))
    tail_aec = np.mean(aec_rms_values[-convergence_chunks:])

    print(f"  Speech over echo: filtered RMS={tail_aec:.4f} (speech should survive)")
    assert tail_aec > 0.03, f"Speech should survive AEC, got RMS={tail_aec:.4f}"


# ---------------------------------------------------------------------------
# Test 5: Silence passthrough when AEC inactive
# ---------------------------------------------------------------------------

def test_silence_passthrough():
    """When TTS is not playing (ref_buf inactive), AEC should pass audio through unchanged."""
    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=SR)
    aec = NLMSEchoCanceller(
        reference_buffer=ref_buf, filter_length=800, mu=0.5,
        output_sample_rate=SR,
    )
    asyncio.get_event_loop().run_until_complete(aec.start(SR))

    # ref_buf is NOT active — AEC should be a no-op
    speech = make_tone(440.0, 0.1, SR, amplitude=0.2)
    for i in range(len(speech) // CHUNK_SAMPLES):
        chunk = speech[i * CHUNK_SAMPLES:(i + 1) * CHUNK_SAMPLES]
        mic_bytes = f32_to_int16_bytes(chunk)
        out_bytes = asyncio.get_event_loop().run_until_complete(aec.filter(mic_bytes))
        # Should be identical (passthrough)
        assert mic_bytes == out_bytes, "AEC should passthrough when inactive"

    print("  Silence passthrough: OK")


# ---------------------------------------------------------------------------
# Test 6: Grace period suppresses barge-in right after TTS starts
# ---------------------------------------------------------------------------

def test_grace_period():
    """Barge-in should be suppressed during the first 500ms after TTS activation."""
    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=SR)
    stt = _make_stt_stub(ref_buf)

    # Activate TTS just now — within grace period
    ref_buf.set_active(True)
    # _activated_at is set by set_active(True) to time.time()

    # Even with high energy, should be suppressed during grace period
    high_energy = make_tone(440.0, 0.02, SR, amplitude=0.25)
    stt._pre_buffer = list(stt._pre_buffer)
    stt._pre_buffer = [f32_to_int16_bytes(high_energy)] * 15
    assert not stt._check_bargein_energy(), "Should suppress during grace period"

    # Now simulate time passing beyond grace period
    ref_buf._activated_at = time.time() - 1.5  # 1.5s ago, well past 0.8s grace
    stt._bargein_consecutive_count = 0
    stt._pre_buffer = [f32_to_int16_bytes(high_energy)] * 15
    assert stt._check_bargein_energy(), "Should allow after grace period"

    ref_buf.set_active(False)
    print("  Grace period: OK")


# ---------------------------------------------------------------------------
# Test 7: Adaptive echo floor tracking
# ---------------------------------------------------------------------------

def test_adaptive_echo_floor():
    """Echo floor should adapt to measured residual levels."""
    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=SR)
    stt = _make_stt_stub(ref_buf)

    # Feed low echo residual samples — floor should decrease
    for _ in range(50):
        stt._update_echo_floor(0.010)

    assert stt._echo_floor_rms < 0.025, f"Floor should decrease, got {stt._echo_floor_rms:.4f}"

    # Feed higher residual — floor should increase
    for _ in range(50):
        stt._update_echo_floor(0.050)

    assert stt._echo_floor_rms > 0.035, f"Floor should increase, got {stt._echo_floor_rms:.4f}"

    # Speech-level RMS (>0.06) should NOT contaminate the floor
    floor_before = stt._echo_floor_rms
    for _ in range(20):
        stt._update_echo_floor(0.10)  # speech level
    assert abs(stt._echo_floor_rms - floor_before) < 0.001, "Speech should not affect echo floor"

    # Threshold should be floor * multiplier, clamped to min
    threshold = stt._get_adaptive_threshold()
    expected = max(stt._echo_floor_rms * 1.8, 0.04)
    assert abs(threshold - expected) < 0.001, f"Threshold mismatch: {threshold} vs {expected}"

    print(f"  Adaptive echo floor: OK (floor={stt._echo_floor_rms:.4f}, threshold={threshold:.4f})")


# ---------------------------------------------------------------------------
# Test 8: Activation timestamp resets on every set_active(True)
# ---------------------------------------------------------------------------

def test_activation_timestamp_resets():
    """Each set_active(True) should reset _activated_at for fresh grace period."""
    ref_buf = ReferenceBuffer(max_duration_secs=2.0, sample_rate=SR)

    ref_buf.set_active(True)
    t1 = ref_buf._activated_at
    time.sleep(0.05)

    # Second activation (new TTS sentence) should reset timestamp
    ref_buf.set_active(True)
    t2 = ref_buf._activated_at
    assert t2 > t1, "Activation timestamp should reset on each set_active(True)"

    # Deactivate and reactivate
    ref_buf.set_active(False)
    time.sleep(0.05)
    ref_buf.set_active(True)
    t3 = ref_buf._activated_at
    assert t3 > t2, "Timestamp should reset after deactivate/reactivate cycle"

    ref_buf.set_active(False)
    print("  Activation timestamp resets: OK")


# ---------------------------------------------------------------------------
# Main (for running outside pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  Synthetic AEC + Echo Gate Tests")
    print("=" * 60)

    tests = [
        ("1. Echo suppression (interleaved)", test_echo_suppression),
        ("2. Reference buffer", test_reference_buffer),
        ("3. Echo gate logic", test_echo_gate_logic),
        ("4. Speech over echo", test_speech_over_echo),
        ("5. Silence passthrough", test_silence_passthrough),
        ("6. Grace period", test_grace_period),
        ("7. Adaptive echo floor", test_adaptive_echo_floor),
        ("8. Activation timestamp resets", test_activation_timestamp_resets),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            print(f"\n  Test {name}...")
            fn()
            passed += 1
            print(f"  ✓ PASS")
        except Exception as e:
            failed += 1
            print(f"  ✗ FAIL: {e}")

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed}/{passed + failed} passed")
    if failed:
        print(f"  ✗ {failed} FAILED")
    else:
        print(f"  ✓ ALL PASS")
    print()
