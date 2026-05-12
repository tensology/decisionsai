import asyncio

from distr.core.agent.libs import LLMFullResponseStartFrame, TextFrame
from distr.core.agent.services.tts.openai import OpenAITTSService
from distr.core.agent.services.tts.coqui import CoquiTTSService
from distr.core.agent.services.tts.vibevoice_realtime import VibeVoiceRealtimeTTSService


def _prepare_streaming_service(service):
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
    pushed = []

    async def push_frame(frame, direction):
        pushed.append(frame)

    service.push_frame = push_frame
    return pushed


def test_openai_live_textframes_are_cleaned_before_sentence_synthesis():
    service = object.__new__(OpenAITTSService)
    _prepare_streaming_service(service)
    synthesized = []

    async def run_tts(text):
        synthesized.append(text)
        if False:
            yield None

    service.run_tts = run_tts

    start = LLMFullResponseStartFrame()
    text = TextFrame()
    text.text = "OpenAI should ignore <tool_call>{bad}</tool_call> ahhhhh artifacts."

    asyncio.run(service.process_frame(start, None))
    asyncio.run(service.process_frame(text, None))

    assert synthesized == ["OpenAI should ignore artifacts."]


def test_coqui_and_vibevoice_clean_before_synthesis():
    for cls in (CoquiTTSService, VibeVoiceRealtimeTTSService):
        service = object.__new__(cls)
        service._cancelled = False
        service._tts_session_active = False
        service._tts_started_emitted = False
        service._current_telegram_request = False
        service._total_audio_duration = 0.0
        service._speech_volume = 1.0
        service._frame_id_counter = 0
        service.event_queue = None
        synthesized = []

        def fake_generate_audio(text):
            synthesized.append(text)
            return None, 0

        if cls is CoquiTTSService:
            service._generate_audio = fake_generate_audio
        else:
            service._synthesize_to_48k_mono = fake_generate_audio

        async def consume():
            async for _ in service.run_tts("<tool_call>{bad}</tool_call> ahhhhh clean sentence."):
                pass

        asyncio.run(consume())
        assert synthesized == ["clean sentence."]
