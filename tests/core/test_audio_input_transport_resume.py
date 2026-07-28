import asyncio
import time
from types import SimpleNamespace

import numpy as np

from distr.core.agent.libs import LocalAudioTransportParams
from distr.core.agent.transport import HotSwappableLocalAudioInputTransport


class FakeStream:
    def __init__(self):
        self.started = False
        self.closed = False

    def is_active(self):
        return self.started and not self.closed

    def start_stream(self):
        self.started = True

    def stop_stream(self):
        self.started = False

    def close(self):
        self.closed = True


class FakePyAudio:
    def __init__(self):
        self.open_calls = []

    def get_format_from_width(self, width):
        return width

    def open(self, **kwargs):
        self.open_calls.append(kwargs)
        return FakeStream()


def _transport():
    attrs = {
        "audio_in_enabled": True,
        "audio_in_sample_rate": 16000,
        "audio_in_channels": 1,
        "input_device_index": 1,
    }
    try:
        params = LocalAudioTransportParams(**attrs)
        transport = HotSwappableLocalAudioInputTransport(FakePyAudio(), params)
    except TypeError:
        params = SimpleNamespace(**attrs)
        transport = object.__new__(HotSwappableLocalAudioInputTransport)
        transport._py_audio = FakePyAudio()
        transport._params = params
        transport._in_stream = None
        transport._audio_task = None
        transport._paused = False
        transport._input_callback_count = 0
        transport._input_callback_bytes = 0
        transport._input_callback_errors = 0
        transport._input_idle_callback_count = 0
        transport._input_last_callback_at = 0.0
        transport._input_last_callback_peak = 0
    transport._sample_rate = 16000
    return transport


def test_resume_input_recreates_audio_task_after_idle_pause(monkeypatch):
    transport = _transport()
    created = []

    def fake_create_audio_task():
        created.append(True)
        transport._audio_task = object()

    monkeypatch.setattr(transport, "_create_audio_task", fake_create_audio_task, raising=False)

    transport.pause_idle_input()
    assert transport._params.audio_in_enabled is False
    assert transport._in_stream is None

    transport.resume_input()

    assert transport._params.audio_in_enabled is True
    assert created == [True]
    assert transport._in_stream.is_active()
    assert transport.get_input_health()["audio_task_alive"] is True


def test_pause_idle_input_keeps_existing_stream_warm():
    transport = _transport()
    transport._open_input_stream()
    stream = transport._in_stream

    transport.pause_idle_input()

    assert transport._params.audio_in_enabled is False
    assert transport._in_stream is stream
    assert transport._in_stream.is_active()
    assert transport.get_input_health()["stream_active"] is True


def test_resume_input_reuses_warm_paused_stream_even_when_callback_age_is_stale(monkeypatch):
    transport = _transport()
    created = []

    def fake_create_audio_task():
        created.append(True)
        transport._audio_task = object()
        transport._audio_in_queue = object()

    monkeypatch.setattr(transport, "_create_audio_task", fake_create_audio_task, raising=False)

    transport._open_input_stream()
    stale_stream = transport._in_stream
    transport.pause_idle_input()
    transport._input_last_callback_at = time.time() - 10.0

    transport.resume_input()

    assert created == [True]
    assert stale_stream.closed is False
    assert transport._in_stream is stale_stream
    assert transport._in_stream.is_active()
    assert len(transport._py_audio.open_calls) == 1


def test_audio_callback_enqueues_input_frame_and_records_health(monkeypatch):
    transport = _transport()
    received = []
    loop = asyncio.new_event_loop()

    class FakeInputAudioRawFrame:
        def __init__(self, audio, sample_rate, num_channels):
            self.audio = audio
            self.sample_rate = sample_rate
            self.num_channels = num_channels

    monkeypatch.setattr("distr.core.agent.transport.InputAudioRawFrame", FakeInputAudioRawFrame)

    async def fake_push_audio_frame(frame):
        received.append(frame)

    class DoneFuture:
        def add_done_callback(self, callback):
            callback(self)

        def result(self):
            return None

    def fake_run_coroutine_threadsafe(coro, target_loop):
        assert target_loop is loop
        loop.run_until_complete(coro)
        return DoneFuture()

    monkeypatch.setattr(transport, "get_event_loop", lambda: loop, raising=False)
    monkeypatch.setattr(transport, "push_audio_frame", fake_push_audio_frame, raising=False)
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)

    try:
        audio = np.array([0, 512, -1024, 256], dtype=np.int16).tobytes()
        transport._audio_in_callback(audio, frame_count=4, time_info={}, status=None)
    finally:
        loop.close()

    assert len(received) == 1
    assert received[0].audio == audio
    health = transport.get_input_health()
    assert health["callbacks"] == 1
    assert health["bytes"] == len(audio)
    assert health["last_peak"] == 1024
    assert health["callback_errors"] == 0


def test_audio_callback_does_no_numpy_or_async_work_while_idle(monkeypatch):
    transport = _transport()
    transport.pause_idle_input()

    monkeypatch.setattr(
        "distr.core.agent.transport.InputAudioRawFrame",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("idle callback must not build a frame")
        ),
    )
    monkeypatch.setattr(
        asyncio,
        "run_coroutine_threadsafe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("idle callback must not schedule a coroutine")
        ),
    )

    audio = np.array([0, 512, -1024, 256], dtype=np.int16).tobytes()
    result = transport._audio_in_callback(audio, frame_count=4, time_info={}, status=None)

    assert result == (None, transport._pa_continue())
    health = transport.get_input_health()
    assert health["callbacks"] == 1
    assert health["idle_callbacks"] == 1
    assert health["last_peak"] == 0


def test_resume_input_clears_paused_flag():
    transport = _transport()
    transport._paused = True
    transport._open_input_stream = lambda: None
    transport._ensure_audio_task_ready = lambda: None

    transport.resume_input()

    assert transport._params.audio_in_enabled is True
    assert transport._paused is False


def test_audio_callback_ignores_closed_event_loop_during_shutdown(monkeypatch):
    transport = _transport()
    loop = asyncio.new_event_loop()
    loop.close()

    async def fake_push_audio_frame(frame):
        raise AssertionError("closed event loop should short-circuit before enqueue")

    monkeypatch.setattr(transport, "get_event_loop", lambda: loop, raising=False)
    monkeypatch.setattr(transport, "push_audio_frame", fake_push_audio_frame, raising=False)

    audio = np.array([0, 512, -1024, 256], dtype=np.int16).tobytes()
    transport._audio_in_callback(audio, frame_count=4, time_info={}, status=None)

    health = transport.get_input_health()
    assert health["callbacks"] == 1
    assert health["callback_errors"] == 0
