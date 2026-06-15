"""Regression: one-shot dictation must not end before the real transcript is typed."""

from unittest.mock import MagicMock

from distr.core.agent.services.stt.base import BaseSTTService


class _DummySTT(BaseSTTService):
    async def run_stt(self, audio_bytes):
        if False:
            yield None


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


def test_one_shot_dictation_does_not_stop_on_empty_frame_in_source():
    from pathlib import Path

    source = Path("distr/core/agent/services/llm/core_mixin.py").read_text()
    block = source.split("if not text:", 1)[1].split("logger.info(\"LLM: Received transcription:", 1)[0]
    assert "_stop_dictation()" not in block
