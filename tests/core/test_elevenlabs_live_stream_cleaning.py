import asyncio

from distr.core.agent.libs import (
    LLMFullResponseEndFrame,
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
    service._init_tts_pipeline_state()

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
    text = TextFrame()
    text.text = "The answer is <tool_call>{bad}</tool_call> ahhhhh stable."

    async def run_stream():
        await service.process_frame(start, None)
        await service.process_frame(text, None)
        await service.process_frame(LLMFullResponseEndFrame(), None)

    asyncio.run(run_stream())

    assert synthesized == ["The answer is stable."]
    assert any(isinstance(frame, TTSStartedFrame) for frame in pushed)
    assert any(isinstance(frame, TTSStoppedFrame) for frame in pushed)


def test_elevenlabs_provider_speed_stays_unity_when_transport_handles_playback_speed(monkeypatch):
    service = object.__new__(ElevenLabsTTSService)
    service.playback_speed = 1.2
    service.voice_id = "voice-id"
    service._stability = 0.5
    service._similarity_boost = 0.6
    service._style = 0.25
    service._use_speaker_boost = True

    captured_request = {}

    class FakeTextToSpeech:
        def convert(self, **kwargs):
            captured_request.update(kwargs)
            return [b"fake mp3"]

    class FakeClient:
        text_to_speech = FakeTextToSpeech()

    class FakeAudioSegment:
        channels = 1
        frame_rate = 44100

        def get_array_of_samples(self):
            return [0, 1000, -1000, 0]

    service.client = FakeClient()
    monkeypatch.setattr(
        "distr.core.agent.services.tts.elevenlabs.AudioSegment",
        type("FakeAudioSegmentModule", (), {"from_mp3": staticmethod(lambda _: FakeAudioSegment())}),
    )
    monkeypatch.setattr("distr.core.agent.services.tts.elevenlabs.PYDUB_AVAILABLE", True)

    audio, sample_rate = service._generate_audio("Testing speed.")

    assert sample_rate == 44100
    assert len(audio) == 4
    assert captured_request["model_id"] == "eleven_flash_v2_5"
    assert captured_request["voice_settings"]["speed"] == 1.0
