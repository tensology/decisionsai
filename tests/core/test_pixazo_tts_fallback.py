import asyncio
from types import SimpleNamespace

import numpy as np

from distr.core.agent.libs import ErrorFrame, TTSStoppedFrame
from distr.core.agent.services.tts.openai import OpenAITTSService
from distr.core.agent.services.tts import pixazo as pixazo_tts
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
        err = ErrorFrame(error="Pixazo request failed (500): Internal Server Error")
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


def test_pixazo_custom_voice_refreshes_reference_once_after_clone_500(monkeypatch):
    from distr.core import pixazo_client

    service = object.__new__(PixazoTTSService)
    service._pixazo_api_key = "pixazo-test-key"
    service.voice_id = "custom_14"
    service._reference_audio_url = "https://relay.example/old.wav"
    service._prompt_text = "reference prompt"
    service._dit_steps = 6
    service._refresh_reference_audio_url = lambda: "https://relay.example/new.wav"

    seen_urls = []

    def fake_synthesize(_api_key, _text, *, voice_id, reference_audio_url, prompt_text, dit_steps):
        seen_urls.append(reference_audio_url)
        assert voice_id == "custom_14"
        assert prompt_text == "reference prompt"
        assert dit_steps == 6
        if len(seen_urls) == 1:
            raise RuntimeError("Pixazo request failed (500): Internal Server Error")
        return b"RIFF fake wav"

    monkeypatch.setattr(pixazo_client, "voxcpm_synthesize_wav_bytes", fake_synthesize)
    monkeypatch.setattr(pixazo_tts, "SOUNDFILE_AVAILABLE", True)
    monkeypatch.setattr(
        pixazo_tts,
        "sf",
        SimpleNamespace(
            read=lambda _buf, dtype="float32": (np.array([0.1, -0.1], dtype=np.float32), 24000)
        ),
    )

    audio, sample_rate = service._generate_audio("Done.")

    assert seen_urls == ["https://relay.example/old.wav", "https://relay.example/new.wav"]
    assert service._reference_audio_url == "https://relay.example/new.wav"
    assert sample_rate == 24000
    assert audio.dtype == np.float32
