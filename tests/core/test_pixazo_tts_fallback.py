import asyncio

from distr.core.agent.libs import ErrorFrame, TTSStoppedFrame
from distr.core.agent.services.tts.openai import OpenAITTSService
from distr.core.agent.services.tts.pixazo import (
    PixazoTTSService,
    build_pixazo_fallback_text,
    describe_pixazo_tts_failure,
)


def test_describe_pixazo_tts_failure_includes_http_500_reason():
    message = describe_pixazo_tts_failure(
        RuntimeError("Pixazo request failed (500): Internal Server Error")
    )

    assert message == (
        "Pixazo TTS failed with HTTP 500: Internal Server Error. "
        "Pixazo did not provide a more specific reason."
    )


def test_build_pixazo_fallback_text_says_it_will_retry_and_continues_response():
    spoken = build_pixazo_fallback_text(
        RuntimeError("Pixazo request failed (500): Internal Server Error"),
        "Hey Paul, nice to meet you.",
    )

    assert spoken == (
        "Pixazo TTS failed with HTTP 500: Internal Server Error. "
        "Pixazo did not provide a more specific reason. "
        "I'll try Pixazo again on the next response. "
        "Continuing with the fallback voice now. "
        "Hey Paul, nice to meet you."
    )


def test_pixazo_run_tts_falls_back_for_failed_primary_generation(monkeypatch):
    async def primary_run_tts(_self, _text):
        err = ErrorFrame()
        err.error = "Pixazo request failed (500): Internal Server Error"
        yield err
        yield TTSStoppedFrame()

    class SpokenFrame:
        def __init__(self, text):
            self.text = text

    class FallbackTTS:
        def __init__(self):
            self.spoken_text = None

        async def run_tts(self, text):
            self.spoken_text = text
            yield SpokenFrame(text)

    monkeypatch.setattr(OpenAITTSService, "run_tts", primary_run_tts)

    fallback = FallbackTTS()
    service = object.__new__(PixazoTTSService)
    service._fallback_tts_service = fallback
    service._is_hands_free = False
    service._ptt_active = False
    service._tts_session_active = True

    async def collect_frames():
        return [frame async for frame in service.run_tts("Original response.")]

    frames = asyncio.run(collect_frames())

    assert len(frames) == 1
    assert frames[0].text == (
        "Pixazo TTS failed with HTTP 500: Internal Server Error. "
        "Pixazo did not provide a more specific reason. "
        "I'll try Pixazo again on the next response. "
        "Continuing with the fallback voice now. "
        "Original response."
    )
    assert fallback._tts_session_active is True
    assert fallback._tts_started_emitted is False
