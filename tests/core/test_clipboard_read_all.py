"""Clipboard commands must reach the real speech queue without losing text."""

import asyncio
from types import SimpleNamespace

import pytest

from distr.core.agent.services.llm.mixins.fast_actions import FastActionMixin
from distr.core.agent.services.tts.fishaudio import FishAudioTTSService
from distr.core.agent.tools.clipboard.clipboard_actions import ClipboardActionTool


class RecordingTTS(FishAudioTTSService):
    """Use Fish's real framing, splitter and queue with local playback only."""

    def __init__(self):
        self._init_tts_pipeline_state()
        self._cancelled = False
        self._ptt_active = False
        self._is_hands_free = False
        self._text_buffer = ""
        self._processed_sentences = set()
        self._tts_session_active = False
        self._total_audio_duration = 0.0
        self._tts_started_emitted = False
        self.event_queue = None
        self.played = []

    async def push_frame(self, frame, direction):
        pass

    def _can_prefetch_synthesis(self):
        return False

    async def _play_queued_sentence(self, sentence, generation, direction):
        await asyncio.sleep(0.001)  # Synthesis trails the clipboard producer.
        self.played.append(sentence)


class ClipboardReader(FastActionMixin):
    def __init__(self, tts):
        self._tts_service = tts
        self._messages = []
        self._cancelled = False
        self._is_telegram_request = False
        self._pipeline_direction = None
        self.remote = []

    async def push_frame(self, frame, direction=None):
        # The old fast-action route: consuming frames still used the lossy queue.
        await self._tts_service.process_frame(frame, direction)

    def _fa_record_read_aloud_activity(self, *args):
        pass

    def _fa_remember_clipboard_context(self, *args):
        pass

    def _emit_telegram_response(self, text, unused):
        self.remote.append(text)


@pytest.mark.parametrize("route,count", [("get", 24), ("read", 24), ("direct", 24),
                                       ("tool", 24), ("get", 540)])
def test_long_clipboard_reads_every_sentence_in_order(route, count):
    sentences = [f"The item numbered {index} needs to be read aloud." for index in range(count)]
    text = "\n\n".join(" ".join(sentences[i:i + 6]) for i in range(0, count, 6))

    async def scenario():
        tts = RecordingTTS()
        reader = ClipboardReader(tts)
        action = SimpleNamespace(original_text="read from clipboard")
        try:
            if route == "get":
                await reader._fa_handle_tts_clipboard(action, None, "CLIPBOARD CONTENT:\n\n" + text, None)
            elif route == "read":
                await reader._fa_handle_tts(action, None, "READ_ACTION:" + text, None)
            elif route == "direct":
                reader._get_clipboard_text = lambda: text
                await reader._handle_read_this_directly("downstream", use_clipboard_directly=True)
            else:
                tool = ClipboardActionTool(llm_service=reader)
                result = await tool._handle_read_async(text)
                assert result.startswith("Reading ")
            assert " ".join(tts.played) == " ".join(sentences)
            assert tts._read_all_sentences is False
        finally:
            if tts._speak_worker_task:
                tts._speak_worker_task.cancel()
                await asyncio.gather(tts._speak_worker_task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
def test_read_all_mode_is_restored_when_delivery_fails(error):
    async def scenario():
        tts = RecordingTTS()
        reader = ClipboardReader(tts)

        async def fail(frame, direction):
            assert tts._read_all_sentences
            raise error()

        tts.process_frame = fail
        with pytest.raises(error):
            await reader._fa_push_read_aloud("Read the whole passage.")
        assert tts._read_all_sentences is False

    asyncio.run(scenario())


def test_remote_clipboard_read_does_not_start_desktop_speech():
    async def scenario():
        tts = RecordingTTS()
        reader = ClipboardReader(tts)
        reader._is_telegram_request = True
        await reader._fa_deliver_spoken_response("The full clipboard text.", read_all=True)
        assert reader.remote == ["The full clipboard text."]
        assert tts._speak_queue is None
        assert tts._read_all_sentences is False

    asyncio.run(scenario())


def test_async_clipboard_tool_sends_remote_audio_only_once():
    async def scenario():
        tts = RecordingTTS()
        reader = ClipboardReader(tts)
        reader._is_telegram_request = True
        tool = ClipboardActionTool(llm_service=reader)
        tool._last_read_text = "The full clipboard text."
        tool._read_task = asyncio.create_task(tool._handle_read_async(tool._last_read_text))
        await reader._fa_handle_tts(SimpleNamespace(original_text="read this"), None, None, tool)
        assert reader.remote == ["The full clipboard text."]
        assert tts._speak_queue is None
        assert tool._read_task is None

    asyncio.run(scenario())


def test_clipboard_read_can_be_interrupted_without_leaving_read_all_on():
    async def scenario():
        tts = RecordingTTS()
        reader = ClipboardReader(tts)

        async def interrupt(sentence, generation, direction):
            tts.played.append(sentence)
            tts._cancelled = True

        tts._play_queued_sentence = interrupt
        try:
            text = " ".join(f"Please read item {i} in this passage." for i in range(24))
            await reader._fa_deliver_spoken_response(text, read_all=True)
            assert len(tts.played) == 1
            assert tts._read_all_sentences is False
        finally:
            if tts._speak_worker_task:
                tts._speak_worker_task.cancel()
                await asyncio.gather(tts._speak_worker_task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
