"""MediaDevices audio transport — LiveKit WebRTC APM, device-timed AEC.

Alternative to ``HotSwappableLocalAudioTransport``. Capture and playback both go
through one ``rtc.MediaDevices`` / ``rtc.AudioProcessingModule`` instance, so the
AEC reverse stream is the *actual rendered audio* with device-timestamped
timing. This is the path validated by ``tests/audio/livekit_apm_check.py``
(near-perfect cancellation on laptop speakers).

Enabled with ``audio_backend=mediadevices`` (or env DECISIONS_AUDIO_BACKEND).
The default transport (PyAudio) is untouched.

Trade-off vs the PyAudio transport: device hot-swap mid-session, playback speed
(time-stretch) and the fine-grained playback watermark are simplified here.
Volume, interruption flush, VAD frames and the player events are preserved.
"""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

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

logger = logging.getLogger(__name__)

try:
    from pipecat.audio.vad.vad_analyzer import VADParams, VADState
except Exception:  # pragma: no cover
    try:
        from pipecat.audio.vad.vad_analyzer import VADState
    except Exception:
        VADState = None
    VADParams = None


def _to_int16_mono(audio: bytes, src_rate: int, dst_rate: int, src_channels: int = 1) -> bytes:
    data = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
    ch = max(1, int(src_channels or 1))
    if ch > 1:
        usable = (len(data) // ch) * ch
        data = data[:usable].reshape(-1, ch).mean(axis=1)
    if src_rate != dst_rate and len(data) > 0:
        n = max(1, int(round(len(data) * dst_rate / src_rate)))
        data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data).astype(np.float32)
    return np.clip(data * 32767.0, -32768, 32767).astype(np.int16).tobytes()


class MediaDevicesInputTransport(FrameProcessor):
    def __init__(self, transport: "MediaDevicesAudioTransport"):
        super().__init__()
        self._transport = transport
        self._started = False
        self._task = None
        self._vad = transport._vad_analyzer
        self._vad_state = None

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
        await self._transport._open_input(self._vad)
        self._task = asyncio.create_task(self._pump())

    # -- compatibility shims (session / command handler expect these) ---
    def get_input_health(self) -> dict:
        return {
            "enabled": True,
            "stream_active": True,
            "audio_task_alive": self._task is not None and not self._task.done(),
            "stream_callbacks_stale": False,
        }

    async def resume_input(self):
        return None

    async def pause_idle_input(self):
        return None

    async def _pump(self):
        md = self._transport
        try:
            async for event in md._mic_stream:
                frame = event.frame
                raw = frame.data
                # VAD on the AEC-cleaned audio.
                if self._vad is not None and VADState is not None:
                    try:
                        state = await self._vad.analyze_audio(raw)
                        if state != self._vad_state and state in (VADState.SPEAKING, VADState.QUIET):
                            if state == VADState.SPEAKING:
                                await self.push_frame(VADUserStartedSpeakingFrame())
                            else:
                                await self.push_frame(VADUserStoppedSpeakingFrame())
                            self._vad_state = state
                    except Exception:
                        pass
                await self.push_frame(
                    InputAudioRawFrame(
                        audio=raw,
                        sample_rate=md._input_sample_rate,
                        num_channels=1,
                    )
                )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("MediaDevices input pump failed: %s", exc, exc_info=True)

    async def _stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


class MediaDevicesOutputTransport(FrameProcessor):
    def __init__(self, transport: "MediaDevicesAudioTransport"):
        super().__init__()
        self._transport = transport
        self._started = False
        self._volume = 1.0
        self._speed = 1.0
        self._accept_bargein_interrupt = False
        self._force_silence = False
        self._pipeline_cut = False
        self._input_transport = None
        self._buffer = bytearray()
        self._response_started_at = 0.0
        self._first_audio_at = None
        self._total_out_secs = 0.0
        self._wall_at_last_audio = 0.0
        self._scheduled_finish = None

    # -- compatibility surface used by session/STT/LLM ------------------
    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, float(volume)))

    def set_speed(self, speed: float):
        # Time-stretch is not applied in this mode; playback speed stays 1.0x.
        self._speed = float(speed)

    def set_base_vad_confidence(self, confidence: float):
        pass

    def set_device(self, device_index=None, device_name=None):
        # Mid-session device hot-swap is not supported in MediaDevices mode.
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
        md = self._transport

        if isinstance(frame, StartFrame):
            await self._ensure_started()

        if isinstance(frame, LLMFullResponseStartFrame):
            self._response_started_at = time.monotonic()
            self._first_audio_at = None
            self._total_out_secs = 0.0

        if isinstance(frame, AudioRawFrame) and isinstance(frame.audio, (bytes, bytearray)) and frame.audio:
            await self._write(frame)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            # Drop queued audio immediately and stop counting it.
            try:
                md._player._buffer.clear()
            except Exception:
                pass
            if md._ref_buf is not None:
                md._ref_buf.set_active(False)
            self._total_out_secs = 0.0
            self._wall_at_last_audio = 0.0
            self._first_audio_at = None
            self._emit("playback_finished", {"playback_id": self._transport._playback_id})
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (LLMFullResponseEndFrame, EndFrame, TTSStoppedFrame)):
            self._schedule_finish()

        await self.push_frame(frame, direction)

    async def _ensure_started(self):
        if self._started:
            return
        self._started = True
        await self._transport._open_output()

    async def _write(self, frame):
        md = self._transport
        src_rate = int(getattr(frame, "sample_rate", md._output_sample_rate) or md._output_sample_rate)
        channels = int(getattr(frame, "num_channels", 1) or 1)
        pcm = _to_int16_mono(frame.audio, src_rate, md._output_sample_rate, channels)
        if not pcm:
            return
        if self._volume != 1.0:
            arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * self._volume
            pcm = np.clip(arr, -32768, 32767).astype(np.int16).tobytes()
        try:
            await md._tts_source.capture_frame(
                md._rtc_audio_frame(pcm, md._output_sample_rate, 1, len(pcm) // 2)
            )
        except Exception as exc:
            logger.debug("MediaDevices output write failed: %s", exc)
            return
        secs = (len(pcm) // 2) / md._output_sample_rate
        self._total_out_secs += secs
        self._wall_at_last_audio = time.monotonic()
        if self._first_audio_at is None:
            self._first_audio_at = time.monotonic()
            if md._ref_buf is not None:
                md._ref_buf.set_active(True)
            self._emit("tts_started", {"source": "mediadevices", "playback_id": md._playback_id})

    def _schedule_finish(self):
        if self._scheduled_finish is not None and not self._scheduled_finish.done():
            return
        remaining = (self._wall_at_last_audio + self._total_out_secs) - time.monotonic()
        self._scheduled_finish = asyncio.create_task(self._finish_after(max(0.0, remaining)))

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


class MediaDevicesAudioTransport:
    """Holder mirroring the HotSwappable transport's public surface."""

    def __init__(
        self,
        *,
        input_device=None,
        output_device=None,
        input_sample_rate: int = 16000,
        output_sample_rate: int = 48000,
        vad_enabled: bool = True,
        vad_analyzer=None,
        event_queue=None,
        aec_reference_buffer=None,
    ):
        from livekit import rtc

        self._rtc = rtc
        self._input_device = input_device
        self._output_device = output_device
        self._input_sample_rate = int(input_sample_rate)
        self._output_sample_rate = int(output_sample_rate)
        self._vad_analyzer = vad_analyzer if vad_analyzer is not None else (
            SileroVADAnalyzer() if vad_enabled else None
        )
        self._vad_enabled = self._vad_analyzer is not None
        self._event_queue = event_queue
        self._ref_buf = aec_reference_buffer
        self._playback_id = None
        self._devices = None
        self._mic = None
        self._mic_stream = None
        self._player = None
        self._tts_source = None
        self._input = MediaDevicesInputTransport(self)
        self._output = MediaDevicesOutputTransport(self)

    # -- construction helpers -------------------------------------------
    def _rtc_audio_frame(self, pcm: bytes, sample_rate: int, channels: int, samples: int):
        return self._rtc.AudioFrame(
            data=pcm, sample_rate=sample_rate, num_channels=channels, samples_per_channel=max(1, samples)
        )

    async def _open_input(self, vad):
        loop = asyncio.get_running_loop()
        self._devices = self._rtc.MediaDevices(
            loop=loop,
            input_sample_rate=self._input_sample_rate,
            output_sample_rate=self._output_sample_rate,
            num_channels=1,
            blocksize=4800,
        )
        self._mic = self._devices.open_input(
            enable_aec=True,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=False,
            input_device=self._input_device,
        )
        mic_track = self._rtc.LocalAudioTrack.create_audio_track("decisions-mic", self._mic.source)
        self._mic_stream = self._rtc.AudioStream(
            mic_track, sample_rate=self._input_sample_rate, num_channels=1
        )
        logger.info(
            "MediaDevices transport: input open (sr=%s, aec=True)", self._input_sample_rate
        )

    async def _open_output(self):
        if self._devices is None:
            loop = asyncio.get_running_loop()
            self._devices = self._rtc.MediaDevices(
                loop=loop,
                input_sample_rate=self._input_sample_rate,
                output_sample_rate=self._output_sample_rate,
                num_channels=1,
                blocksize=4800,
            )
        self._player = self._devices.open_output(output_device=self._output_device)
        # OutputPlayer is created with the input's APM for reverse-stream AEC.
        self._tts_source = self._rtc.AudioSource(self._output_sample_rate, 1, loop=asyncio.get_running_loop())
        tts_track = self._rtc.LocalAudioTrack.create_audio_track("decisions-tts", self._tts_source)
        await self._player.add_track(tts_track)
        await self._player.start()
        logger.info(
            "MediaDevices transport: output open (sr=%s, aec reverse wired)", self._output_sample_rate
        )

    def new_playback_id(self) -> str:
        import uuid

        self._playback_id = str(uuid.uuid4())
        return self._playback_id

    # -- public surface --------------------------------------------------
    def input(self):
        return self._input

    def output(self):
        return self._output
