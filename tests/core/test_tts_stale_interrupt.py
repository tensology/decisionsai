"""Intentional barge-ins must bypass TTS startup-race protection."""

import time

from distr.core.agent.services.tts.tts_pipeline_mixin import (
    BARGEIN_ACCEPT_WINDOW_SEC,
    TTSPipelineMixin,
)


def test_marked_bargein_is_not_treated_as_stale_tts_interrupt():
    tts = TTSPipelineMixin()
    tts._llm_response_started_at = time.monotonic()
    tts._accept_bargein_interrupt = True

    tts._accept_bargein_interrupt = False
    assert tts.is_stale_interrupt_frame() is True
    tts._accept_bargein_interrupt = True
    assert tts.is_stale_interrupt_frame() is False


def test_bargein_window_outlives_the_momentary_flag():
    """The broadcast InterruptionFrame arrives after the flag is cleared.

    Regression for the live log: `Barge-in confirmed` followed by
    `TTS: Ignoring stale InterruptionFrame`, because the boolean marker was
    already reset before Pipecat broadcast the frame to the TTS.
    """
    tts = TTSPipelineMixin()
    tts._llm_response_started_at = time.monotonic()  # fresh response -> normally stale
    assert tts.is_stale_interrupt_frame() is True

    tts.mark_bargein()
    # Flag already cleared (the STT's finally block ran) — the window must hold.
    tts._accept_bargein_interrupt = False
    assert tts.is_stale_interrupt_frame() is False

    # Outside the window, the stale-race guard applies again.
    tts._bargein_at = time.monotonic() - (BARGEIN_ACCEPT_WINDOW_SEC + 1.0)
    assert tts.is_stale_interrupt_frame() is True
