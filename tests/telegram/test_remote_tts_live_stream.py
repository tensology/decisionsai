"""Live PCM streaming of TTS to the remote app (first audio before synthesis ends)."""

from __future__ import annotations

import base64
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from distr.core.agent.services.tts import remote_stream
from distr.core.agent.services.tts.remote_stream import (
    PcmStream,
    _openai_units,
    _sentence_stream,
    split_for_streaming,
)
from distr.core.integrations.telegram.remote_audio_stream import iter_remote_pcm_messages
from distr.core.integrations.telegram.remote_tts_delivery import (
    build_synthetic_remote_context,
    deliver_remote_tts,
)

RATE = 24000


def _pcm(seconds: float) -> bytes:
    return (np.zeros(int(RATE * seconds), dtype="<i2")).tobytes()


class Manager:
    def __init__(self):
        self.sent: list[dict] = []
        self.times: list[float] = []
        self._pending_remote_agent_response = None
        self._pending_remote_agent_responses = []
        self._cancelled_remote_audio_requests: set[str] = set()

    def is_connected(self):
        return True

    def _send_websocket_message(self, message):
        self.sent.append(message)
        self.times.append(time.monotonic())
        return True

    def _press_key(self, *a, **k):
        return True


def _types(manager):
    return [m["type"] for m in manager.sent]


def _deliver(manager, open_pcm_stream, *, generate_tts=lambda t: None, ctx=None, **kw):
    return deliver_remote_tts(
        manager,
        "Hello there.",
        ctx or build_synthetic_remote_context({"mode": "command", "request_id": "r1"}),
        generate_tts=generate_tts,
        convert_wav_to_ogg=lambda p: p,
        cleanup_files=MagicMock(),
        open_pcm_stream=open_pcm_stream,
        **kw,
    )


# --- splitting ------------------------------------------------------------


def test_split_keeps_domain_names_whole():
    units = split_for_streaming(
        "The deploy for merrypak.co.za finished. Then tensology.com restarted cleanly. Nothing else to report."
    )
    assert any("merrypak.co.za" in u for u in units)
    assert any("tensology.com" in u for u in units)
    assert not any(u.startswith((".co", ".za", ".com")) for u in units)


def test_split_merges_short_sentences_and_caps_long_ones():
    units = split_for_streaming("Yes. Done. " + "word " * 120 + "end.")
    assert units[0].startswith("Yes. Done.")
    assert all(len(u) <= 310 for u in units)
    assert " ".join(units).replace("  ", " ").count("word") == 120


def test_split_never_loses_text():
    text = "First sentence here is long enough. Second one follows it closely. And a third without a stop"
    assert " ".join(split_for_streaming(text)) == text


def test_openai_units_first_sentence_alone_then_batches():
    text = " ".join(f"This is sentence number {i} of the reply." for i in range(30))
    units = _openai_units(text)
    assert units[0].startswith("This is sentence number 0")
    assert len(units[0]) < 80
    assert all(len(u) <= 650 for u in units[1:])
    assert " ".join(units) == text


# --- framing --------------------------------------------------------------


def test_pcm_messages_frame_start_chunks_end():
    msgs = list(iter_remote_pcm_messages(request_id="r", sample_rate=RATE, chunks=iter([_pcm(0.5), _pcm(0.3)])))
    assert msgs[0]["type"] == "remote_agent_audio_start"
    assert msgs[0]["data"]["format"] == "pcm_s16le" and msgs[0]["data"]["sample_rate"] == RATE
    chunks = [m for m in msgs if m["type"] == "remote_agent_audio_chunk"]
    assert [m["data"]["seq"] for m in chunks] == list(range(len(chunks)))
    assert chunks[0]["data"]["size_bytes"] < chunks[1]["data"]["size_bytes"]  # small first frame
    total = sum(len(base64.b64decode(m["data"]["data"])) for m in chunks)
    assert total == len(_pcm(0.5)) + len(_pcm(0.3))
    assert all(m["data"]["size_bytes"] % 2 == 0 for m in chunks)
    assert msgs[-1]["type"] == "remote_agent_audio_end" and msgs[-1]["data"]["size_bytes"] == total


def test_pcm_messages_fail_before_start_when_no_audio():
    with pytest.raises(ValueError):
        list(iter_remote_pcm_messages(request_id="r", sample_rate=RATE, chunks=iter([])))


# --- delivery -------------------------------------------------------------


def test_first_audio_is_sent_before_synthesis_finishes():
    manager = Manager()
    gate = threading.Event()
    t0 = time.monotonic()

    def slow_chunks():
        yield _pcm(0.5)
        assert gate.wait(5), "first audio must be on the wire while synthesis still runs"
        yield _pcm(0.5)

    stream = PcmStream("fake", RATE, slow_chunks())

    def release_after_first_chunk():
        while not any(m["type"] == "remote_agent_audio_chunk" for m in list(manager.sent)):
            time.sleep(0.005)
        gate.set()

    threading.Thread(target=release_after_first_chunk, daemon=True).start()
    assert _deliver(manager, lambda text: stream) is True
    types = _types(manager)
    assert types[0] == "remote_agent_response"
    assert types[1] == "remote_agent_audio_start"
    assert types[-1] == "remote_agent_audio_end"
    assert manager.sent[0]["data"]["audio_streamed"] is True
    assert time.monotonic() - t0 < 4


def test_stream_failure_before_audio_falls_back_to_file(tmp_path):
    manager = Manager()
    ogg = tmp_path / "v.ogg"
    ogg.write_bytes(b"OggS" + b"\0" * 32)

    def boom(_text):
        raise ValueError("no key")

    assert _deliver(manager, boom, generate_tts=lambda t: ogg) is True
    types = _types(manager)
    assert "remote_agent_audio_start" in types
    start = next(m for m in manager.sent if m["type"] == "remote_agent_audio_start")
    assert start["data"]["mime_type"] == "audio/ogg; codecs=opus"  # legacy full-file path


def test_disabled_stream_returning_none_uses_file(tmp_path):
    manager = Manager()
    ogg = tmp_path / "v.ogg"
    ogg.write_bytes(b"OggS" + b"\0" * 32)
    assert _deliver(manager, lambda t: None, generate_tts=lambda t: ogg) is True
    start = next(m for m in manager.sent if m["type"] == "remote_agent_audio_start")
    assert "ogg" in start["data"]["mime_type"]


def test_stream_failure_midway_ends_cleanly_without_replay():
    manager = Manager()

    def chunks():
        yield _pcm(0.5)
        raise RuntimeError("provider dropped")

    ok = _deliver(manager, lambda t: PcmStream("fake", RATE, chunks()))
    assert ok is True  # True so the retry wrapper does not play the reply twice
    assert _types(manager).count("remote_agent_audio_start") == 1
    assert manager.sent[-1]["type"] == "remote_agent_audio_end"
    assert manager.sent[-1]["data"]["truncated"] is True


def test_stop_during_stream_sends_stopped_and_closes_provider():
    manager = Manager()
    closed = threading.Event()

    def chunks():
        try:
            for _ in range(100):
                yield _pcm(0.2)
        finally:
            closed.set()

    stream = PcmStream("fake", RATE, chunks())
    orig = manager._send_websocket_message
    seen = {"chunks": 0}

    def send(message):
        if message["type"] == "remote_agent_audio_chunk":
            seen["chunks"] += 1
            if seen["chunks"] == 3:
                manager._cancelled_remote_audio_requests.add("r1")
        return orig(message)

    manager._send_websocket_message = send
    assert _deliver(manager, lambda t: stream) is True
    assert manager.sent[-1]["type"] == "remote_agent_audio_stopped"
    assert manager.sent[-1]["data"]["reason"] == "user_stop"
    assert "remote_agent_audio_end" not in _types(manager)
    assert seen["chunks"] < 10
    assert closed.wait(2)


def test_stop_before_start_streams_nothing():
    manager = Manager()
    manager._cancelled_remote_audio_requests.add("r1")
    opened = MagicMock()
    _deliver(manager, opened)
    opened.assert_not_called()
    assert "remote_agent_audio_start" not in _types(manager)


def test_text_after_audio_when_text_not_sent_first():
    manager = Manager()
    ok = _deliver(manager, lambda t: PcmStream("fake", RATE, iter([_pcm(0.3)])), send_text_first=False)
    assert ok is True
    assert _types(manager)[:2] == ["remote_agent_response", "remote_agent_audio_start"]


# --- sentence-level generic provider ---------------------------------------


class FakeDescriptor:
    default_voice = "v"

    def __init__(self, delay=0.0):
        self.calls: list[tuple[str, str]] = []
        self.delay = delay

    def generate_audio(self, text, voice, speed, out_file):
        import soundfile as sf

        self.calls.append((text, voice))
        time.sleep(self.delay)
        sf.write(out_file, np.zeros(int(RATE * 0.2), dtype="float32"), RATE)


def test_sentence_stream_yields_each_sentence_as_it_is_synthesised():
    desc = FakeDescriptor(delay=0.15)
    text = "The first sentence is a decent length. The second sentence is also fine. The third one closes it out."
    t0 = time.monotonic()
    stream = _sentence_stream("fake", desc, text, "myvoice", 1.0)
    first_ready = time.monotonic() - t0
    got = list(stream.chunks)
    total = time.monotonic() - t0
    assert first_ready < total * 0.6  # first sentence ready well before all three are done
    assert len(desc.calls) == 3 and all(v == "myvoice" for _t, v in desc.calls)
    assert stream.mode == "sentence" and stream.sample_rate == RATE
    assert sum(len(c) for c in got) == 3 * int(RATE * 0.2) * 2


def test_open_pcm_stream_uses_sentence_mode_for_unknown_provider(monkeypatch):
    from distr.core.agent.services.tts import registry

    desc = FakeDescriptor()
    monkeypatch.setattr(registry.tts_registry, "get", lambda pid: desc)
    stream = remote_stream.open_pcm_stream("supertonic", "A reasonably long sentence to speak aloud.", "F1")
    assert stream.mode == "sentence"
    assert b"".join(stream.chunks)


def test_voice_is_passed_through_to_native_openers(monkeypatch):
    seen = {}

    def fake_fish(text, voice, speed):
        seen["voice"] = voice
        return PcmStream("fishaudio", RATE, iter([_pcm(0.1)]))

    monkeypatch.setitem(remote_stream._NATIVE_OPENERS, "fishaudio", fake_fish)
    remote_stream.open_pcm_stream("fishaudio", "Speak this.", "voice-123")
    assert seen["voice"] == "voice-123"


def test_native_opener_none_falls_to_sentence(monkeypatch):
    from distr.core.agent.services.tts import registry

    monkeypatch.setitem(remote_stream._NATIVE_OPENERS, "kokoro", lambda *a: None)
    monkeypatch.setattr(registry.tts_registry, "get", lambda pid: FakeDescriptor())
    assert remote_stream.open_pcm_stream("kokoro", "Custom voice sentence goes here.", "custom_1").mode == "sentence"


def test_streaming_switch(monkeypatch):
    monkeypatch.delenv("DECISIONS_REMOTE_TTS_STREAM", raising=False)
    assert remote_stream.remote_streaming_enabled({}) is True
    assert remote_stream.remote_streaming_enabled({"remote_tts_streaming": False}) is False
    monkeypatch.setenv("DECISIONS_REMOTE_TTS_STREAM", "0")
    assert remote_stream.remote_streaming_enabled({}) is False


def test_align_pcm_keeps_odd_byte():
    carry = bytearray()
    assert remote_stream._align_pcm(carry, b"\x01\x02\x03") == b"\x01\x02"
    assert remote_stream._align_pcm(carry, b"\x04") == b"\x03\x04"


# --- native providers (provider APIs faked) ---------------------------------


def test_fish_stream_yields_frames_as_the_socket_produces_them(monkeypatch):
    from distr.core.agent.services.tts import fishaudio_client

    gate = threading.Event()
    calls = {}

    async def fake_iter(api_key, text, **kw):
        calls.update(kw, text=text)
        yield b"\x01\x00" * 100
        yield b"\x01"                      # odd split must be re-aligned
        yield b"\x00" * 199
        gate.set()

    monkeypatch.setattr(fishaudio_client, "iter_pcm_audio", fake_iter)
    monkeypatch.setattr(
        remote_stream, "_settings",
        lambda: {"fishaudio_key": "k", "fishaudio_voice": "default-voice", "fishaudio_tts_model": "s2.1-pro-free"},
    )
    stream = remote_stream.open_pcm_stream("fishaudio", "Hello from Fish.", "voice-xyz", 1.2)
    assert stream.mode == "native" and stream.sample_rate == 24000
    first = next(stream.chunks)
    assert len(first) % 2 == 0
    rest = b"".join(stream.chunks)
    assert (len(first) + len(rest)) == 200 + 200
    assert calls["reference_id"] == "voice-xyz" and calls["speed"] == 1.2 and calls["sample_rate"] == 24000
    assert calls["text"] == "Hello from Fish."


def test_fish_stream_close_stops_the_socket(monkeypatch):
    from distr.core.agent.services.tts import fishaudio_client

    closed = threading.Event()

    async def endless(api_key, text, **kw):
        try:
            while True:
                yield b"\x00\x00" * 480
        finally:
            closed.set()

    monkeypatch.setattr(fishaudio_client, "iter_pcm_audio", endless)
    monkeypatch.setattr(remote_stream, "_settings", lambda: {"fishaudio_key": "k"})
    stream = remote_stream.open_pcm_stream("fishaudio", "Barge in test sentence.", "v")
    next(stream.chunks)
    stream.close()
    assert closed.wait(3)


def test_fish_stream_without_key_fails_before_audio(monkeypatch):
    monkeypatch.setattr(remote_stream, "_settings", lambda: {})
    with pytest.raises(ValueError):
        remote_stream.open_pcm_stream("fishaudio", "No key configured here.", "v")


class _FakeOpenAI:
    created: list[dict] = []
    reject_voice: str | None = None

    def __init__(self, api_key=None):
        outer = self

        class _Resp:
            def __init__(self, n):
                self.n = n

            def iter_bytes(self, chunk_size=0):
                for _ in range(self.n):
                    yield b"\x00\x00" * 240

        class _CM:
            def __init__(self, kwargs):
                self.kwargs = kwargs

            def __enter__(self):
                if outer.reject_voice and self.kwargs["voice"] == outer.reject_voice:
                    raise RuntimeError("Error code: 400 - Bad Request: invalid voice for this model")
                return _Resp(3)

            def __exit__(self, *a):
                return False

        class _Streaming:
            def create(_self, **kwargs):
                _FakeOpenAI.created.append(kwargs)
                return _CM(kwargs)

        class _Speech:
            with_streaming_response = _Streaming()

        class _Audio:
            speech = _Speech()

        self.audio = _Audio()


def test_openai_stream_requests_pcm_first_sentence_alone_and_in_order(monkeypatch):
    import openai

    _FakeOpenAI.created = []
    _FakeOpenAI.reject_voice = None
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(remote_stream, "_settings", lambda: {"openai_key": "k", "openai_tts_model": "tts-1"})
    text = " ".join(f"Sentence number {i} of the spoken reply." for i in range(25))
    stream = remote_stream.open_pcm_stream("openai", text, "nova", 1.0)
    assert stream.sample_rate == 24000 and stream.mode == "native"
    pcm = b"".join(stream.chunks)
    assert len(_FakeOpenAI.created) >= 2
    assert all(c["response_format"] == "pcm" and c["voice"] == "nova" for c in _FakeOpenAI.created)
    assert _FakeOpenAI.created[0]["input"].startswith("Sentence number 0")
    assert len(_FakeOpenAI.created[0]["input"]) < 80
    assert len(pcm) == len(_FakeOpenAI.created) * 3 * 480


def test_openai_stream_retries_incompatible_voice_with_fallback(monkeypatch):
    import openai

    _FakeOpenAI.created = []
    _FakeOpenAI.reject_voice = "marin"
    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(remote_stream, "_settings", lambda: {"openai_key": "k", "openai_tts_model": "tts-1"})
    stream = remote_stream.open_pcm_stream("openai", "A short reply that is spoken aloud.", "marin")
    assert b"".join(stream.chunks)
    assert _FakeOpenAI.created[-1]["voice"] == "coral"
