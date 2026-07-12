from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.core.agent.libs import TranscriptionFrame
from distr.core.agent.services.stt.base import BaseSTTService
from distr.core.agent.services.stt.whisper import WhisperSTTService


class _DummySTT(BaseSTTService):
    async def run_stt(self, audio_bytes):
        if False:
            yield None


class _CaptionModel:
    def transcribe(self, _audio_np):
        return [SimpleNamespace(text="(gentle music)")]


def _whisper_service_with_caption_model():
    service = object.__new__(WhisperSTTService)
    BaseSTTService.__init__(service, event_queue=MagicMock(), is_hands_free=False)
    service.model = _CaptionModel()
    service.model_path = "base.en"
    return service


async def _collect_run_stt(service, audio):
    frames = []
    async for frame in service.run_stt(audio):
        frames.append(frame)
    return frames


def test_shared_stt_filter_rejects_captioned_music_artifacts():
    stt = _DummySTT(event_queue=MagicMock())

    assert stt._is_meaningful_text("(gentle music)") is False
    assert stt._is_meaningful_text("[background music].") is False
    assert stt._is_meaningful_text("gentle music") is False
    assert stt._is_meaningful_text("Hello, my name is Paul.") is True


def test_whisper_ptt_drops_captioned_music_artifact():
    service = _whisper_service_with_caption_model()
    audio = b"\x01\x00" * 16000

    frames = asyncio.run(_collect_run_stt(service, audio))

    assert not [frame for frame in frames if isinstance(frame, TranscriptionFrame)]
