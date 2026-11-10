"""Speaker hardware is owned only by playback, never an idle agent."""

import asyncio
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from distr.core.agent.libs import OutputAudioRawFrame, StartFrame
from distr.core.agent.transport import AudioPlaybackState, HotSwappableLocalAudioOutputTransport
from pipecat.transports.local.audio import LocalAudioTransportParams


class Stream:
    def __init__(self, events):
        self.events = events
        self.active = True
        self.writing = False
        self.gate = None
        self.fail = False
        self.audio = bytearray()

    def is_active(self):
        return self.active

    def start_stream(self):
        self.active = True
        self.events.append("start")

    def write(self, data, exception_on_underflow=False):
        assert exception_on_underflow, "Underflows must be visible to recovery"
        self.writing = True
        self.events.append("write")
        self.audio.extend(data)
        try:
            if self.gate:
                self.gate.wait(timeout=3)
            if self.fail:
                raise self.fail
        finally:
            self.writing = False

    def stop_stream(self):
        assert not self.writing, "stop raced native output write"
        self.active = False
        self.events.append("stop")

    def close(self):
        assert not self.writing, "close raced native output write"
        self.active = False
        self.events.append("close")


class Audio:
    def __init__(self):
        self.events = []
        self.streams = []

    def get_default_output_device_info(self):
        return self.get_device_info_by_index(0)

    def get_device_info_by_index(self, index):
        return {"index": index, "name": "Speakers", "maxOutputChannels": 2}

    def get_device_count(self):
        return 1

    def get_format_from_width(self, width):
        return width

    def open(self, **kwargs):
        self.events.append("open")
        stream = Stream(self.events)
        self.streams.append(stream)
        return stream


@pytest.fixture
def output(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.config_loader.resolve_system_default_output_device", lambda: (0, "Speakers")
    )
    transport = HotSwappableLocalAudioOutputTransport(
        Audio(), LocalAudioTransportParams(audio_out_enabled=True, audio_out_sample_rate=44100),
        event_queue=Queue(),
    )
    transport.set_transport_ready = AsyncMock()
    transport._OUTPUT_IDLE_SECS = 0.015
    yield transport
    transport._executor.shutdown(wait=True)


def audio():
    return OutputAudioRawFrame(audio=b"\x10\x00" * 882, sample_rate=44100, num_channels=2)


async def start(output):
    await output.start(StartFrame(audio_out_sample_rate=44100))


async def finish(output):
    task = getattr(output, "_output_idle_task", None)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if output._out_stream:
        await output._close_output_stream_async()


def test_start_and_idle_device_change_never_open_the_speaker(output):
    async def scenario():
        await start(output)
        output.set_device(0, "Speakers")
        await asyncio.sleep(0.03)
        assert output._py_audio.events == []
        output.set_transport_ready.assert_awaited_once()
        assert output._active_output_route()["stream_active"] is False

    asyncio.run(scenario())


def test_playback_opens_drains_closes_and_reopens_for_next_audio(output):
    async def scenario():
        await start(output)
        try:
            assert await output.write_audio_frame(audio())
            assert output._output_stream_is_active()
            await asyncio.sleep(0.06)
            assert output._out_stream is None
            assert output._py_audio.events == ["open", "start", "write", "stop", "close"]
            assert await output.write_audio_frame(audio())
            assert len(output._py_audio.streams) == 2
            assert output._active_output_route()["write_stalled"] is False
        finally:
            await finish(output)

    asyncio.run(scenario())


def test_idle_release_waits_for_queued_sink_audio(output):
    async def scenario():
        await start(output)
        output._media_senders = {None: SimpleNamespace(_audio_queue=asyncio.Queue())}
        queue = output._media_senders[None]._audio_queue
        await queue.put(audio())
        try:
            await output.write_audio_frame(audio())
            await asyncio.sleep(0.05)
            assert output._out_stream is not None
            queue.get_nowait()
            queue.task_done()
            await asyncio.sleep(0.05)
            assert output._out_stream is None
        finally:
            await finish(output)

    asyncio.run(scenario())


def test_interruption_releases_stream_without_restarting_it(output):
    async def scenario():
        await start(output)
        try:
            await output.write_audio_frame(audio())
            await output._abort_output_stream_async()
            assert output._out_stream is None
            assert output._py_audio.events[-2:] == ["stop", "close"]
            output._force_silence = True
            assert await output.write_audio_frame(audio()) is False
            assert len(output._py_audio.streams) == 1
        finally:
            await finish(output)

    asyncio.run(scenario())


def test_underflow_is_reported_and_next_playback_recovers(output):
    async def scenario():
        await start(output)
        try:
            await output.write_audio_frame(audio())
            output._out_stream.fail = OSError("device disconnected")
            assert await output.write_audio_frame(audio()) is False
            health = output._active_output_route()
            assert health["write_errors"] == 1
            assert health["recovery_count"] == 1
            assert output._out_stream is None
            assert await output.write_audio_frame(audio())
            assert output._active_output_route()["write_errors"] == 0
        finally:
            await finish(output)

    asyncio.run(scenario())


def test_active_stream_with_stuck_write_is_unhealthy_and_abort_is_serialized(output):
    async def scenario():
        await start(output)
        gate = threading.Event()
        output._OUTPUT_WRITE_STALL_SECS = 0.025
        try:
            await output.write_audio_frame(audio())
            output._out_stream.gate = gate
            writing = asyncio.create_task(output.write_audio_frame(audio()))
            for _ in range(100):
                if output._output_write_started_at is not None:
                    break
                await asyncio.sleep(0.001)
            await asyncio.sleep(0.05)
            health = output._active_output_route()
            assert health["stream_active"] is True
            assert health["write_in_flight"] is True
            assert health["write_stalled"] is True
            assert health["stall_count"] == 1
            from distr.core.agent.command_handler import _cmd_agent_health_probe
            session = SimpleNamespace(event_queue=output.event_queue,
                                      transport=SimpleNamespace(output=lambda: output))
            _cmd_agent_health_probe(session, {"probe_id": "blocked-speaker"})
            event, payload = output.event_queue.get_nowait()
            assert event == "agent_health_ack"
            assert payload["audio_output"]["write_stalled"] is True
            aborting = asyncio.create_task(output._abort_output_stream_async())
            await asyncio.sleep(0.03)
            assert "close" not in output._py_audio.events
            gate.set()
            await writing
            await aborting
            assert output._out_stream is None
            assert output._active_output_route()["write_stalled"] is False
        finally:
            gate.set()
            await finish(output)

    asyncio.run(asyncio.wait_for(scenario(), timeout=4))


def test_silence_without_playback_does_not_claim_speaker_hardware(output):
    async def scenario():
        await start(output)
        frame = OutputAudioRawFrame(audio=b"\x00" * 882, sample_rate=44100, num_channels=2)
        assert await output.write_audio_frame(frame)
        assert output._py_audio.events == []

    asyncio.run(scenario())


def test_underflow_notification_keeps_the_current_audio_and_counts_the_gap(output):
    async def scenario():
        await start(output)
        try:
            await output.write_audio_frame(audio())
            stream = output._out_stream
            stream.fail = OSError(-9980, "output underflow")
            assert await output.write_audio_frame(audio())
            assert output._out_stream is stream
            assert output._active_output_route()["underflow_count"] == 1
            assert output._active_output_route()["recovery_count"] == 0
        finally:
            await finish(output)

    asyncio.run(scenario())


def test_cancelling_write_caller_still_releases_the_stream_after_native_write(output):
    async def scenario():
        await start(output)
        gate = threading.Event()
        try:
            await output.write_audio_frame(audio())
            output._out_stream.gate = gate
            writing = asyncio.create_task(output.write_audio_frame(audio()))
            while output._output_write_started_at is None:
                await asyncio.sleep(0)
            writing.cancel()
            await asyncio.gather(writing, return_exceptions=True)
            assert "close" not in output._py_audio.events
            gate.set()
            await asyncio.sleep(0.08)
            assert output._out_stream is None
        finally:
            gate.set()
            await finish(output)

    asyncio.run(asyncio.wait_for(scenario(), timeout=4))


def test_real_pipeline_queues_release_hardware_and_preserve_all_audio(output):
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.transports.base_output import BaseOutputTransport
    from distr.core.agent.libs import LLMFullResponseStartFrame, LLMFullResponseEndFrame, TTSStartedFrame, EndFrame

    class PassThrough(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    async def scenario():
        output.set_transport_ready = BaseOutputTransport.set_transport_ready.__get__(output)
        task = PipelineTask(Pipeline([PassThrough(), output, PassThrough()]),
                            params=PipelineParams(audio_out_sample_rate=44100))
        runner = PipelineRunner(handle_sigint=False)
        running = asyncio.create_task(runner.run(task))
        try:
            while not output._media_senders:
                await asyncio.sleep(0.001)
            assert output._py_audio.events == []
            await task.queue_frame(TTSStartedFrame())
            await asyncio.sleep(0.03)
            assert output._py_audio.events == [], "Synthesis without PCM must not start the speaker"
            frames = [audio() for _ in range(20)]
            await task.queue_frames([LLMFullResponseStartFrame(), *frames, LLMFullResponseEndFrame()])
            expected_bytes = sum(len(frame.audio) for frame in frames)
            for _ in range(1000):
                if sum(len(stream.audio) for stream in output._py_audio.streams) == expected_bytes and output._out_stream is None:
                    break
                await asyncio.sleep(0.001)
            assert sum(len(stream.audio) for stream in output._py_audio.streams) == expected_bytes
            assert output._out_stream is None
            assert not running.done()
            await task.queue_frame(EndFrame())
            await running
            assert output._out_stream is None
        finally:
            if not running.done():
                await task.cancel()
                await running

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_continuous_audio_keeps_one_stream_until_the_last_chunk_drains(output):
    async def scenario():
        await start(output)
        try:
            for _ in range(8):
                assert await output.write_audio_frame(audio())
                await asyncio.sleep(0.004)
            assert len(output._py_audio.streams) == 1
            await asyncio.sleep(0.05)
            assert output._out_stream is None
        finally:
            await finish(output)

    asyncio.run(scenario())
