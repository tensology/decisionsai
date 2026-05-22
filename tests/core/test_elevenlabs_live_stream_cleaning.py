import asyncio

from distr.core.agent.libs import (
    LLMFullResponseStartFrame,
    TextFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from distr.core.agent.services.tts.elevenlabs import ElevenLabsTTSService


def test_elevenlabs_sanitize_removes_ssml_like_and_drawn_out_fillers():
    cleaned = ElevenLabsTTSService._sanitize_for_elevenlabs(
        'Here is <think>hidden</think> the answer &entity ahhhhh --- done.'
    )

    assert "<" not in cleaned
    assert ">" not in cleaned
    assert "&entity" not in cleaned
    assert "ahhhhh" not in cleaned.lower()
    assert "hidden" not in cleaned
    assert "the answer" in cleaned


def test_elevenlabs_live_textframes_are_cleaned_before_sentence_synthesis():
    service = object.__new__(ElevenLabsTTSService)
    service._cancelled = False
    service._is_hands_free = False
    service._ptt_active = False
    service._text_buffer = ""
    service._processed_sentences = set()
    service._tts_session_active = False
    service._llm_response_started_at = 0
    service._total_audio_duration = 0.0
    service._tts_started_emitted = False
    service.event_queue = None
    service._frame_id_counter = 0

    synthesized = []
    pushed = []

    async def push_frame(frame, direction):
        pushed.append(frame)

    async def run_tts(text):
        synthesized.append(text)
        yield TTSStartedFrame()
        yield TTSStoppedFrame()

    service.push_frame = push_frame
    service.run_tts = run_tts

    start = LLMFullResponseStartFrame()
    text = TextFrame("The answer is <tool_call>{bad}</tool_call> ahhhhh stable.")

    asyncio.run(service.process_frame(start, None))
    asyncio.run(service.process_frame(text, None))

    assert synthesized == ["The answer is stable."]
    assert any(isinstance(frame, TTSStartedFrame) for frame in pushed)
    assert any(isinstance(frame, TTSStoppedFrame) for frame in pushed)
