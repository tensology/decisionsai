"""Offline end-to-end hands-free echo / barge-in tests.

Drives ReferenceBuffer + WebRTCAECFilter + SileroVADAnalyzer + BaseSTTService
barge-in logic with synthetic speaker echo (see vad_echo_harness.py).
No audio devices, no network, no settings DB.
"""
import os
import sys

import pytest

pytest.importorskip("pipecat.audio.vad.silero")
pytest.importorskip("pywebrtc_audio")

sys.path.insert(0, os.path.dirname(__file__))
import vad_echo_harness as H  # noqa: E402

try:
    H.load_clips()
except Exception as exc:  # pragma: no cover
    pytest.skip(f"vad_echo fixtures unavailable: {exc}", allow_module_level=True)


@pytest.mark.parametrize("delay_ms", [60, 90, 150])
@pytest.mark.parametrize("echo_gain", [0.45, 0.8])
def test_case1_tts_echo_never_starts_user_turn(delay_ms, echo_gain):
    r = H.run_case(1, delay_ms=delay_ms, echo_gain=echo_gain)
    assert not r.bargeins, r.summary()
    assert not r.vad_starts, r.summary()


@pytest.mark.parametrize("delay_ms", [20, 40])
def test_case1_echo_path_not_longer_than_reference_delay_is_gated(delay_ms):
    """Known AEC limitation: when the real speaker->mic delay is <= the
    configured aec_reference_delay_ms (40 ms) the far-end is non-causal and
    AEC3 only reaches ~14 dB ERLE. The hands-free echo gate must still cut
    false barge-ins from ~12-16 per clip (pre-fix) to at most 2."""
    r = H.run_case(1, delay_ms=delay_ms)
    assert len(r.bargeins) <= 2, r.summary()


@pytest.mark.parametrize("delay_ms", [60, 90, 150])
@pytest.mark.parametrize("user_at_s", [2.5, 5.0])
def test_case2_user_speech_over_echo_barges_in(delay_ms, user_at_s):
    r = H.run_case(2, delay_ms=delay_ms, user_at_s=user_at_s)
    # If VAD resolves on the exact frame playback ends, recognizing the user
    # turn is correct and no pipeline interruption is necessary.
    starts = r.bargeins or ([r.vad_starts[0]] if r.vad_starts and r.vad_starts[0] >= r.tts_stop_s else [])
    assert starts, r.summary()
    first = starts[0]
    assert first >= r.user_onset, f"barge-in before user spoke: {r.summary()}"
    assert first - r.user_onset <= 1.0, f"barge-in too slow: {r.summary()}"
    # Transcript proxy: captured audio follows the user, not the echo.
    assert r.corr_user > 0.8 and r.corr_user > r.corr_echo + 0.3, (r.corr_user, r.corr_echo)


def test_vad_params_slider_keeps_start_secs_and_ptt_unchanged():
    from distr.core.agent.constants import VAD_START_SECS
    from distr.core.agent.vad_params import vad_param_values, build_vad_params
    assert vad_param_values(50, hands_free=False) == {"confidence": 0.5, "start_secs": VAD_START_SECS}
    hf = build_vad_params(80, hands_free=True)
    assert hf.start_secs == VAD_START_SECS and hf.confidence == 0.8 and hf.min_volume == 0.6


def test_echo_gate_is_hands_free_only():
    from distr.core.audio.echo_canceller import ReferenceBuffer
    ref = ReferenceBuffer(sample_rate=24000)
    ref.set_active(True)
    stt = H._make_stt(ref, None)
    assert stt._hands_free_echo_gate_active()
    stt.set_hands_free(False)
    assert not stt._hands_free_echo_gate_active()
    stt.set_hands_free(True)
    stt._is_dictating = True
    assert not stt._hands_free_echo_gate_active()
