"""Regression tests for STT PTT interruption coalescing."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from distr.core.agent.libs import TranscriptionFrame

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
    assert stt._stt_cancelled is False


def test_set_ptt_active_with_queue_interruption_arms_pending_interrupt():
    stt = _DummySTT(event_queue=MagicMock())
    stt._pipeline_direction = object()
    stt._event_loop = MagicMock()
    stt._event_loop.is_running.return_value = True

    stt.set_ptt_active(True, queue_interruption=True)

    assert stt._ptt_active is True
    assert stt._pending_interruption is True
    assert stt._stt_cancelled is True


def test_empty_agent_ptt_release_schedules_terminal_transcription_feedback():
    stt = _DummySTT(event_queue=MagicMock())
    stt._ptt_active = True
    stt._is_dictating = False
    stt._schedule_empty_ptt_transcription_feedback = MagicMock()

    stt.set_ptt_active(False)

    stt._schedule_empty_ptt_transcription_feedback.assert_called_once_with()


def test_chat_switch_drops_inflight_ptt_result_and_next_capture_still_transcribes(monkeypatch):
    async def exercise():
        stt = _DummySTT(event_queue=MagicMock())
        started = asyncio.Event()
        finish = asyncio.Event()
        sent = AsyncMock()
        monkeypatch.setattr(BaseSTTService.__mro__[1], "push_frame", sent)

        async def transcribe(direction):
            started.set()
            await finish.wait()
            await stt.push_frame(TranscriptionFrame("test phrase", "", ""), direction)

        stt._process_ptt_buffer_immediate = transcribe
        stt.set_ptt_active(True, queue_interruption=False)
        stale_capture = stt._ptt_capture
        stale = asyncio.create_task(stt._flush_ptt_capture(None, stale_capture))
        await started.wait()
        stt.discard_voice_capture()
        stt.set_ptt_active(True, queue_interruption=False)
        finish.set()
        await stale
        sent.assert_not_awaited()

        await stt._flush_ptt_capture(None, stt._ptt_capture)
        assert sent.await_args.args[0].text == "test phrase"
        queued = sent.await_args.args[0]
        stt.discard_voice_capture()
        assert queued.metadata["ptt_capture"]["valid"] is False

    asyncio.run(exercise())


def test_hands_free_cutoff_notifies_player_immediately():
    queue = MagicMock()
    stt = _DummySTT(event_queue=queue, is_hands_free=True)

    stt._notify_tts_interrupted()

    queue.put.assert_called_once_with(
        ("tts_stopped", {"duration": 0.0, "interrupted": True}),
        block=False,
    )
