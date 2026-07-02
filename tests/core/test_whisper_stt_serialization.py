import asyncio
import threading
import time
from types import SimpleNamespace

from distr.core.agent.services.stt.whisper import WhisperSTTService


class _ConcurrentProbeModel:
    def __init__(self):
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def transcribe(self, _audio_np):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.15)
            return [SimpleNamespace(text="")]
        finally:
            with self._lock:
                self.active -= 1


def _whisper_service_with_model(model):
    service = object.__new__(WhisperSTTService)
    service.model = model
    service.model_path = "base.en"
    service._stt_cancelled = False
    service._min_audio_duration_ms = 1000
    service._is_hands_free = False
    service._audio_artifacts = set()
    service._filler_words = set()
    return service


async def _collect_run_stt(service, audio):
    frames = []
    async for frame in service.run_stt(audio):
        frames.append(frame)
    return frames


def test_whisper_transcribe_calls_are_serialized_per_model_instance():
    model = _ConcurrentProbeModel()
    service = _whisper_service_with_model(model)
    audio = b"\x01\x00" * 16000

    async def run_two_transcriptions():
        await asyncio.gather(
            _collect_run_stt(service, audio),
            _collect_run_stt(service, audio),
        )

    asyncio.run(run_two_transcriptions())

    assert model.max_active == 1


class _ImmediateThread:
    def __init__(self, target, *args, **kwargs):
        self._target = target

    def start(self):
        self._target()


def test_whisper_warm_up_async_runs_once(monkeypatch):
    service = _whisper_service_with_model(_ConcurrentProbeModel())
    service._warmup_started = False
    service._warmup_complete = False
    calls = []

    def fake_warm_up_model():
        calls.append("warmed")
        service._warmup_complete = True

    service._warm_up_model = fake_warm_up_model
    monkeypatch.setattr(threading, "Thread", _ImmediateThread)

    service.warm_up_async()
    service.warm_up_async()

    assert calls == ["warmed"]
    assert service._warmup_started is True
    assert service._warmup_complete is True
