"""Intentional barge-ins must bypass TTS startup-race protection."""

import time

from distr.core.agent.services.tts.tts_pipeline_mixin import TTSPipelineMixin


def test_marked_bargein_is_not_treated_as_stale_tts_interrupt():
    tts = TTSPipelineMixin()
    tts._llm_response_started_at = time.monotonic()
    tts._accept_bargein_interrupt = True

    tts._accept_bargein_interrupt = False
    assert tts.is_stale_interrupt_frame() is True
    tts._accept_bargein_interrupt = True
    assert tts.is_stale_interrupt_frame() is False
