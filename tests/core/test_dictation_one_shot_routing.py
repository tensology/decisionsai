"""Regression: one-shot dictation must not end before the real transcript is typed."""

import asyncio
from unittest.mock import MagicMock

from distr.core.agent.services.stt.base import BaseSTTService
from distr.core.agent.services.llm.core_mixin import LLMSharedMixin


class _DummySTT(BaseSTTService):
    async def run_stt(self, audio_bytes):
        if False:
            yield None


class _FakeTranscriptionFrame:
    def __init__(self, text: str):
        self.text = text


class _DummyLLM(LLMSharedMixin):
    def __init__(self):
        self._is_dictating = True
        self._dictation_one_shot = True
        self._one_shot_dictation_armed = True
        self._is_hands_free = False
        self._is_listening = True
        self._messages = []
        self._tools = []
        self._tools_dict = {}
        self._cancelled = False
        self._tts_service = None
        self.event_queue = None
        self.chat_manager = None

    async def push_frame(self, frame, direction):
        raise AssertionError("empty dictation transcript should not be pushed downstream")


def test_empty_dictation_capture_queues_stop_dictation():
    queue = MagicMock()
    stt = _DummySTT(event_queue=queue)
    stt._is_dictating = True

    stt._schedule_dictation_empty_transcription_unblock()

    queue.put.assert_called_once_with(("stop_dictation", {}), block=False)


def test_dictation_hotkey_arms_typing_before_capture():
    from pathlib import Path

    source = Path("distr/core/agent/command_handler.py").read_text()
    block = source.split("def _cmd_dictation_hotkey_pressed", 1)[1].split(
        "def _cmd_ticket_dictation_hotkey_pressed", 1
    )[0]
    assert "_one_shot_dictation_armed = True" in block
    assert '_cmd_set_dictating(session, {"enabled": True})' in block
    assert "for_dictation" in block
    assert block.index("_one_shot_dictation_armed") < block.index("_cmd_push_to_talk_start")


def test_one_shot_dictation_does_not_stop_on_empty_frame(monkeypatch):
    import distr.core.agent.libs as libs

    monkeypatch.setattr(libs, "TranscriptionFrame", _FakeTranscriptionFrame)

    llm = _DummyLLM()
    asyncio.run(llm.process_frame(_FakeTranscriptionFrame(""), None))

    assert llm._is_dictating is True
    assert llm._dictation_one_shot is True
    assert llm._one_shot_dictation_armed is True
