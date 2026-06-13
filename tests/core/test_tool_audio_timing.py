"""Tool-side-effect audio timing helpers."""

import asyncio

from distr.core.agent.tool_audio_timing import (
    estimate_speech_duration,
    wait_before_tool_side_effects,
)


def test_estimate_speech_duration_scales_with_words_and_speed():
    slow = estimate_speech_duration("one two three four", playback_speed=1.0)
    fast = estimate_speech_duration("one two three four", playback_speed=2.0)
    assert slow > 0
    assert fast < slow


def test_wait_before_tool_side_effects_uses_transport_when_available():
    class FakeTransport:
        def __init__(self):
            self._speed = 1.0
            self.called = False

        async def wait_for_playback_idle(self, timeout=10.0):
            self.called = True

    service = type("Svc", (), {})()
    service._is_telegram_request = False
    service._speaker_enabled = True
    service._pipeline_direction = None
    service._audio_transport_output = FakeTransport()
    pushed = []

    async def push_frame(frame, direction=None):
        pushed.append(type(frame).__name__)

    service.push_frame = push_frame

    asyncio.run(wait_before_tool_side_effects(service, "I'll take a screenshot now."))

    assert service._audio_transport_output.called is True
    assert "LLMFullResponseEndFrame" in pushed


def test_wait_before_tool_side_effects_skips_when_speaker_disabled():
    service = type("Svc", (), {})()
    service._is_telegram_request = False
    service._speaker_enabled = False

    asyncio.run(wait_before_tool_side_effects(service, "hello"))
