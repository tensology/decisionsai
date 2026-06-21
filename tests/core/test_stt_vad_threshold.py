"""Regression tests for VAD threshold mapping into hands-free barge-in gating."""

import time
from unittest.mock import MagicMock

import numpy as np

from distr.core.agent.services.stt.base import BaseSTTService


class _DummySTT(BaseSTTService):
    async def run_stt(self, audio_bytes):
        if False:
            yield None


class _FakeReferenceBuffer:
    def __init__(self):
        self.is_active = False
        self._activated_at = 0.0

    def set_active(self, active: bool):
        self.is_active = active
        if active:
            self._activated_at = time.time()

    def seconds_since_activation(self) -> float:
        return max(0.0, time.time() - self._activated_at)


def _tone_bytes(amplitude: float, sample_rate: int = 16000) -> bytes:
    samples = np.arange(int(sample_rate * 0.02), dtype=np.float32) / sample_rate
    tone = amplitude * np.sin(2 * np.pi * 440.0 * samples)
    return (np.clip(tone, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def test_set_vad_threshold_scales_hands_free_bargein_strictness():
    stt = _DummySTT(event_queue=MagicMock())

    stt.set_vad_threshold(0)
    low_multiplier = stt._echo_floor_multiplier
    low_floor = stt._echo_floor_min
    low_chunks = stt._bargein_consecutive_required

    stt.set_vad_threshold(100)

    assert stt._echo_floor_multiplier > low_multiplier
    assert stt._echo_floor_min > low_floor
    assert stt._bargein_consecutive_required > low_chunks


def test_vad_threshold_changes_continuous_mode_bargein_gate():
    ref_buf = _FakeReferenceBuffer()
    ref_buf.set_active(True)
    ref_buf._activated_at = time.time() - 2.0

    stt = _DummySTT(event_queue=MagicMock(), aec_ref_buf=ref_buf, is_hands_free=True)
    stt._echo_floor_rms = 0.02

    medium_energy = _tone_bytes(0.05)

    stt.set_vad_threshold(0)
    stt._pre_buffer.clear()
    stt._pre_buffer.extend([medium_energy] * 15)
    assert stt._check_bargein_energy() is True

    stt.set_vad_threshold(100)
    stt._pre_buffer.clear()
    stt._pre_buffer.extend([medium_energy] * 15)
    assert stt._check_bargein_energy() is False
