from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace


class StartFrame:
    pass


class LLMFullResponseStartFrame:
    pass


class LLMFullResponseEndFrame:
    pass


class TextFrame:
    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeTTS:
    def __init__(self) -> None:
        self._force_desktop_tts = False
        self.frame_names: list[str] = []
        self.force_values: list[bool] = []

    async def process_frame(self, frame, direction) -> None:
        self.frame_names.append(type(frame).__name__)
        self.force_values.append(bool(self._force_desktop_tts))


class _RecordingLLM:
    _pipeline_direction = object()
    _FrameProcessor__started = True

    def __init__(self) -> None:
        self.frame_names: list[str] = []

    async def push_frame(self, frame, direction) -> None:
        self.frame_names.append(type(frame).__name__)


def test_speak_text_directly_routes_frames_to_tts_service(monkeypatch):
    from distr.core.agent import command_handler
    from distr.core.agent import libs

    monkeypatch.setattr(libs, "StartFrame", StartFrame)
    monkeypatch.setattr(libs, "LLMFullResponseStartFrame", LLMFullResponseStartFrame)
    monkeypatch.setattr(libs, "TextFrame", TextFrame)
    monkeypatch.setattr(libs, "LLMFullResponseEndFrame", LLMFullResponseEndFrame)

    loop = asyncio.new_event_loop()
    tts = _FakeTTS()
    llm = _RecordingLLM()
    session = SimpleNamespace(
        logger=logging.getLogger("test.direct_tts"),
        llm_service=llm,
        tts_service=tts,
        runner=SimpleNamespace(_loop=loop),
    )

    def run_now(coro, _loop):
        loop.run_until_complete(coro)
        return SimpleNamespace(result=lambda timeout=None: None)

    monkeypatch.setattr(command_handler.asyncio, "run_coroutine_threadsafe", run_now)

    try:
        command_handler._cmd_speak_text_directly(session, {"text": "Yo! Where the Ho's at?"})
    finally:
        loop.close()

    assert tts.frame_names == [
        "LLMFullResponseStartFrame",
        "TextFrame",
        "LLMFullResponseEndFrame",
    ]
    assert all(tts.force_values)
    assert tts._force_desktop_tts is False
    assert llm.frame_names == []
