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


def test_disabling_last_continuous_mode_clears_stale_audio():
    stt = _DummySTT(is_hands_free=True)
    stt._audio_buffer = [b"speech"]
    stt._pre_buffer.append(b"lead-in")
    stt._user_speaking = True
    old_epoch = stt._continuous_capture_epoch

    stt.set_hands_free(False)

    assert stt._audio_buffer == []
    assert list(stt._pre_buffer) == []
    assert stt._user_speaking is False
    assert stt._continuous_capture_epoch == old_epoch + 1


def test_dictation_hotkey_starts_hold_mode_before_capture():
    from pathlib import Path

    source = Path("distr/core/agent/command_handler.py").read_text()
    block = source.split("def _cmd_dictation_hotkey_pressed", 1)[1].split(
        "def _cmd_ticket_dictation_hotkey_pressed", 1
    )[0]
    assert '_cmd_set_dictating(session, {"enabled": True})' in block
    assert "_start_dictation(one_shot=False)" in block
    assert "_one_shot_dictation_armed = True" not in block
    assert "_start_dictation(one_shot=True)" not in block
    assert "for_dictation" in block
    assert block.index("_cmd_set_dictating") < block.index("_cmd_push_to_talk_start")
    assert block.index("_start_dictation") < block.index("_cmd_push_to_talk_start")


def test_dictation_hotkey_release_stops_without_one_shot_watchdog():
    from pathlib import Path

    source = Path("distr/core/agent/command_handler.py").read_text()
    block = source.split("def _cmd_dictation_hotkey_released", 1)[1].split(
        "def _cmd_interrupt_tts", 1
    )[0]
    assert "_cmd_push_to_talk_stop(session, params)" in block
    assert "llm._finish_dictation_after_pending_transcript()" in block
    assert "asyncio.sleep(20.0)" not in block
    assert "_dictation_one_shot" not in block


def test_ticket_dictation_hotkey_keeps_ticket_mode_without_one_shot():
    from pathlib import Path

    source = Path("distr/core/agent/command_handler.py").read_text()
    block = source.split("def _cmd_ticket_dictation_hotkey_pressed", 1)[1].split(
        "def _cmd_dictation_hotkey_released", 1
    )[0]
    assert '_cmd_set_dictating(session, {"enabled": True})' in block
    assert '_start_dictation(one_shot=False, output_mode="ticket")' in block
    assert "_one_shot_dictation_armed = True" not in block
    assert "_start_dictation(one_shot=True" not in block


def test_one_shot_dictation_does_not_stop_on_empty_frame(monkeypatch):
    import distr.core.agent.libs as libs

    monkeypatch.setattr(libs, "TranscriptionFrame", _FakeTranscriptionFrame)

    llm = _DummyLLM()
    asyncio.run(llm.process_frame(_FakeTranscriptionFrame(""), None))

    assert llm._is_dictating is True
    assert llm._dictation_one_shot is True
    assert llm._one_shot_dictation_armed is True
