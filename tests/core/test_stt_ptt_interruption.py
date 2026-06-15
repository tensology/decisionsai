"""Regression tests for STT PTT interruption coalescing."""

from unittest.mock import MagicMock

from distr.core.agent.services.stt.base import BaseSTTService


class _DummySTT(BaseSTTService):
    async def run_stt(self, audio_bytes):
        if False:
            yield None


def test_set_ptt_active_without_queue_interruption_does_not_arm_pending_interrupt():
    stt = _DummySTT(event_queue=MagicMock())
    stt._pipeline_direction = object()
    stt._event_loop = MagicMock()
    stt._event_loop.is_running.return_value = True

    stt.set_ptt_active(True, queue_interruption=False)

    assert stt._ptt_active is True
    assert stt._pending_interruption is False


def test_set_ptt_active_with_queue_interruption_arms_pending_interrupt():
    stt = _DummySTT(event_queue=MagicMock())
    stt._pipeline_direction = object()
    stt._event_loop = MagicMock()
    stt._event_loop.is_running.return_value = True

    stt.set_ptt_active(True, queue_interruption=True)

    assert stt._ptt_active is True
    assert stt._pending_interruption is True
