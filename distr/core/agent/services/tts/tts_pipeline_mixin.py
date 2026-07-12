"""Shared TTS pipeline helpers: queued synthesis, interrupt races, hot-swap abort."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from distr.core.agent.libs import (
    AudioRawFrame,
    OutputAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)

logger = logging.getLogger(__name__)

STALE_INTERRUPT_GRACE_SEC = 2.0
STALE_CANCELLED_CLEAR_SEC = 1.5


class TTSPipelineMixin:
    """Non-blocking sentence queue + stale interrupt/cancel recovery."""

    _cancelled_since: float
    _tts_generation: int
    _speak_queue: asyncio.Queue | None
    _speak_worker_task: asyncio.Task | None
    _speak_busy: bool
    _llm_response_started_at: float
    _cancelled: bool
    _ptt_active: bool
    _text_buffer: str
    _processed_sentences: set
    _speech_volume: float

    def _init_tts_pipeline_state(self) -> None:
        self._cancelled_since = 0.0
        self._tts_generation = 0
        self._speak_queue = None
        self._speak_worker_task = None
        self._speak_busy = False

    def abort_pending_synthesis(self, *, clear_buffer: bool = True) -> None:
        """Drop queued/in-flight synthesis (hot-swap or hard interrupt)."""
        self._tts_generation += 1
        self._cancelled = True
        self._cancelled_since = time.monotonic()
        if clear_buffer:
            self._text_buffer = ""
            if hasattr(self, "_sentence_batch_hold"):
                self._sentence_batch_hold = []
            if hasattr(self, "_processed_sentences"):
                self._processed_sentences.clear()
        queue = self._speak_queue
        if queue is not None:
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break

    def is_stale_interrupt_frame(self) -> bool:
        started = float(getattr(self, "_llm_response_started_at", 0.0) or 0.0)
        if started <= 0:
            return False
        age = time.monotonic() - started
        return age < STALE_INTERRUPT_GRACE_SEC

    def maybe_clear_stale_cancelled_for_text(self) -> bool:
        """Return True when a TextFrame may be processed."""
        if not getattr(self, "_cancelled", False):
            return True
        age = time.monotonic() - float(getattr(self, "_cancelled_since", 0.0) or 0.0)
        if not getattr(self, "_ptt_active", False) and age > STALE_CANCELLED_CLEAR_SEC:
            logger.warning(
                "TTS: Auto-clearing stale _cancelled state before TextFrame (age=%.2fs)",
                age,
            )
            self._cancelled = False
            self._cancelled_since = 0.0
            return True
        return False

    def reset_tts_response_start(self) -> None:
        self._llm_response_started_at = time.monotonic()
        self._text_buffer = ""
        if hasattr(self, "_sentence_batch_hold"):
            self._sentence_batch_hold = []
        self._cancelled = False
        self._cancelled_since = 0.0
        if hasattr(self, "_processed_sentences"):
            self._processed_sentences.clear()

    def _ensure_speak_worker(self) -> None:
        if self._speak_queue is None:
            self._speak_queue = asyncio.Queue()
        if self._speak_worker_task is None or self._speak_worker_task.done():
            self._speak_worker_task = asyncio.create_task(self._speak_worker_loop())

    async def _speak_worker_loop(self) -> None:
        queue = self._speak_queue
        if queue is None:
            return
        while True:
            sentence, generation, direction = await queue.get()
            try:
                if generation != self._tts_generation or self._cancelled:
                    continue
                self._speak_busy = True
                await self._play_queued_sentence(sentence, generation, direction)
            finally:
                self._speak_busy = False
                queue.task_done()

    async def _enqueue_sentence(self, sentence: str, direction: Any) -> None:
        clean = (sentence or "").strip()
        if not clean:
            return
        self._ensure_speak_worker()
        assert self._speak_queue is not None
        await self._speak_queue.put((clean, self._tts_generation, direction))

    async def _drain_speak_queue(self) -> None:
        queue = self._speak_queue
        if queue is None:
            return
        started = time.monotonic()
        queued = queue.qsize()
        await queue.join()
        while self._speak_busy:
            await asyncio.sleep(0.02)
        elapsed = time.monotonic() - started
        if elapsed >= 0.750:
            logger.warning(
                "TTS: response completion waited %.3fs for synthesis/playback queue "
                "(queued_at_start=%d)",
                elapsed,
                queued,
            )

    async def _play_queued_sentence(self, sentence: str, generation: int, direction: Any) -> None:
        """Default: stream audio frames from ``run_tts``."""
        if not hasattr(self, "run_tts"):
            logger.warning("TTS: _play_queued_sentence called but run_tts is missing")
            return
        push_direction = self._resolve_tts_push_direction(direction)
        logger.info("TTS SENTENCE EMIT: sentence=%r", sentence[:120])
        sentence_started = time.monotonic()
        first_audio_at = None
        audio_frame_count = 0
        truncated = False
        async for audio_frame in self.run_tts(sentence):  # type: ignore[attr-defined]
            if generation != self._tts_generation or self._cancelled:
                truncated = True
                break
            if isinstance(audio_frame, (TTSStartedFrame, TTSStoppedFrame)):
                # Transport needs TTSStartedFrame to disable VAD barge-in and
                # prepare the output stream before the first audio chunk.
                try:
                    await self.push_frame(audio_frame, push_direction)  # type: ignore[attr-defined]
                except Exception as e:
                    logger.error("TTS: Error pushing lifecycle frame: %s", e, exc_info=True)
                    truncated = True
                    break
                continue
            is_audio = isinstance(audio_frame, AudioRawFrame) or (
                OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)
            )
            if not is_audio:
                continue
            try:
                self._apply_volume_to_audio_frame(audio_frame)
                await self.push_frame(audio_frame, push_direction)  # type: ignore[attr-defined]
                if first_audio_at is None:
                    first_audio_at = time.monotonic()
                audio_frame_count += 1
            except Exception as e:
                logger.error("TTS: Error pushing frame: %s", e, exc_info=True)
                truncated = True
                break
        if truncated and audio_frame_count > 0:
            logger.warning(
                "TTS: sentence playback truncated after %s audio frame(s) (generation/cancelled)",
                audio_frame_count,
            )
        elif audio_frame_count > 0:
            logger.debug("TTS: Pushed %s audio frames for sentence", audio_frame_count)
        elapsed = time.monotonic() - sentence_started
        first_audio_latency = (
            first_audio_at - sentence_started if first_audio_at is not None else None
        )
        if elapsed >= 1.000 or first_audio_latency is None or first_audio_latency >= 0.750:
            logger.warning(
                "TTS: sentence timing provider=%s total=%.3fs first_audio=%s frames=%d truncated=%s",
                type(self).__name__,
                elapsed,
                f"{first_audio_latency:.3f}s" if first_audio_latency is not None else "none",
                audio_frame_count,
                truncated,
            )

    def _resolve_tts_push_direction(self, direction: Any) -> Any:
        if direction is not None:
            return direction
        stored = getattr(self, "_pipeline_direction", None)
        if stored is not None:
            return stored
        try:
            from pipecat.processors.frame_processor import FrameDirection

            return FrameDirection.DOWNSTREAM
        except Exception:
            return direction

    def _apply_volume_to_audio_frame(self, audio_frame: Any) -> None:
        if getattr(self, "_volume_in_run_tts", False):
            return
        import numpy as np

        audio_array = np.frombuffer(audio_frame.audio, dtype=np.int16).astype(np.float32)
        audio_array = audio_array / 32767.0
        audio_array = audio_array * float(getattr(self, "_speech_volume", 1.0))
        audio_array = np.clip(audio_array, -1.0, 1.0)
        audio_frame.audio = (audio_array * 32767.0).astype(np.int16).tobytes()
