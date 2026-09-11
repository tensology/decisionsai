"""Barge-in interruptions must be distinguishable from stale transport frames."""

import asyncio
from types import SimpleNamespace

from distr.core.agent.services.stt.base import BaseSTTService
from distr.core.agent.libs import InterruptionFrame


class _TestSTTService(BaseSTTService):
    async def run_stt(self, _audio):
        return None


def test_stt_marks_interruption_until_downstream_accepts_it():
    service = _TestSTTService.__new__(_TestSTTService)
    output = SimpleNamespace(_accept_bargein_interrupt=False)
    tts = SimpleNamespace(_accept_bargein_interrupt=False)
    observed = []
    service._audio_output_transport = output
    service._tts_service = tts

    async def push_frame(frame, direction):
        observed.append(
            (
                isinstance(frame, InterruptionFrame),
                output._accept_bargein_interrupt,
                tts._accept_bargein_interrupt,
            )
        )

    service.push_frame = push_frame

    asyncio.run(service._send_interruption(None))

    assert observed == [(True, True, True)]
    assert output._accept_bargein_interrupt is False
    assert tts._accept_bargein_interrupt is False


def test_stt_clears_bargein_marker_when_downstream_fails():
    service = _TestSTTService.__new__(_TestSTTService)
    output = SimpleNamespace(_accept_bargein_interrupt=False)
    tts = SimpleNamespace(_accept_bargein_interrupt=False)
    service._audio_output_transport = output
    service._tts_service = tts

    async def push_frame(_frame, _direction):
        raise RuntimeError("simulated downstream failure")

    service.push_frame = push_frame

    asyncio.run(service._send_interruption(None))

    assert output._accept_bargein_interrupt is False
    assert tts._accept_bargein_interrupt is False


def test_continuous_bargein_closes_player_before_forwarding_interrupt():
    service = _TestSTTService.__new__(_TestSTTService)
    service._is_hands_free = True
    service._is_dictating = False
    service._aec_ref_buf = SimpleNamespace(is_active=True)
    service._audio_output_transport = SimpleNamespace(_accept_bargein_interrupt=False)
    service._tts_service = SimpleNamespace(_accept_bargein_interrupt=False)
    events = []
    observed = []
    service.event_queue = SimpleNamespace(put=lambda item, block=False: events.append(item))

    async def push_interruption_task_frame_and_wait():
        observed.append(("interrupt", list(events)))

    service.push_interruption_task_frame_and_wait = push_interruption_task_frame_and_wait
    asyncio.run(service._push_bargein_interruption())

    assert events == [("tts_stopped", {"duration": 0.0, "interrupted": True})]
    assert observed == [("interrupt", events)]


def test_direct_continuous_interruption_closes_player():
    service = _TestSTTService.__new__(_TestSTTService)
    service._is_hands_free = True
    service._is_dictating = False
    service._aec_ref_buf = SimpleNamespace(is_active=True)
    events = []
    service.event_queue = SimpleNamespace(put=lambda item, block=False: events.append(item))

    assert service._should_filter_interruption(InterruptionFrame()) is False
    assert events == [("tts_stopped", {"duration": 0.0, "interrupted": True})]


def test_gate_uses_measured_reference_dominance_not_correlation_alone():
    service = _TestSTTService.__new__(_TestSTTService)
    service._aec_ref_buf = SimpleNamespace(is_active=True)
    service._aec_filter = SimpleNamespace(
        last_metrics={
            "reference_correlation": 0.9,
            "reference_rms": 0.10,
            "input_rms": 0.05,
            "residual_rms": 0.02,
        },
    )

    assert service._is_reference_dominated() is True


def test_gate_keeps_correlated_double_talk_interruptible():
    service = _TestSTTService.__new__(_TestSTTService)
    service._aec_ref_buf = SimpleNamespace(is_active=True)
    service._aec_filter = SimpleNamespace(
        last_metrics={
            "reference_correlation": 0.8,
            "reference_rms": 0.06,
            "input_rms": 0.09,
            "residual_rms": 0.08,
        },
    )

    assert service._is_reference_dominated() is False


def test_unsettled_aec_with_speech_level_residual_is_double_talk():
    metrics = {
        "reference_correlation": 0.23,
        "input_rms": 0.217,
        "residual_rms": 0.217,
        "reference_rms": 0.080,
    }
    assert BaseSTTService._reference_dominance_from_metrics(metrics) is False


def test_unsettled_aec_echo_level_remains_unknown():
    metrics = {
        "reference_correlation": 0.23,
        "input_rms": 0.091,
        "residual_rms": 0.122,
        "reference_rms": 0.052,
    }
    assert BaseSTTService._reference_dominance_from_metrics(metrics) is None


def test_quiet_room_human_onset_is_double_talk_at_lower_level():
    metrics = {
        "reference_correlation": 0.05,
        "input_rms": 0.073,
        "residual_rms": 0.073,
        "reference_rms": 0.052,
    }
    assert BaseSTTService._reference_dominance_from_metrics(metrics) is False


def test_current_measured_double_talk_overrides_aec_warmup_history():
    service = _TestSTTService.__new__(_TestSTTService)
    service._aec_ref_buf = SimpleNamespace(is_active=True)
    service._aec_filter = SimpleNamespace(
        last_metrics={
            "reference_correlation": 0.21,
            "input_rms": 0.20,
            "residual_rms": 0.14,
            "reference_rms": 0.08,
        },
    )
    service._echo_dominance_history = [None, None, True, None]
    assert service._is_reference_dominated() is False


def test_moderately_correlated_double_talk_overrides_echo_history():
    service = _TestSTTService.__new__(_TestSTTService)
    service._aec_ref_buf = SimpleNamespace(is_active=True)
    service._aec_filter = SimpleNamespace(
        last_metrics={
            "reference_correlation": 0.50,
            "input_rms": 0.072,
            "residual_rms": 0.060,
            "reference_rms": 0.046,
        },
    )
    service._echo_dominance_history = [True, None, True]
    assert service._is_reference_dominated() is False


def test_gate_requires_three_consecutive_non_echo_frames():
    service = _TestSTTService.__new__(_TestSTTService)
    service._is_hands_free = True
    service._is_dictating = False
    service._aec_ref_buf = SimpleNamespace(is_active=True)
    service._aec_filter = SimpleNamespace(
        last_metrics={
            "reference_correlation": 0.8,
            "reference_rms": 0.02,
            "input_rms": 0.08,
            "residual_rms": 0.07,
        },
    )
    service._echo_dominance_history = [True, True, False, False]
    assert service._is_reference_dominated() is False

    service._echo_dominance_history = [True, True, False, False, True]
    service._aec_filter.last_metrics["reference_correlation"] = 0.8
    service._aec_filter.last_metrics["reference_rms"] = 0.08
    service._aec_filter.last_metrics["input_rms"] = 0.05
    service._aec_filter.last_metrics["residual_rms"] = 0.02
    assert service._is_reference_dominated() is True


def test_bargein_energy_decision_suppresses_echo_and_accepts_double_talk():
    import numpy as np

    service = _TestSTTService.__new__(_TestSTTService)
    service._aec_ref_buf = SimpleNamespace(
        is_active=True,
        seconds_since_activation=lambda: 2.0,
    )
    service._pre_buffer = [
        (np.full(320, 0.06, dtype=np.float32) * 32767).astype(np.int16).tobytes()
        for _ in range(10)
    ]
    service._bargein_consecutive_required = 3
    service._echo_floor_rms = 0.02
    service._echo_floor_multiplier = 1.8
    service._echo_floor_min = 0.03
    service._aec_filter = SimpleNamespace(
        last_metrics={
            "reference_correlation": 0.8,
            "reference_rms": 0.08,
            "input_rms": 0.06,
            "residual_rms": 0.03,
        },
    )

    assert service._check_bargein_energy() is False

    service._aec_filter.last_metrics = {
        "reference_correlation": 0.8,
        "reference_rms": 0.05,
        "input_rms": 0.09,
        "residual_rms": 0.08,
    }
    assert service._check_bargein_energy() is True
