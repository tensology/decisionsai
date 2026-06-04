import asyncio

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
    params = LocalAudioTransportParams(
        audio_in_enabled=True,
        audio_in_sample_rate=16000,
        audio_in_channels=1,
        input_device_index=1,
    )
    transport = HotSwappableLocalAudioInputTransport(FakePyAudio(), params)
    transport._sample_rate = 16000
    return transport


def test_resume_input_recreates_audio_task_after_idle_pause(monkeypatch):
    transport = _transport()
    created = []

    def fake_create_audio_task():
        created.append(True)
        transport._audio_task = object()

    monkeypatch.setattr(transport, "_create_audio_task", fake_create_audio_task)

    transport.pause_idle_input()
    assert transport._params.audio_in_enabled is False
    assert transport._in_stream is None

    transport.resume_input()

    assert transport._params.audio_in_enabled is True
    assert created == [True]
    assert transport._in_stream.is_active()
    assert transport.get_input_health()["audio_task_alive"] is True


def test_audio_callback_enqueues_input_frame_and_records_health(monkeypatch):
    transport = _transport()
    received = []
    loop = asyncio.new_event_loop()

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

    monkeypatch.setattr(transport, "get_event_loop", lambda: loop)
    monkeypatch.setattr(transport, "push_audio_frame", fake_push_audio_frame)
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
