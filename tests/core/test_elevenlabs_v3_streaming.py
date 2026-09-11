import asyncio
from types import SimpleNamespace

from distr.core.agent.libs import (
    AudioRawFrame,
    ErrorFrame,
    OutputAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from distr.core.agent.services.tts.elevenlabs import ElevenLabsTTSService


def _service(client, model_id="eleven_v3_conversational"):
    service = object.__new__(ElevenLabsTTSService)
    service.client = client
    service.voice_id = "existing-clone-id"
    service.voice_name = "Existing Clone"
    service.model_id = model_id
    service.playback_speed = 1.0
    service._cancelled = False
    service._is_hands_free = False
    service._ptt_active = False
    service._speech_volume = 1.0
    service._stability = 0.6
    service._similarity_boost = 0.2
    service._style = 0.7
    service._use_speaker_boost = True
    service._tts_session_active = True
    service._tts_started_emitted = False
    service._total_audio_duration = 0.0
    service._frame_id_counter = 1
    service._on_quota_exceeded = None
    service.event_queue = None
    service._init_tts_pipeline_state()
    return service


def _collect(service, text="This is an expressive streaming test."):
    async def run():
        return [frame async for frame in service.run_tts(text)]

    return asyncio.run(run())


def _audio_frames(frames):
    frame_types = (AudioRawFrame,) + ((OutputAudioRawFrame,) if OutputAudioRawFrame else ())
    return [frame for frame in frames if isinstance(frame, frame_types)]


def test_v3_conversational_streams_pcm_through_dialogue_endpoint_with_same_clone_id():
    requests = []

    class Dialogue:
        def stream(self, **kwargs):
            requests.append(kwargs)
            return iter([b"\x01\x00" * 480, b"\x02\x00" * 480])

    class Speech:
        def stream(self, **_kwargs):
            raise AssertionError("v3 must use the dialogue streaming endpoint")

    service = _service(SimpleNamespace(text_to_dialogue=Dialogue(), text_to_speech=Speech()))
    frames = _collect(service)

    assert isinstance(frames[0], TTSStartedFrame)
    assert isinstance(frames[-1], TTSStoppedFrame)
    audio = _audio_frames(frames)
    assert len(audio) == 2
    assert all(frame.sample_rate == 24000 for frame in audio)
    assert len(requests) == 1
    request = requests[0]
    dialogue_input = request["inputs"][0]
    assert getattr(dialogue_input, "text", None) == "This is an expressive streaming test."
    assert getattr(dialogue_input, "voice_id", None) == "existing-clone-id"
    assert request["model_id"] == "eleven_v3_conversational"
    assert request["output_format"] == "pcm_24000"
    assert getattr(request["settings"], "stability", None) == 0.5
    assert set(request["settings"].model_fields_set) == {"stability"}


def test_v3_failure_before_audio_falls_back_to_flash_stream_with_same_clone_id():
    speech_requests = []

    class CompatibilityFailure(RuntimeError):
        status_code = 422

    class Dialogue:
        def stream(self, **_kwargs):
            raise CompatibilityFailure("voice is not available for this v3 model")

    class Speech:
        def stream(self, **kwargs):
            speech_requests.append(kwargs)
            return iter([b"\x03\x00" * 480])

    service = _service(SimpleNamespace(text_to_dialogue=Dialogue(), text_to_speech=Speech()))
    frames = _collect(service)

    assert len(_audio_frames(frames)) == 1
    assert speech_requests[0]["voice_id"] == "existing-clone-id"
    assert speech_requests[0]["model_id"] == "eleven_flash_v2_5"
    assert speech_requests[0]["output_format"] == "pcm_24000"
    assert speech_requests[0]["voice_settings"]["use_speaker_boost"] is True


def test_v3_stream_yields_first_pcm_chunk_without_buffering_complete_response():
    state = {"next_calls": 0}

    class StreamingChunks:
        def __iter__(self):
            return self

        def __next__(self):
            state["next_calls"] += 1
            if state["next_calls"] == 1:
                return b"\x04\x00" * 480
            raise StopIteration

    client = SimpleNamespace(
        text_to_dialogue=SimpleNamespace(stream=lambda **_kwargs: StreamingChunks()),
        text_to_speech=SimpleNamespace(stream=lambda **_kwargs: iter(())),
    )
    service = _service(client)

    async def run_until_audio():
        generator = service.run_tts("Start playing now.")
        first = await anext(generator)
        second = await anext(generator)
        await generator.aclose()
        return first, second

    first, second = asyncio.run(run_until_audio())

    assert isinstance(first, TTSStartedFrame)
    assert isinstance(second, tuple(t for t in (AudioRawFrame, OutputAudioRawFrame) if t))
    assert state["next_calls"] == 1


def test_v3_stream_reassembles_odd_provider_chunks_without_losing_samples():
    expected = b"\x01\x02" * 481
    chunks = [expected[:1], expected[1:511], expected[511:]]
    client = SimpleNamespace(
        text_to_dialogue=SimpleNamespace(stream=lambda **_kwargs: iter(chunks)),
        text_to_speech=SimpleNamespace(stream=lambda **_kwargs: iter(())),
    )
    service = _service(client)

    audio = _audio_frames(_collect(service))

    assert b"".join(frame.audio for frame in audio) == expected


def test_v3_stream_does_not_replay_with_flash_after_audio_was_emitted():
    speech_requests = []

    class PartialFailure:
        def __init__(self):
            self._count = 0

        def __iter__(self):
            return self

        def __next__(self):
            self._count += 1
            if self._count == 1:
                return b"\x05\x00" * 480
            raise RuntimeError("provider disconnected")

    client = SimpleNamespace(
        text_to_dialogue=SimpleNamespace(stream=lambda **_kwargs: PartialFailure()),
        text_to_speech=SimpleNamespace(
            stream=lambda **kwargs: speech_requests.append(kwargs) or iter(())
        ),
    )
    service = _service(client)

    frames = _collect(service)

    assert len(_audio_frames(frames)) == 1
    assert speech_requests == []


def test_v3_stream_does_not_fallback_after_any_provider_audio_arrives():
    speech_requests = []

    class TinyPartialFailure:
        def __init__(self):
            self._count = 0

        def __iter__(self):
            return self

        def __next__(self):
            self._count += 1
            if self._count == 1:
                return b"\x01\x00" * 10
            raise RuntimeError("provider disconnected")

    client = SimpleNamespace(
        text_to_dialogue=SimpleNamespace(stream=lambda **_kwargs: TinyPartialFailure()),
        text_to_speech=SimpleNamespace(
            stream=lambda **kwargs: speech_requests.append(kwargs) or iter(())
        ),
    )
    service = _service(client)

    frames = _collect(service)

    assert speech_requests == []
    assert any(isinstance(frame, ErrorFrame) for frame in frames)


def test_v3_auth_failure_is_sanitized_and_does_not_fallback():
    speech_requests = []

    class AuthFailure(RuntimeError):
        status_code = 401

    client = SimpleNamespace(
        text_to_dialogue=SimpleNamespace(
            stream=lambda **_kwargs: (_ for _ in ()).throw(
                AuthFailure("secret provider response headers")
            )
        ),
        text_to_speech=SimpleNamespace(
            stream=lambda **kwargs: speech_requests.append(kwargs) or iter(())
        ),
    )
    service = _service(client)

    frames = _collect(service)

    errors = [frame for frame in frames if isinstance(frame, ErrorFrame)]
    assert len(errors) == 1
    assert "secret provider response headers" not in errors[0].error
    assert "Check the ElevenLabs API key" in errors[0].error
    assert speech_requests == []


def test_v3_quota_failure_uses_existing_kokoro_fallback_without_flash_retry():
    fallback_calls = []
    speech_requests = []

    class QuotaFailure(RuntimeError):
        status_code = 429
        body = {
            "detail": {
                "status": "quota_exceeded",
                "message": "Quota exceeded for this account.",
            }
        }

    class Fallback:
        _cancelled = False
        _is_hands_free = False

        async def run_tts(self, text):
            fallback_calls.append(text)
            yield TTSStartedFrame()
            yield TTSStoppedFrame()

    client = SimpleNamespace(
        text_to_dialogue=SimpleNamespace(
            stream=lambda **_kwargs: (_ for _ in ()).throw(QuotaFailure())
        ),
        text_to_speech=SimpleNamespace(
            stream=lambda **kwargs: speech_requests.append(kwargs) or iter(())
        ),
    )
    service = _service(client)
    service._on_quota_exceeded = lambda: Fallback()

    frames = _collect(service)

    assert fallback_calls == ["This is an expressive streaming test."]
    assert speech_requests == []
    assert not any(isinstance(frame, ErrorFrame) for frame in frames)
