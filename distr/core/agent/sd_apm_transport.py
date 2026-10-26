"""sounddevice + LiveKit APM audio transport.

Replacement for the PyAudio transport's audio I/O. Capture and playback run
through ONE ``rtc.AudioProcessingModule`` inside the sounddevice callbacks, so
the AEC reverse stream is the real rendered audio with device-timestamped
timing. This is what ``tests/audio/sd_apm_check.py`` proved cancels echo on the
Mac (0.011 -> 0.002 median during playback) without the native SIGABRT that
``rtc.MediaDevices`` hits.

Interface matches the other transports (input()/output(), volume, playback
events, compatibility shims). Enable with ``DECISIONS_AUDIO_BACKEND=sdapm``.
"""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np
import sounddevice as sd
from livekit import rtc

from distr.core.agent.libs import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    FrameProcessor,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    SileroVADAnalyzer,
    StartFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from distr.core.audio.stream_resampler import LinearStreamResampler

logger = logging.getLogger(__name__)

try:
    from pipecat.audio.vad.vad_analyzer import VADState
except Exception:  # pragma: no cover
    VADState = None

_APM_FRAME_SAMPLES_16K = 160  # 10 ms @ 16 kHz (APM works in 10 ms frames)


class _OutputBuffer:
    """Thread-safe PCM byte buffer shared between the async writer and the audio callback."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._lock = __import__("threading").Lock()

    def extend(self, data: bytes) -> None:
        with self._lock:
            self._buf.extend(data)

    def read(self, n: int) -> bytes:
        with self._lock:
            if len(self._buf) >= n:
                out = bytes(self._buf[:n])
                del self._buf[:n]
                return out
            out = bytes(self._buf)
            self._buf.clear()
            return out + b"\x00" * (n - len(out))

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


class SdApmInputTransport(FrameProcessor):
    def __init__(self, transport: "SdApmAudioTransport"):
        super().__init__()
        self._transport = transport
        self._started = False
        self._task = None
        self._queue: asyncio.Queue | None = None
        self._vad = transport._vad_analyzer
        self._vad_state = None
        self._down = LinearStreamResampler()
        self._down.configure(transport._input_sample_rate, transport._pipeline_sample_rate)

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            await self._ensure_started()
        if isinstance(frame, CancelFrame):
            await self._stop()
        await self.push_frame(frame, direction)

    async def _ensure_started(self):
        if self._started:
            return
        self._started = True
        t = self._transport
        loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()

        def callback(indata, frames, time_info, status):
            t._note_input_delay(time_info)
            cleaned = t._apm_process_near(indata[:, 0].copy())
            if cleaned is not None:
                loop.call_soon_threadsafe(self._queue.put_nowait, cleaned)

        blocksize = int(t._input_sample_rate * 0.1)
        t._in_stream = sd.InputStream(
            callback=callback,
            channels=1,
            samplerate=t._input_sample_rate,
            dtype="int16",
            blocksize=blocksize,
            device=t._input_device,
        )
        t._in_stream.start()
        self._task = asyncio.create_task(self._pump())
        logger.info(
            "sounddevice APM transport: input open (sr=%s, aec=True)",
            t._input_sample_rate,
        )

    async def _pump(self):
        t = self._transport
        try:
            while True:
                pcm16 = await self._queue.get()
                # Downsample the AEC-cleaned 16 kHz-free stream to the pipeline rate.
                if t._input_sample_rate != t._pipeline_sample_rate:
                    f = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
                    f = self._down.process(f)
                    pcm16 = np.clip(f * 32767.0, -32768, 32767).astype(np.int16).tobytes()
                if not pcm16:
                    continue
                if self._vad is not None and VADState is not None:
                    try:
                        state = await self._vad.analyze_audio(pcm16)
                        if state != self._vad_state and state in (VADState.SPEAKING, VADState.QUIET):
                            await self.push_frame(
                                VADUserStartedSpeakingFrame() if state == VADState.SPEAKING
                                else VADUserStoppedSpeakingFrame()
                            )
                            self._vad_state = state
                    except Exception:
                        pass
                await self.push_frame(
                    InputAudioRawFrame(
                        audio=pcm16, sample_rate=t._pipeline_sample_rate, num_channels=1
                    )
                )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("sounddevice APM input pump failed: %s", exc, exc_info=True)

    async def _stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        t = self._transport
        if t._in_stream is not None:
            try:
                t._in_stream.stop()
                t._in_stream.close()
            except Exception:
                pass
            t._in_stream = None

    # -- compatibility shims -------------------------------------------
    def get_input_health(self) -> dict:
        return {
            "enabled": True,
            "stream_active": self._transport._in_stream is not None,
            "audio_task_alive": self._task is not None and not self._task.done(),
            "stream_callbacks_stale": False,
        }

    async def resume_input(self):
        return None

    async def pause_idle_input(self):
        return None


class SdApmOutputTransport(FrameProcessor):
    def __init__(self, transport: "SdApmAudioTransport"):
        super().__init__()
        self._transport = transport
        self._started = False
        self._volume = 1.0
        self._speed = 1.0
        self._accept_bargein_interrupt = False
        self._force_silence = False
        self._pipeline_cut = False
        self._input_transport = None
        self._response_started_at = 0.0
        self._first_audio_at = None
        self._total_out_secs = 0.0
        self._wall_at_last_audio = 0.0
        self._finish_task = None

    # -- compatibility surface -----------------------------------------
    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, float(volume)))

    def set_speed(self, speed: float):
        self._speed = float(speed)

    def set_base_vad_confidence(self, confidence: float):
        pass

    def set_device(self, device_index=None, device_name=None):
        return None

    async def wait_for_playback_idle(self, timeout: float = 10.0):
        end = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < end:
            remaining = (self._wall_at_last_audio + self._total_out_secs) - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.05))

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        t = self._transport

        if isinstance(frame, StartFrame):
            await self._ensure_started()

        if isinstance(frame, LLMFullResponseStartFrame):
            self._response_started_at = time.monotonic()
            self._first_audio_at = None
            self._total_out_secs = 0.0

        if isinstance(frame, AudioRawFrame) and isinstance(frame.audio, (bytes, bytearray)) and frame.audio:
            self._write(frame)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            t._out_buf.clear()
            if t._ref_buf is not None:
                t._ref_buf.set_active(False)
            self._total_out_secs = 0.0
            self._wall_at_last_audio = 0.0
            self._first_audio_at = None
            self._emit("playback_finished", {"playback_id": t._playback_id})
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (LLMFullResponseEndFrame, EndFrame, TTSStoppedFrame)):
            self._schedule_finish()

        await self.push_frame(frame, direction)

    async def _ensure_started(self):
        if self._started:
            return
        self._started = True
        t = self._transport
        blocksize = int(t._output_sample_rate * 0.1)
        t._out_stream = sd.OutputStream(
            callback=t._output_callback,
            channels=1,
            samplerate=t._output_sample_rate,
            dtype="int16",
            blocksize=blocksize,
            device=t._output_device,
        )
        t._out_stream.start()
        logger.info(
            "sounddevice APM transport: output open (sr=%s, aec reverse wired)",
            t._output_sample_rate,
        )

    def _write(self, frame):
        t = self._transport
        src_rate = int(getattr(frame, "sample_rate", t._output_sample_rate) or t._output_sample_rate)
        channels = int(getattr(frame, "num_channels", 1) or 1)
        data = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32) / 32768.0
        ch = max(1, channels)
        if ch > 1:
            usable = (len(data) // ch) * ch
            data = data[:usable].reshape(-1, ch).mean(axis=1)
        if src_rate != t._output_sample_rate and len(data) > 0:
            n = max(1, int(round(len(data) * t._output_sample_rate / src_rate)))
            data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype(np.float32)
        if self._volume != 1.0:
            data = data * self._volume
        pcm = np.clip(data * 32767.0, -32768, 32767).astype(np.int16).tobytes()
        if not pcm:
            return
        t._out_buf.extend(pcm)
        secs = (len(pcm) // 2) / t._output_sample_rate
        self._total_out_secs += secs
        self._wall_at_last_audio = time.monotonic()
        if self._first_audio_at is None:
            self._first_audio_at = time.monotonic()
            if t._ref_buf is not None:
                t._ref_buf.set_active(True)
            self._emit("tts_started", {"source": "sdapm", "playback_id": t._playback_id})

    def _schedule_finish(self):
        if self._finish_task is not None and not self._finish_task.done():
            return
        remaining = (self._wall_at_last_audio + self._total_out_secs) - time.monotonic()
        self._finish_task = asyncio.create_task(self._finish_after(max(0.0, remaining)))

    async def _finish_after(self, delay):
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            if self._transport._ref_buf is not None:
                self._transport._ref_buf.set_active(False)
            self._emit("playback_finished", {"playback_id": self._transport._playback_id})
        except asyncio.CancelledError:
            pass

    def _emit(self, event, data):
        q = self._transport._event_queue
        if q is not None:
            try:
                q.put((event, data), block=False)
            except Exception:
                pass


class SdApmAudioTransport:
    def __init__(
        self,
        *,
        input_device=None,
        output_device=None,
        input_sample_rate: int = 16000,
        output_sample_rate: int = 44100,
        pipeline_sample_rate: int | None = None,
        vad_enabled: bool = True,
        vad_analyzer=None,
        event_queue=None,
        aec_reference_buffer=None,
    ):
        self._input_device = input_device
        self._output_device = output_device
        self._input_sample_rate = int(input_sample_rate)
        self._output_sample_rate = int(output_sample_rate)
        self._pipeline_sample_rate = int(pipeline_sample_rate or input_sample_rate)
        self._vad_analyzer = vad_analyzer if vad_analyzer is not None else (
            SileroVADAnalyzer() if vad_enabled else None
        )
        self._event_queue = event_queue
        self._ref_buf = aec_reference_buffer
        self._playback_id = None
        self._in_stream = None
        self._out_stream = None
        self._out_buf = _OutputBuffer()
        self._reverse_down = LinearStreamResampler()
        self._reverse_down.configure(self._output_sample_rate, self._input_sample_rate)
        self._out_delay = 0.0
        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=True,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=False,
        )
        self._input = SdApmInputTransport(self)
        self._output = SdApmOutputTransport(self)

    # -- APM helpers (called from the audio threads) --------------------
    def _note_input_delay(self, time_info) -> None:
        try:
            d = (time_info.currentTime - time_info.inputBufferAdcTime + self._out_delay) * 1000.0
            self._apm.set_stream_delay_ms(int(max(d, 0.0)))
        except Exception:
            pass

    def _apm_process_near(self, x: np.ndarray) -> bytes | None:
        n = len(x)
        if n == 0:
            return None
        out = np.empty(n, dtype=np.int16)
        step = _APM_FRAME_SAMPLES_16K
        for i in range(0, n - step + 1, step):
            chunk = x[i:i + step]
            frame = rtc.AudioFrame(
                data=chunk.tobytes(), sample_rate=self._input_sample_rate,
                num_channels=1, samples_per_channel=step,
            )
            try:
                self._apm.process_stream(frame)
                out[i:i + step] = np.frombuffer(frame.data, dtype=np.int16)[:step]
            except Exception:
                out[i:i + step] = chunk
        if n % step:
            out[n - (n % step):] = x[n - (n % step):]
        return out.tobytes()

    def _output_callback(self, outdata, frames, time_info, status):
        need = frames * 2
        pcm = self._out_buf.read(need)
        mono = np.frombuffer(pcm, dtype=np.int16)
        outdata[:, 0] = mono[:frames]
        try:
            self._out_delay = float(time_info.outputBufferDacTime - time_info.currentTime)
        except Exception:
            pass
        # Feed the AEC reverse stream with exactly what we rendered.
        step = _APM_FRAME_SAMPLES_16K
        for i in range(0, frames - step + 1, step):
            chunk = outdata[i:i + step, 0]
            try:
                rf = np.frombuffer(chunk.tobytes(), dtype=np.int16).astype(np.float32) / 32768.0
                rf = self._reverse_down.process(rf)
                rf16 = np.clip(rf * 32767.0, -32768, 32767).astype(np.int16)
                frame = rtc.AudioFrame(
                    data=rf16.tobytes(), sample_rate=self._input_sample_rate,
                    num_channels=1, samples_per_channel=min(step, len(rf16)),
                )
                self._apm.process_reverse_stream(frame)
            except Exception:
                pass

    def new_playback_id(self) -> str:
        import uuid

        self._playback_id = str(uuid.uuid4())
        return self._playback_id

    def input(self):
        return self._input

    def output(self):
        return self._output
