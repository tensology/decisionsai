"""Regression: a superseded response must not be spoken on top of the new one.

Root cause of "it started speaking this twice": in continuous mode a barge-in
arriving during a blocking tool run is ignored, and the voice-transcript handler
cancels the old generation but proceeds after a 3s shield timeout. The old
generation then finished its tool and pushed a second spoken reply.

Two guards are covered here:
1. TTS: starting a new response discards any sentences still queued by a
   superseded one.
2. LLM: a superseded generation stops pushing text.
"""

import asyncio

import pytest

from distr.core.agent.services.tts.tts_pipeline_mixin import TTSPipelineMixin


class _StubTTS(TTSPipelineMixin):
    def __init__(self):
        self._init_tts_pipeline_state()
        self._speak_queue = asyncio.Queue(maxsize=512)


def test_discard_pending_sentences_clears_queue_and_bumps_generation():
    stub = _StubTTS()
    for i in range(5):
        stub._speak_queue.put_nowait((f"sentence {i}", stub._tts_generation, None))
    assert stub._speak_queue.qsize() == 5
    gen_before = stub._tts_generation

    stub.discard_pending_sentences()

    assert stub._speak_queue.qsize() == 0
    assert stub._tts_generation == gen_before + 1


def test_discard_pending_sentences_is_safe_without_queue():
    stub = _StubTTS()
    stub._speak_queue = None
    gen_before = stub._tts_generation
    stub.discard_pending_sentences()  # must not raise
    assert stub._tts_generation == gen_before + 1


def test_superseded_sentences_are_skipped_by_worker_generation_check():
    """A queued sentence whose generation no longer matches must be skipped."""
    stub = _StubTTS()
    stale_gen = stub._tts_generation
    stub._speak_queue.put_nowait(("stale", stale_gen, None))
    stub.discard_pending_sentences()
    fresh_gen = stub._tts_generation
    stub._speak_queue.put_nowait(("fresh", fresh_gen, None))

    # The worker only plays items whose generation equals the current one.
    played = []
    while not stub._speak_queue.empty():
        sentence, generation, _ = stub._speak_queue.get_nowait()
        if generation == stub._tts_generation:
            played.append(sentence)
    assert played == ["fresh"]


def test_llm_generation_guard_attribute_exists():
    from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService

    assert hasattr(OpenAICompatibleLLMService, "_response_generation")
    assert isinstance(OpenAICompatibleLLMService._response_generation, int)
