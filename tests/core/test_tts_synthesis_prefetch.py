"""Synthesis must overlap playback instead of queueing behind it.

Synthesis is the bottleneck (Fish Audio: ~5s per sentence). The worker used to
synthesise sentence N+1 only after sentence N had finished, so a reply cost the
sum of every sentence. It now looks one sentence ahead.
"""

from __future__ import annotations

import asyncio
import time

from distr.core.agent.libs import AudioRawFrame
from distr.core.agent.services.tts import tts_pipeline_mixin as mixin

SYNTH_SECONDS = 0.20
PLAY_SECONDS = 0.20


class _Stub(mixin.TTSPipelineMixin):
    _volume_in_run_tts = True

    def __init__(self) -> None:
        self._init_tts_pipeline_state()
        self._cancelled = False
        self.marks: dict[str, float] = {}
        self.played: list[str] = []

    def _mark(self, label: str) -> None:
        self.marks.setdefault(label, time.monotonic())

    async def push_frame(self, frame, direction=None) -> None:
        if isinstance(frame, AudioRawFrame):
            # The sentence travels in the audio bytes so playback is identifiable
            # without any shared mutable state.
            label = frame.audio.decode()
            self.played.append(label)
            self._mark(f"play_start:{label}")
            await asyncio.sleep(PLAY_SECONDS)
            self._mark(f"play_end:{label}")
        return None

    def _resolve_tts_push_direction(self, direction):
        return direction

    async def run_tts(self, text: str):
        self._mark(f"synth_start:{text}")
        await asyncio.sleep(SYNTH_SECONDS)
        yield AudioRawFrame(audio=text.encode(), sample_rate=44100, num_channels=1)
        self._mark(f"synth_end:{text}")


async def _speak_all(sentences: list[str]) -> _Stub:
    stub = _Stub()
    stub._ensure_speak_worker()
    for sentence in sentences:
        await stub._enqueue_sentence(sentence, None)
    await stub._speak_queue.join()
    stub._speak_worker_task.cancel()
    try:
        await stub._speak_worker_task
    except (asyncio.CancelledError, Exception):
        pass
    return stub


def test_the_next_sentence_is_synthesised_while_the_current_one_plays():
    sentences = ["one", "two", "three"]
    stub = asyncio.run(_speak_all(sentences))

    for first, second in zip(sentences, sentences[1:]):
        assert stub.marks[f"synth_start:{second}"] < stub.marks[f"play_end:{first}"], (
            f"{second!r} waited for {first!r} to finish playing"
        )


def test_every_sentence_still_plays_in_order():
    sentences = ["alpha", "beta", "gamma"]
    stub = asyncio.run(_speak_all(sentences))

    assert stub.played == sentences


def test_prefetch_is_skipped_for_providers_with_their_own_playback():
    class _OwnPath(_Stub):
        async def _play_queued_sentence(self, sentence, generation, direction) -> None:
            return None

    assert _OwnPath()._can_prefetch_synthesis() is False
    assert _Stub()._can_prefetch_synthesis() is True
