import asyncio

from distr.core.agent.services.llm.core_mixin import LLMSharedMixin


class _FakeTranscriptionFrame:
    def __init__(self, text: str):
        self.text = text


class _EventQueue:
    def __init__(self):
        self.items = []

    def put(self, item, block=False):
        self.items.append(item)


class _ChatManager:
    def get_current_chat(self):
        return 123


class _Harness(LLMSharedMixin):
    def __init__(self):
        self._is_dictating = True
        self._dictation_one_shot = False
        self._one_shot_dictation_armed = False
        self._dictation_release_pending = False
        self._is_hands_free = False
        self._is_listening = True
        self._messages = []
        self._tools = []
        self._tools_dict = {}
        self._cancelled = False
        self._tts_service = None
        self.event_queue = _EventQueue()
        self.chat_manager = _ChatManager()
        self.typed = []

    async def _type_dictation_text(self, text: str):
        self.typed.append(text)

    async def push_frame(self, frame, direction):
        raise AssertionError("dictation transcript should not be pushed downstream")


def test_dictation_transcript_does_not_touch_agent_ptt_state(monkeypatch):
    import distr.core.agent.libs as libs

    monkeypatch.setattr(libs, "TranscriptionFrame", _FakeTranscriptionFrame)

    harness = _Harness()
    asyncio.run(harness.process_frame(_FakeTranscriptionFrame("ordinary dictated text"), None))

    assert harness.typed == ["ordinary dictated text"]
    assert not hasattr(harness, "_last_ptt_transcription_text")
    assert ("transcription_progress", {
        "chat_id": 123,
        "status_text": "",
        "done": False,
        "clear_live_preview": False,
        "discard_live_preview": True,
    }) in harness.event_queue.items


def test_released_hold_dictation_transcript_still_routes_to_typing(monkeypatch):
    import distr.core.agent.libs as libs

    monkeypatch.setattr(libs, "TranscriptionFrame", _FakeTranscriptionFrame)

    harness = _Harness()
    harness._is_dictating = False
    harness._dictation_release_pending = True

    asyncio.run(harness.process_frame(_FakeTranscriptionFrame("late flushed dictation"), None))

    assert harness.typed == ["late flushed dictation"]
    assert harness._dictation_release_pending is False
    assert not hasattr(harness, "_last_ptt_transcription_text")


def test_transcript_without_active_voice_capture_is_rejected(monkeypatch):
    import distr.core.agent.libs as libs

    monkeypatch.setattr(libs, "TranscriptionFrame", _FakeTranscriptionFrame)

    harness = _Harness()
    harness._is_dictating = False
    harness._voice_capture_pending = False
    harness._ptt_active = False

    asyncio.run(harness.process_frame(_FakeTranscriptionFrame("ambient speech"), None))

    assert harness.typed == []
    assert not hasattr(harness, "_last_ptt_transcription_text")
    assert harness.event_queue.items == []


def test_completed_ptt_reserves_exactly_one_transcript(monkeypatch):
    import distr.core.agent.libs as libs

    monkeypatch.setattr(libs, "TranscriptionFrame", _FakeTranscriptionFrame)

    harness = _Harness()
    harness._is_dictating = False
    harness._voice_capture_pending = True
    harness._ptt_active = False
    harness._messages = [{"role": "user", "content": "captured speech"}]

    asyncio.run(harness.process_frame(_FakeTranscriptionFrame("captured speech"), None))

    assert harness._voice_capture_pending is False
    assert harness._last_ptt_transcription_text == "captured speech"
