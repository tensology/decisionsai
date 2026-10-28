"""The spoken-sentence queue must stay bounded so speech never plays stale."""

from __future__ import annotations

import asyncio

from distr.core.agent.services.tts import tts_pipeline_mixin as mixin


class _Stub(mixin.TTSPipelineMixin):
    def __init__(self) -> None:
        self._speak_queue = None
        self._speak_worker_task = None
        self._speak_busy = False
        self._tts_generation = 1
        self._cancelled = False
        self._processed_sentences = set()
        self.played: list[str] = []
        self.gate = asyncio.Event()

    async def _play_queued_sentence(self, sentence, generation, direction):  # noqa: D401
        # Stand in for a slow provider: hold the worker so the queue fills.
        self.played.append(sentence)
        await self.gate.wait()


def _drain(queue) -> list[str]:
    items: list[str] = []
    while not queue.empty():
        items.append(queue.get_nowait()[0])
        queue.task_done()
    return items


def test_speak_queue_is_bounded():
    async def scenario():
        stub = _Stub()
        stub._ensure_speak_worker()
        try:
            return stub._speak_queue.maxsize
        finally:
            task = stub._speak_worker_task
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    assert asyncio.run(scenario()) == mixin.SPEAK_QUEUE_MAX


def test_a_long_reply_drops_stale_lines_and_keeps_the_newest():
    surviving = []

    async def scenario():
        stub = _Stub()
        for index in range(12):
            await stub._enqueue_sentence(f"sentence {index}", None)
        surviving.extend(_drain(stub._speak_queue))
        stub.gate.set()
        task = stub._speak_worker_task
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return stub

    stub = asyncio.run(scenario())

    assert len(surviving) <= mixin.SPEAK_QUEUE_MAX
    assert "sentence 11" in surviving
    # The earliest lines were the ones dropped, not the newest.
    assert "sentence 0" not in surviving
    assert "sentence 1" not in surviving


def test_empty_sentences_are_ignored():
    async def scenario():
        stub = _Stub()
        await stub._enqueue_sentence("   ", None)
        await stub._enqueue_sentence("", None)
        # Nothing was queued, so the worker is never even created.
        return stub._speak_queue is None or stub._speak_queue.empty()

    assert asyncio.run(scenario()) is True
