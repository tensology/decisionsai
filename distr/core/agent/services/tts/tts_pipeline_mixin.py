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
# A confirmed user barge-in must be honoured. Pipecat broadcasts the
# InterruptionFrame to all processors asynchronously, so a momentary boolean
# marker is already cleared by the time the TTS sees the frame. Keep the
# barge-in valid for a short window instead.
BARGEIN_ACCEPT_WINDOW_SEC = 1.5
# How many spoken sentences may wait ahead of playback. Providers such as
# FishAudio take about ten seconds per sentence, so an unbounded queue let a
# normal reply fall 97 seconds behind and then play stale speech.
# Conversational backlog cap: when live speech falls behind we drop stale lines
# rather than reading minutes-old sentences.
SPEAK_QUEUE_MAX = 4
# A deliberate read-aloud (About "Read about", a long document handed to
# speak_text_directly) is the opposite case: the user asked for every word, so
# the queue is given room and nothing is dropped.
SPEAK_QUEUE_MAX_READ_ALL = 512


async def _iterate_frames(frames: list):
    """Replay frames that were synthesised ahead of playback."""
    for frame in frames:
        yield frame


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
        self._read_all_sentences = False
        self._prefetched_frames = None
        self._speak_queue = None
        self._speak_worker_task = None
        self._speak_busy = False
        self._bargein_at = 0.0

    def mark_bargein(self) -> None:
        """Record a confirmed user barge-in.

        The next InterruptionFrame (even if it arrives after this call returns)
        is then treated as authoritative and is not discarded as a stale race.
        """
        self._bargein_at = time.monotonic()

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
        if getattr(self, "_accept_bargein_interrupt", False):
            return False
        # Honor a recently confirmed user barge-in even though its broadcast
        # InterruptionFrame arrives asynchronously.
        bargein_at = float(getattr(self, "_bargein_at", 0.0) or 0.0)
        if bargein_at > 0.0 and (time.monotonic() - bargein_at) < BARGEIN_ACCEPT_WINDOW_SEC:
            return False
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

    def begin_read_all(self) -> None:
        """Mark the next speech as a complete read-aloud that must lose nothing.

        Only a flag is set. The queue object is deliberately left alone: the
        worker captured a reference to it, so swapping in a bigger queue left the
        worker draining the old one and the read-aloud produced no audio at all.
        """
        self._read_all_sentences = True

    def end_read_all(self) -> None:
        self._read_all_sentences = False

    def _ensure_speak_worker(self) -> None:
        if self._speak_queue is None:
            # Sized for a read-aloud; conversational speech is kept short by the
            # explicit drop below, not by the queue capacity.
            self._speak_queue = asyncio.Queue(maxsize=SPEAK_QUEUE_MAX_READ_ALL)
        if self._speak_worker_task is None or self._speak_worker_task.done():
            self._speak_worker_task = asyncio.create_task(self._speak_worker_loop())

    async def _speak_worker_loop(self) -> None:
        queue = self._speak_queue
        if queue is None:
            return
        # One sentence of lookahead, already synthesised and waiting to play.
        pending: Any = None
        pending_frames: list | None = None
        while True:
            if pending is not None:
                item, frames = pending, pending_frames
                pending = pending_frames = None
            else:
                item, frames = await queue.get(), None
            sentence, generation, direction = item
            lookahead: Any = None
            lookahead_task: asyncio.Task | None = None
            try:
                if generation != self._tts_generation or self._cancelled:
                    continue
                self._speak_busy = True
                # Start the next sentence now, so its synthesis overlaps with the
                # audio of this one instead of following it.
                if self._can_prefetch_synthesis() and not queue.empty():
                    try:
                        lookahead = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        lookahead = None
                    if lookahead is not None:
                        lookahead_task = asyncio.create_task(
                            self._materialize_sentence(lookahead[0])
                        )
                try:
                    self._prefetched_frames = frames
                    await self._play_queued_sentence(sentence, generation, direction)
                finally:
                    self._prefetched_frames = None
            finally:
                self._speak_busy = False
                queue.task_done()
            if lookahead is not None:
                keep = False
                try:
                    lookahead_frames = await lookahead_task  # type: ignore[arg-type]
                    keep = bool(lookahead_frames) and (
                        lookahead[1] == self._tts_generation and not self._cancelled
                    )
                except Exception:
                    logger.debug("TTS: lookahead synthesis failed", exc_info=True)
                if keep:
                    pending, pending_frames = lookahead, lookahead_frames
                else:
                    # The item was already taken from the queue; release its slot
                    # or a pending queue.join() would wait forever.
                    queue.task_done()

    async def _enqueue_sentence(self, sentence: str, direction: Any) -> None:
        clean = (sentence or "").strip()
        if not clean:
            return
        # Hold here (not in _play_queued_sentence): providers such as Kokoro override
        # that method, but they all enqueue through this one. Never talk over the
        # splash video or the About chime.
        try:
            from distr.core.about_audio import about_audio_remaining

            remaining = about_audio_remaining()
            if remaining > 0:
                logger.info("TTS: waiting %.2fs for startup audio to finish", remaining)
            while remaining > 0:
                if getattr(self, "_cancelled", False):
                    return
                await asyncio.sleep(min(0.1, remaining))
                remaining = about_audio_remaining()
        except Exception:
            pass
        self._ensure_speak_worker()
        queue = self._speak_queue
        assert queue is not None
        item = (clean, self._tts_generation, direction)
        if getattr(self, "_read_all_sentences", False):
            # Wait for a slot instead of dropping: a read-aloud the user asked for
            # must be complete, and dropping here silently ate whole sentences.
            await queue.put(item)
            return
        # Speech has fallen behind. Drop the oldest pending line rather than
        # reading minutes-old sentences: a 10s-per-sentence provider turned an
        # unbounded queue into a 97s backlog of stale speech.
        dropped = 0
        try:
            while queue.qsize() >= SPEAK_QUEUE_MAX:
                _stale_sentence, _gen, _dir = queue.get_nowait()
                queue.task_done()
                dropped += 1
        except asyncio.QueueEmpty:
            pass
        if dropped:
            logger.warning(
                "TTS: dropped %d stale queued sentence(s) to keep speech current (max=%d)",
                dropped,
                SPEAK_QUEUE_MAX,
            )
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            # The worker is about to free a slot; losing one line is preferable
            # to blocking the response on a queue that is already behind.
            logger.warning("TTS: speak queue still full; dropping the newest sentence")

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

    def _can_prefetch_synthesis(self) -> bool:
        """True when the base streaming implementation will play the audio.

        Providers that override ``_play_queued_sentence`` (Kokoro synthesises
        locally and fast) keep their own path; only the cloud-streaming providers
        pay enough synthesis latency for a lookahead to be worth it.
        """
        return type(self)._play_queued_sentence is TTSPipelineMixin._play_queued_sentence

    async def _materialize_sentence(self, sentence: str) -> list:
        """Synthesise a sentence to frames ahead of time, without pushing them.

        Synthesis is the bottleneck (Fish Audio: ~5s per sentence), so running it
        while the previous sentence is still playing turns per-sentence cost into
        overlap instead of addition.
        """
        return [frame async for frame in self.run_tts(sentence)]  # type: ignore[attr-defined]

    async def _play_queued_sentence(self, sentence: str, generation: int, direction: Any) -> None:
        """Default: stream audio frames from ``run_tts``."""
        if not hasattr(self, "run_tts"):
            logger.warning("TTS: _play_queued_sentence called but run_tts is missing")
            return
        prefetched = getattr(self, "_prefetched_frames", None)
        if prefetched:
            await self._push_sentence_frames(
                sentence, _iterate_frames(prefetched), generation, direction
            )
            return
        await self._push_sentence_frames(
            sentence, self.run_tts(sentence), generation, direction  # type: ignore[attr-defined]
        )

    async def _push_sentence_frames(
        self, sentence: str, frame_source: Any, generation: int, direction: Any
    ) -> None:
        """Push one sentence's frames to the transport, in order."""
        push_direction = self._resolve_tts_push_direction(direction)
        logger.info("TTS SENTENCE EMIT: sentence=%r", sentence[:120])
        sentence_started = time.monotonic()
        first_audio_at = None
        audio_frame_count = 0
        truncated = False
        async for audio_frame in frame_source:
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
                    timing = getattr(self, "_audio_timing", None)
                    if timing is not None:
                        timing.mark("tts_first_audio", at=first_audio_at)
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
