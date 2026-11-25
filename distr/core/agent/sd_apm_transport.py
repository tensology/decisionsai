"""Cross-platform sounddevice + LiveKit WebRTC APM full-duplex audio transport.

Primary full-duplex transport on Windows (WASAPI) and Linux/Unix (PipeWire /
PulseAudio / ALSA), and automatic fallback on macOS when Apple VoiceProcessingIO
is unavailable.

Capture and playback run through ONE thread-safe ``rtc.AudioProcessingModule``
inside low-latency (20 ms) sounddevice callbacks so the AEC reverse stream is
the exact rendered speaker audio with device-timestamped latency alignment.
"""

from __future__ import annotations

import asyncio
from collections import deque
import logging
import threading
import time
from typing import Optional

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
    TTSStartedFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from distr.core.agent.transport import AudioPlaybackState
from distr.core.audio.stream_resampler import LinearStreamResampler

logger = logging.getLogger(__name__)

try:
    from pipecat.audio.vad.vad_analyzer import VADParams, VADState
except Exception:  # pragma: no cover
    try:
        from pipecat.audio.vad.vad_analyzer import VADState
    except Exception:
        VADState = None
    VADParams = None

_APM_RATE = 16000
_APM_FRAME_SAMPLES = 160  # 10 ms @ 16 kHz (required by WebRTC APM)
_CALLBACK_BLOCK_SECS = 0.02  # 20 ms low-latency callbacks
# Playback end-of-reply tuning (seconds); see macos_vpio_transport for rationale.
_FINISH_GRACE_SECS = 0.4
_REPLY_IDLE_FALLBACK_SECS = 20.0
_FINISH_HARD_CAP_SECS = 5.0


class _OutputBuffer:
    """Thread-safe PCM byte buffer shared between async writer and audio callback."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._lock = threading.Lock()

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


class SdApmAecMetrics:
    """Expose real post-AEC telemetry and lag-tolerant echo correlation to STT."""

    is_hardware_aec = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ref_history: deque[np.ndarray] = deque(maxlen=25)  # ~250 ms @ 10 ms frames
        self._last_ref_rms = 0.0
        self._last_metrics: dict[str, float | int] = {
            "input_rms": 0.0,
            "residual_rms": 0.0,
            "reference_rms": 0.0,
            "reference_correlation": 0.0,
            "erle_db": 0.0,
        }

    @property
    def calibrated(self) -> bool:
        return True

    @property
    def last_metrics(self) -> dict[str, float | int]:
        with self._lock:
            return dict(self._last_metrics)

    def record_reference(self, frame_16k_i16: np.ndarray) -> None:
        if frame_16k_i16.size == 0:
            return
        f = frame_16k_i16.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(f * f)))
        with self._lock:
            self._ref_history.append(f)
            self._last_ref_rms = rms

    def record_near(self, raw_16k_i16: np.ndarray, clean_16k_i16: np.ndarray) -> None:
        if raw_16k_i16.size == 0:
            return
        raw_f = raw_16k_i16.astype(np.float32) / 32768.0
        clean_f = clean_16k_i16.astype(np.float32) / 32768.0
        in_rms = float(np.sqrt(np.mean(raw_f * raw_f)))
        out_rms = float(np.sqrt(np.mean(clean_f * clean_f)))
        erle_db = 20.0 * np.log10(max(in_rms, 1e-8) / max(out_rms, 1e-8))

        with self._lock:
            ref_rms = self._last_ref_rms
            history = list(self._ref_history)

        corr = self._lagged_peak_correlation(raw_f, history) if ref_rms > 1e-4 else 0.0
        with self._lock:
            self._last_metrics = {
                "input_rms": in_rms,
                "residual_rms": out_rms,
                "reference_rms": ref_rms,
                "reference_correlation": float(corr),
                "erle_db": float(erle_db),
            }

    @staticmethod
    def _lagged_peak_correlation(near_f: np.ndarray, ref_frames: list[np.ndarray]) -> float:
        """Peak normalized cross-correlation over recent reference frames (handles 0-250 ms delay)."""
        if not ref_frames or near_f.size < 32:
            return 0.0
        ref = np.concatenate(ref_frames)
        # Use up to 20 ms of near-end signal and slide across the reference buffer via FFT/correlate.
        seg = near_f[-min(near_f.size, 320):]
        seg = seg - np.mean(seg)
        seg_norm = float(np.linalg.norm(seg))
        if seg_norm < 1e-6 or ref.size < seg.size:
            return 0.0
        # Coarse stride (every 4 samples = 0.25 ms) keeps callback CPU negligible.
        best = 0.0
        n = seg.size
        max_start = ref.size - n
        step = 4
        for start in range(0, max_start + 1, step):
            w = ref[start:start + n]
            w_c = w - np.mean(w)
            denom = seg_norm * float(np.linalg.norm(w_c))
            if denom > 1e-6:
                c = abs(float(np.dot(seg, w_c))) / denom
                if c > best:
                    best = c
                    if best >= 0.85:
                        break
        return best


def _open_sd_stream(cls, *, device, requested_rate: int, callback, channels: int = 1):
    """Open a sounddevice stream at requested_rate, falling back to device default rate.

    Windows WASAPI and Linux PipeWire/ALSA devices often lock to 48 kHz and reject
    16 kHz / 44.1 kHz directly. Falling back cleanly ensures cross-platform reliability.
    """
    rates = [int(requested_rate)]
    try:
        kind = "input" if cls is sd.InputStream else "output"
        info = sd.query_devices(device, kind=kind) if device is not None else sd.query_devices(kind=kind)
        default_sr = int(round(float(info.get("default_samplerate") or 0)))
        if default_sr > 0 and default_sr not in rates:
            rates.append(default_sr)
    except Exception:
        pass
    for fallback_sr in (48000, 44100, 16000):
        if fallback_sr not in rates:
            rates.append(fallback_sr)

    last_err = None
    for sr in rates:
        blocksize = max(160, int(round(sr * _CALLBACK_BLOCK_SECS)))
        stream = None
        try:
            stream = cls(
                callback=callback,
                channels=channels,
                samplerate=sr,
                dtype="int16",
                blocksize=blocksize,
                device=device,
            )
            stream.start()
            return stream, sr
        except Exception as exc:
            last_err = exc
            # cls(...) may have succeeded before start() raised; close it so the
            # device handle is not leaked while we try the next rate.
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            logger.debug("sounddevice %s failed at %d Hz (%s); trying next rate", cls.__name__, sr, exc)
    raise RuntimeError(f"Could not open {cls.__name__} on device={device!r}: {last_err}")


class SdApmInputTransport(FrameProcessor):
    def __init__(self, transport: "SdApmAudioTransport"):
        super().__init__()
        self._transport = transport
        self._started = False
        self._task: Optional[asyncio.Task] = None
        self._queue: Optional[asyncio.Queue] = None
        self._vad = transport._vad_analyzer
        self._vad_state = None
        self._last_frame_at: float = 0.0
        self._in_to_apm = LinearStreamResampler()
        self._apm_to_pipe = LinearStreamResampler()
        self._apm_to_pipe.configure(_APM_RATE, transport._pipeline_sample_rate)

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
        self._configure_vad()
        t = self._transport
        loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=100)

        def callback(indata, frames, time_info, status):
            t._note_input_delay(time_info)
            mono = indata[:, 0].copy()
            if t._actual_input_rate != _APM_RATE:
                f = mono.astype(np.float32) / 32768.0
                f = self._in_to_apm.process(f)
                mono = np.clip(f * 32767.0, -32768, 32767).astype(np.int16)
            cleaned = t._apm_process_near(mono)
            if cleaned:
                def _enqueue(chunk=cleaned):
                    if self._queue is not None:
                        if self._queue.full():
                            try:
                                self._queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        try:
                            self._queue.put_nowait(chunk)
                        except asyncio.QueueFull:
                            pass
                loop.call_soon_threadsafe(_enqueue)

        stream, actual_sr = _open_sd_stream(
            sd.InputStream,
            device=t._input_device,
            requested_rate=t._input_sample_rate,
            callback=callback,
            channels=1,
        )
        t._actual_input_rate = actual_sr
        if actual_sr != _APM_RATE:
            self._in_to_apm.configure(actual_sr, _APM_RATE)
        t._in_stream = stream
        self._task = asyncio.create_task(self._pump())
        logger.info(
            "sounddevice APM transport: input open (hw_sr=%s, apm_sr=%s, aec=True)",
            actual_sr,
            _APM_RATE,
        )

    def _configure_vad(self):
        """Pipecat's BaseInputTransport normally sets this; custom transports must too."""
        if self._vad is not None:
            try:
                self._vad.set_sample_rate(self._transport._pipeline_sample_rate)
            except Exception as exc:
                logger.warning("sounddevice APM: could not set VAD sample rate: %s", exc)

    async def _pump(self):
        t = self._transport
        try:
            while True:
                pcm16 = await self._queue.get()
                self._last_frame_at = time.monotonic()
                if t._pipeline_sample_rate != _APM_RATE:
                    f = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
                    f = self._apm_to_pipe.process(f)
                    pcm16 = np.clip(f * 32767.0, -32768, 32767).astype(np.int16).tobytes()
                if not pcm16:
                    continue
                if self._vad is not None and VADState is not None:
                    try:
                        state = await self._vad.analyze_audio(pcm16)
                        if state != self._vad_state and state in (VADState.SPEAKING, VADState.QUIET):
                            await self.push_frame(
                                VADUserStartedSpeakingFrame()
                                if state == VADState.SPEAKING
                                else VADUserStoppedSpeakingFrame()
                            )
                            self._vad_state = state
                    except Exception:
                        pass
                await self.push_frame(
                    InputAudioRawFrame(
                        audio=pcm16,
                        sample_rate=t._pipeline_sample_rate,
                        num_channels=1,
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

    def get_input_health(self) -> dict:
        stale = bool(self._last_frame_at and (time.monotonic() - self._last_frame_at) > 3.0)
        return {
            "enabled": True,
            "stream_active": self._transport._in_stream is not None,
            "audio_task_alive": self._task is not None and not self._task.done(),
            "stream_callbacks_stale": stale,
            "backend": "sdapm",
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
        self._state = AudioPlaybackState.IDLE
        self._accept_bargein_interrupt = False
        self._force_silence = False
        self._pipeline_cut = False
        self._input_transport = None
        self._first_audio_at: Optional[float] = None
        self._finish_task: Optional[asyncio.Task] = None
        self._out_resampler = LinearStreamResampler()
        self._out_resampler_src_rate = 0
        # Playback ends once per reply (LLMFullResponseEndFrame), not per sentence.
        self._in_reply = False
        self._wall_at_last_audio = 0.0
        self._play_watermark = 0.0

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, float(volume)))

    def set_speed(self, speed: float):
        self._speed = float(speed)

    def set_base_vad_confidence(self, confidence: float):
        vad = self._transport._vad_analyzer
        if vad is not None and VADParams is not None:
            try:
                params = getattr(vad, "_params", None)
                if params is not None:
                    params.confidence = float(confidence)
            except Exception:
                pass

    def set_device(self, device_index=None, device_name=None):
        return None

    def _active_output_route(self) -> dict:
        t = self._transport
        return {
            "backend": "sdapm",
            "aec": "livekit_webrtc_apm",
            "output_device": t._output_device,
            "output_sample_rate": t._actual_output_rate,
            "state": self._state.name,
            "buffered_ms": round(self._remaining_playback_secs() * 1000.0, 1),
        }

    def _remaining_playback_secs(self) -> float:
        t = self._transport
        sr = max(1, int(t._actual_output_rate or t._output_sample_rate))
        # int16 mono bytes -> seconds at the device rate. Never below the
        # local wall-clock estimate of what has been written.
        buffered = len(t._out_buf) / (2.0 * sr)
        local = max(0.0, self._play_watermark - time.monotonic())
        return max(buffered, local)

    async def wait_for_playback_idle(self, timeout: float = 10.0):
        end = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < end:
            if self._state == AudioPlaybackState.IDLE:
                return
            rem = self._remaining_playback_secs()
            if rem <= 0.02 and self._state in (AudioPlaybackState.DRAINING, AudioPlaybackState.IDLE):
                return
            await asyncio.sleep(min(max(rem, 0.02), 0.06))

    async def _abort_output_stream_async(self):
        was_active = self._state != AudioPlaybackState.IDLE
        self._force_silence = True
        self._flush_now()
        if was_active:
            self._emit("playback_finished", {"playback_id": self._transport._playback_id})

    def _flush_now(self):
        t = self._transport
        t._out_buf.clear()
        if self._finish_task is not None and not self._finish_task.done():
            self._finish_task.cancel()
            self._finish_task = None
        if t._ref_buf is not None:
            t._ref_buf.set_active(False)
        self._first_audio_at = None
        self._wall_at_last_audio = 0.0
        self._play_watermark = 0.0
        self._in_reply = False
        self._state = AudioPlaybackState.IDLE

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        t = self._transport

        if isinstance(frame, StartFrame):
            await self._ensure_started()

        if isinstance(frame, LLMFullResponseStartFrame):
            self._in_reply = True

        if isinstance(frame, (LLMFullResponseStartFrame, TTSStartedFrame)):
            self._force_silence = False
            self._pipeline_cut = False
            if self._finish_task is not None and not self._finish_task.done():
                self._finish_task.cancel()
                self._finish_task = None
            if self._state == AudioPlaybackState.IDLE:
                self._first_audio_at = None
                self._state = AudioPlaybackState.SYNTHESIZING
                t.new_playback_id()

        if isinstance(frame, AudioRawFrame) and isinstance(frame.audio, (bytes, bytearray)) and frame.audio:
            if not self._force_silence and not self._pipeline_cut:
                self._write(frame)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            was_active = self._state != AudioPlaybackState.IDLE
            self._force_silence = True
            self._flush_now()
            if was_active:
                self._emit("playback_finished", {"playback_id": t._playback_id})
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
            # True end of the reply (or of the stream): drain once, then finish.
            self._in_reply = False
            if self._state in (AudioPlaybackState.SYNTHESIZING, AudioPlaybackState.PLAYING):
                self._state = AudioPlaybackState.DRAINING
                self._schedule_finish()
        elif isinstance(frame, TTSStoppedFrame):
            if self._in_reply:
                # Per-sentence stop inside a reply: keep the same playback id
                # and AEC reference; only a safety net if the end never comes.
                if self._state in (AudioPlaybackState.SYNTHESIZING, AudioPlaybackState.PLAYING):
                    self._schedule_finish(idle_timeout=_REPLY_IDLE_FALLBACK_SECS)
            elif self._state in (AudioPlaybackState.SYNTHESIZING, AudioPlaybackState.PLAYING):
                self._state = AudioPlaybackState.DRAINING
                self._schedule_finish()

        await self.push_frame(frame, direction)

    async def _ensure_started(self):
        if self._started:
            return
        self._started = True
        t = self._transport
        stream, actual_sr = _open_sd_stream(
            sd.OutputStream,
            device=t._output_device,
            requested_rate=t._output_sample_rate,
            callback=t._output_callback,
            channels=1,
        )
        t._actual_output_rate = actual_sr
        t._reverse_down.configure(actual_sr, _APM_RATE)
        t._out_stream = stream
        logger.info(
            "sounddevice APM transport: output open (hw_sr=%s, aec reverse wired @ %s Hz)",
            actual_sr,
            _APM_RATE,
        )

    def _write(self, frame):
        t = self._transport
        dst_rate = int(t._actual_output_rate or t._output_sample_rate)
        src_rate = int(getattr(frame, "sample_rate", dst_rate) or dst_rate)
        channels = max(1, int(getattr(frame, "num_channels", 1) or 1))
        data = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32) / 32768.0
        if data.size == 0:
            return
        if channels > 1:
            usable = (data.size // channels) * channels
            data = data[:usable].reshape(-1, channels).mean(axis=1)
        if src_rate != dst_rate:
            if self._out_resampler_src_rate != src_rate:
                self._out_resampler.configure(src_rate, dst_rate)
                self._out_resampler_src_rate = src_rate
            data = self._out_resampler.process(data)
        if self._volume != 1.0:
            data = data * self._volume
        if data.size == 0:
            return
        pcm = np.clip(data * 32767.0, -32768, 32767).astype(np.int16).tobytes()
        if not pcm:
            return
        if self._state == AudioPlaybackState.IDLE:
            t.new_playback_id()
        t._out_buf.extend(pcm)
        now = time.monotonic()
        secs = (len(pcm) // 2) / float(max(1, dst_rate))
        self._wall_at_last_audio = now
        self._play_watermark = max(self._play_watermark, now) + secs
        self._state = AudioPlaybackState.PLAYING
        if self._first_audio_at is None:
            self._first_audio_at = time.monotonic()
            if t._ref_buf is not None:
                t._ref_buf.set_active(True)
            self._emit("tts_started", {"source": "transport", "backend": "sdapm", "playback_id": t._playback_id})

    def _schedule_finish(self, idle_timeout: float = 0.0):
        if self._finish_task is not None and not self._finish_task.done():
            self._finish_task.cancel()
        self._finish_task = asyncio.create_task(self._wait_and_finish(idle_timeout))

    async def _wait_and_finish(self, idle_timeout: float = 0.0):
        """Finish after the buffer drains and stays quiet for the grace period
        (end of reply) or for ``idle_timeout`` (in-reply safety net)."""
        try:
            needed = max(_FINISH_GRACE_SECS, float(idle_timeout or 0.0))
            drained_at: Optional[float] = None
            while True:
                now = time.monotonic()
                rem = self._remaining_playback_secs()
                if rem > 0.02:
                    drained_at = None
                    if now > self._play_watermark + _FINISH_HARD_CAP_SECS + needed:
                        break
                    await asyncio.sleep(min(max(rem, 0.02), 0.04))
                    continue
                if drained_at is None or self._wall_at_last_audio > drained_at:
                    drained_at = now
                quiet = now - drained_at
                if quiet >= needed:
                    break
                await asyncio.sleep(min(needed - quiet, 0.1))
            self._finish_task = None
            if self._transport._ref_buf is not None:
                self._transport._ref_buf.set_active(False)
            self._first_audio_at = None
            self._play_watermark = 0.0
            self._state = AudioPlaybackState.IDLE
            self._emit("playback_finished", {"playback_id": self._transport._playback_id})
        except asyncio.CancelledError:
            pass

    def _emit(self, event: str, data: dict):
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
        pipeline_sample_rate: Optional[int] = None,
        vad_enabled: bool = True,
        vad_analyzer=None,
        event_queue=None,
        aec_reference_buffer=None,
    ):
        self._input_device = input_device
        self._output_device = output_device
        self._input_sample_rate = int(input_sample_rate)
        self._output_sample_rate = int(output_sample_rate)
        self._actual_input_rate = int(input_sample_rate)
        self._actual_output_rate = int(output_sample_rate)
        self._pipeline_sample_rate = int(pipeline_sample_rate or input_sample_rate)
        self._vad_analyzer = vad_analyzer if vad_analyzer is not None else (
            SileroVADAnalyzer() if vad_enabled else None
        )
        self._event_queue = event_queue
        self._ref_buf = aec_reference_buffer
        self._playback_id: Optional[str] = None
        self._in_stream = None
        self._out_stream = None
        self._out_buf = _OutputBuffer()
        self._reverse_down = LinearStreamResampler()
        self._reverse_down.configure(self._output_sample_rate, _APM_RATE)
        self._reverse_accum = np.zeros(0, dtype=np.int16)
        self._near_accum = np.zeros(0, dtype=np.int16)
        self._out_delay = 0.0
        self._apm_lock = threading.Lock()
        self._aec_metrics = SdApmAecMetrics()
        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=True,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=False,
        )
        self._input = SdApmInputTransport(self)
        self._output = SdApmOutputTransport(self)
        self._output._input_transport = self._input

    @property
    def aec_metrics(self) -> SdApmAecMetrics:
        return self._aec_metrics

    # -- APM helpers (called from PortAudio threads, serialized via _apm_lock) --
    def _note_input_delay(self, time_info) -> None:
        try:
            d = (time_info.currentTime - time_info.inputBufferAdcTime + self._out_delay) * 1000.0
            delay_ms = int(max(0.0, min(500.0, d)))
            with self._apm_lock:
                self._apm.set_stream_delay_ms(delay_ms)
        except Exception:
            pass

    def _apm_process_near(self, x_16k: np.ndarray) -> bytes:
        """Process 16 kHz mono int16 mic samples in exact 10 ms (160-sample) APM frames."""
        if x_16k.size == 0:
            return b""
        buf = np.concatenate((self._near_accum, x_16k)) if self._near_accum.size else x_16k
        n_full = (buf.size // _APM_FRAME_SAMPLES) * _APM_FRAME_SAMPLES
        if n_full == 0:
            self._near_accum = buf
            return b""
        self._near_accum = buf[n_full:].copy()
        work = buf[:n_full]
        out = np.empty(n_full, dtype=np.int16)
        with self._apm_lock:
            for i in range(0, n_full, _APM_FRAME_SAMPLES):
                chunk = work[i:i + _APM_FRAME_SAMPLES]
                frame = rtc.AudioFrame(
                    data=chunk.tobytes(),
                    sample_rate=_APM_RATE,
                    num_channels=1,
                    samples_per_channel=_APM_FRAME_SAMPLES,
                )
                try:
                    self._apm.process_stream(frame)
                    out[i:i + _APM_FRAME_SAMPLES] = np.frombuffer(frame.data, dtype=np.int16)[:_APM_FRAME_SAMPLES]
                except Exception:
                    out[i:i + _APM_FRAME_SAMPLES] = chunk
        self._aec_metrics.record_near(work, out)
        return out.tobytes()

    def _feed_reverse_16k(self, mono_out_i16: np.ndarray) -> None:
        """Downsample rendered speaker audio to 16 kHz and feed exact 10 ms frames to APM."""
        if mono_out_i16.size == 0:
            return
        if self._actual_output_rate != _APM_RATE:
            rf = mono_out_i16.astype(np.float32) / 32768.0
            rf = self._reverse_down.process(rf)
            rf16 = np.clip(rf * 32767.0, -32768, 32767).astype(np.int16)
        else:
            rf16 = mono_out_i16
        if rf16.size == 0:
            return
        buf = np.concatenate((self._reverse_accum, rf16)) if self._reverse_accum.size else rf16
        n_full = (buf.size // _APM_FRAME_SAMPLES) * _APM_FRAME_SAMPLES
        if n_full == 0:
            self._reverse_accum = buf
            return
        self._reverse_accum = buf[n_full:].copy()
        with self._apm_lock:
            for i in range(0, n_full, _APM_FRAME_SAMPLES):
                chunk = buf[i:i + _APM_FRAME_SAMPLES]
                self._aec_metrics.record_reference(chunk)
                frame = rtc.AudioFrame(
                    data=chunk.tobytes(),
                    sample_rate=_APM_RATE,
                    num_channels=1,
                    samples_per_channel=_APM_FRAME_SAMPLES,
                )
                try:
                    self._apm.process_reverse_stream(frame)
                except Exception:
                    pass

    def _output_callback(self, outdata, frames, time_info, status):
        need = frames * 2
        pcm = self._out_buf.read(need)
        mono = np.frombuffer(pcm, dtype=np.int16)[:frames]
        outdata[:, 0] = mono
        try:
            self._out_delay = float(time_info.outputBufferDacTime - time_info.currentTime)
        except Exception:
            pass
        self._feed_reverse_16k(mono)

    def new_playback_id(self) -> str:
        import uuid

        self._playback_id = str(uuid.uuid4())
        return self._playback_id

    async def cleanup(self):
        await self._input._stop()
        if self._output._finish_task is not None and not self._output._finish_task.done():
            self._output._finish_task.cancel()
        if self._out_stream is not None:
            try:
                self._out_stream.stop()
                self._out_stream.close()
            except Exception:
                pass
            self._out_stream = None

    def input(self):
        return self._input

    def output(self):
        return self._output
