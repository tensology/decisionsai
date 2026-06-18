import asyncio

from distr.core.agent.libs import (
    AudioRawFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from distr.core.agent.services.llm.text_utils import clean_text_for_tts
from distr.core.agent.services.tts.coqui import CoquiTTSService
from distr.core.agent.services.tts.sentence_split import (
    extract_complete_sentences,
    is_redundant_sentence,
)


def _stream_sentences(chunks):
    buffer = ""
    emitted = []
    for chunk in chunks:
        buffer += clean_text_for_tts(chunk, strip_whitespace=False)
        sentences, buffer = extract_complete_sentences(buffer)
        emitted.extend(sentences)
    sentences, buffer = extract_complete_sentences(buffer)
    emitted.extend(sentences)
    if buffer.strip():
        emitted.append(buffer.strip())
    return emitted


def test_streaming_sentence_splitter_keeps_all_sentences_across_bad_boundaries():
    chunks = [
        "First, I checked v1.",
        "2.3 and it was stable. Dr.",
        " Smith confirmed the setting. Next sentence arrives",
        " without drama! Final sentence is buffered until the end",
        ".",
    ]

    assert _stream_sentences(chunks) == [
        "First, I checked v1.2.3 and it was stable.",
        "Dr. Smith confirmed the setting.",
        "Next sentence arrives without drama!",
        "Final sentence is buffered until the end.",
    ]


def test_streaming_sentence_splitter_handles_no_space_after_punctuation():
    chunks = [
        "Alpha is complete.",
        "Beta starts immediately.Gamma has a number 3.",
        "14 inside it. Delta ends now.",
    ]

    assert _stream_sentences(chunks) == [
        "Alpha is complete.",
        "Beta starts immediately.",
        "Gamma has a number 3.14 inside it.",
        "Delta ends now.",
    ]


def test_streaming_sentence_splitter_stress_no_drops_or_duplicates():
    expected = [
        "Sentence one is short.",
        "Sentence two mentions e.g. abbreviations and keeps going.",
        "Sentence three has version 0.0.1 and another clause.",
        "Sentence four finishes cleanly.",
        "Sentence five is left for the final flush.",
    ]
    text = " ".join(expected)
    chunks = [text[i : i + size] for i, size in zip(range(0, len(text), 7), [7] * 200)]

    assert _stream_sentences(chunks) == expected


def test_tts_duplicate_filter_does_not_drop_similar_legitimate_sentences():
    spoken = {
        "the first action completed successfully.",
        "the second action completed successfully.",
    }

    assert is_redundant_sentence("the first action completed successfully.", spoken)
    assert is_redundant_sentence("first action completed successfully.", spoken)
    assert not is_redundant_sentence("the third action completed successfully.", spoken)
    assert not is_redundant_sentence("the second action completed with warnings.", spoken)


def test_coqui_forwards_tts_lifecycle_frames_for_transport_state():
    service = object.__new__(CoquiTTSService)
    service._init_tts_pipeline_state()
    service._cancelled = False
    service._in_response_after_start = False
    service._llm_response_started_at = 0
    service._is_hands_free = False
    service._text_buffer = ""
    service._processed_sentences = set()
    service._tts_session_active = False
    service._total_audio_duration = 0.0
    service._tts_started_emitted = False
    service._session_text = ""
    service._current_telegram_request = False
    service._telegram_file_sent = False
    service.event_queue = None
    pushed = []

    async def push_frame(frame, direction):
        pushed.append(frame)

    async def run_tts(text):
        audio = AudioRawFrame()
        audio.audio = b"\x00" * 320
        audio.sample_rate = 16000
        audio.num_channels = 1
        yield TTSStartedFrame()
        yield audio
        yield TTSStoppedFrame()

    service.push_frame = push_frame
    service.run_tts = run_tts

    start = LLMFullResponseStartFrame()
    text = TextFrame()
    text.text = "Coqui should preserve playback lifecycle."

    asyncio.run(service.process_frame(start, None))
    asyncio.run(service.process_frame(text, None))

    assert any(isinstance(frame, TTSStartedFrame) for frame in pushed)
    assert any(isinstance(frame, AudioRawFrame) for frame in pushed)
    assert any(isinstance(frame, TTSStoppedFrame) for frame in pushed)


def test_pixazo_batches_three_sentences_per_synthesis_call():
    from distr.core.agent.services.tts.pixazo import PixazoTTSService

    service = object.__new__(PixazoTTSService)
    service._cancelled = False
    service._text_buffer = ""
    service._processed_sentences = set()
    service._tts_sentence_batch_size = 3
    service._sentence_batch_hold = []
    enqueued: list[str] = []

    async def _enqueue_sentence(sentence, direction):
        enqueued.append(sentence)

    service._enqueue_sentence = _enqueue_sentence

    async def _run():
        await service._enqueue_new_sentences(
            ["One.", "Two.", "Three.", "Four.", "Five."],
            None,
        )
        await service._flush_sentence_batch_hold(None)

    asyncio.run(_run())

    assert enqueued == ["One. Two. Three.", "Four. Five."]


def test_play_queued_sentence_forwards_tts_lifecycle_frames():
    from distr.core.agent.services.tts.tts_pipeline_mixin import TTSPipelineMixin
    from distr.core.agent.libs import TTSStartedFrame, TTSStoppedFrame

    class FakeAudioFrame:
        def __init__(self, audio: bytes, sample_rate: int, num_channels: int):
            self.audio = audio
            self.sample_rate = sample_rate
            self.num_channels = num_channels

    class FakeTTS(TTSPipelineMixin):
        def __init__(self):
            self._init_tts_pipeline_state()
            self._cancelled = False
            self._speech_volume = 1.0
            self._volume_in_run_tts = True
            self.pushed = []

        async def push_frame(self, frame, direction):
            self.pushed.append(frame)

        async def run_tts(self, text):
            yield TTSStartedFrame()
            yield FakeAudioFrame(audio=b"\x01\x00" * 160, sample_rate=16000, num_channels=1)
            yield FakeAudioFrame(audio=b"\x02\x00" * 160, sample_rate=16000, num_channels=1)
            yield TTSStoppedFrame()

    tts = FakeTTS()
    # Mixin checks isinstance against AudioRawFrame; treat our fake as audio.
    import distr.core.agent.services.tts.tts_pipeline_mixin as mixin_mod

    real_audio = mixin_mod.AudioRawFrame
    mixin_mod.AudioRawFrame = FakeAudioFrame
    try:
        asyncio.run(tts._play_queued_sentence("hello", tts._tts_generation, None))
    finally:
        mixin_mod.AudioRawFrame = real_audio

    types = [type(frame).__name__ for frame in tts.pushed]
    assert types[0] == "TTSStartedFrame"
    assert types.count("FakeAudioFrame") == 2
    assert types[-1] == "TTSStoppedFrame"


def test_llm_end_flushes_partial_batch_when_text_buffer_empty():
    from distr.core.agent.services.tts.openai import OpenAITTSService

    service = object.__new__(OpenAITTSService)
    service._cancelled = False
    service._text_buffer = ""
    service._processed_sentences = set()
    service._tts_sentence_batch_size = 3
    service._sentence_batch_hold = ["Yep.", "I can hear you."]
    service._tts_session_active = True
    service._total_audio_duration = 0.0
    service._tts_started_emitted = False
    service.event_queue = None
    enqueued: list[str] = []

    async def _enqueue_sentence(sentence, direction):
        enqueued.append(sentence)

    async def _drain_speak_queue():
        return None

    service._enqueue_sentence = _enqueue_sentence
    service._drain_speak_queue = _drain_speak_queue
    service.push_frame = lambda frame, direction: asyncio.sleep(0)

    asyncio.run(service.process_frame(LLMFullResponseEndFrame(), None))

    assert enqueued == ["Yep. I can hear you."]
