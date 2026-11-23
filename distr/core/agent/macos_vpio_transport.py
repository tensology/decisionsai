"""Native macOS VoiceProcessingIO full-duplex audio transport for Pipecat.

Runs Apple's CoreAudio ``kAudioUnitSubType_VoiceProcessingIO`` AudioUnit inside
an isolated native helper subprocess (``macos_vpio_helper``).  Apple's OS DSP
handles acoustic echo cancellation, MacBook microphone-array beamforming, and
Voice Isolation at the CoreAudio HAL boundary with exact hardware render/capture
timing — eliminating self-interruption in hands-free continuous mode while
keeping Pipecat as the pipeline and isolating CoreAudio from the Python GIL.

Protocol (little-endian framing over stdin/stdout):
    Header: [uint8 type][uint32_le payload_len]
    stdout -> Python:
        0x01 MIC   : 16 kHz mono int16 PCM (640 bytes = 20 ms)
        0x02 READY : UTF-8 JSON readiness metadata
        0x03 STATS : 3 x float32 LE [buffered_ms, in_rms, out_rms]
    Python -> stdin:
        0x01 SPK   : 16 kHz mono int16 PCM
        0x02 FLUSH : len=0, drop queued speaker audio immediately (< 1 ms)
        0x03 VOL   : float32 LE volume in [0.0, 1.0]
        0x04 QUIT  : len=0, clean shutdown
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

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

VPIO_SAMPLE_RATE = 16000
_MSG_AUDIO = 0x01
_MSG_READY = 0x02
_MSG_FLUSH = 0x02
_MSG_STATS = 0x03
_MSG_VOL = 0x03
_MSG_QUIT = 0x04


def _vpio_source_path() -> Path:
    return Path(__file__).resolve().parent.parent / "audio" / "native" / "macos_vpio_helper.c"


def ensure_vpio_helper_binary() -> str:
    """Locate or compile the native macOS VoiceProcessingIO helper binary.

    Raises RuntimeError if not on macOS or if the binary cannot be found/built.
    """
    if sys.platform != "darwin":
        raise RuntimeError("MacOsVpioAudioTransport is only supported on macOS")

    override = (os.environ.get("DECISIONS_VPIO_HELPER_PATH") or "").strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override

    # Check bundled prebuilt binary next to the C source or app bundle
    src_path = _vpio_source_path()
    prebuilt = src_path.parent / "macos_vpio_helper"
    if prebuilt.is_file() and os.access(prebuilt, os.X_OK):
        # Use prebuilt if no source or if prebuilt is newer than source
        if not src_path.is_file() or prebuilt.stat().st_mtime >= src_path.stat().st_mtime:
            return str(prebuilt)

    if not src_path.is_file():
        raise RuntimeError(f"VPIO helper source not found: {src_path}")

    src_bytes = src_path.read_bytes()
    digest = hashlib.sha256(src_bytes).hexdigest()[:12]
    cache_dir = Path.home() / ".decisions" / "cache" / "native"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"macos_vpio_helper_{digest}"
    if target.is_file() and os.access(target, os.X_OK):
        return str(target)

    clang = shutil.which("clang")
    if not clang:
        raise RuntimeError("clang not found; cannot build macOS VoiceProcessingIO helper")

    tmp_out = cache_dir / f"macos_vpio_helper_{digest}.{os.getpid()}.tmp"
    cmd = [
        clang,
        "-O2",
        "-framework", "AudioToolbox",
        "-framework", "CoreAudio",
        "-framework", "CoreFoundation",
        "-lpthread",
        str(src_path),
        "-o", str(tmp_out),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        try:
            tmp_out.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(f"Failed to compile macOS VPIO helper: {(res.stderr or res.stdout)[:400]}")

    os.chmod(tmp_out, 0o755)
    os.replace(tmp_out, target)
    logger.info("Compiled macOS VoiceProcessingIO helper: %s", target)
    return str(target)


class VpioAecMetrics:
    """Expose hardware VPIO telemetry to STT's smart barge-in gate."""

    is_hardware_aec = True

    def __init__(self) -> None:
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
        return dict(self._last_metrics)

    def update(self, *, in_rms: float, out_rms: float) -> None:
        # Output from VPIO is already post-AEC residual.
        self._last_metrics = {
            "input_rms": float(in_rms),
            "residual_rms": float(in_rms),
            "reference_rms": float(out_rms),
            "reference_correlation": 0.0,
            "erle_db": 0.0,
        }


class MacOsVpioInputTransport(FrameProcessor):
    def __init__(self, transport: "MacOsVpioAudioTransport"):
        super().__init__()
        self._transport = transport
        self._started = False
        self._task: Optional[asyncio.Task] = None
        self._vad = transport._vad_analyzer
        self._vad_state = None
        self._last_frame_at: float = 0.0
        self._down = LinearStreamResampler()
        self._down.configure(VPIO_SAMPLE_RATE, transport._pipeline_sample_rate)

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
        await self._transport._ensure_helper_started()
        self._task = asyncio.create_task(self._pump())

    def get_input_health(self) -> dict:
        proc = self._transport._proc
        alive = proc is not None and proc.returncode is None
        stale = bool(self._last_frame_at and (time.monotonic() - self._last_frame_at) > 3.0)
        return {
            "enabled": True,
            "stream_active": alive,
            "audio_task_alive": self._task is not None and not self._task.done(),
            "stream_callbacks_stale": stale,
            "backend": "macos_vpio",
        }

    async def resume_input(self):
        if self._transport._proc is None or self._transport._proc.returncode is not None:
            await self._transport._restart_helper()

    async def pause_idle_input(self):
        # Keep VPIO running so continuous mode & instant PTT stay warm.
        return None

    async def _pump(self):
        t = self._transport
        restarts = 0
        try:
            while not t._closing:
                proc = t._proc
                if proc is None or proc.stdout is None:
                    await asyncio.sleep(0.1)
                    continue
                try:
                    hdr = await proc.stdout.readexactly(5)
                    mtype, mlen = struct.unpack("<BI", hdr)
                    body = await proc.stdout.readexactly(mlen) if mlen else b""
                except asyncio.IncompleteReadError:
                    if t._closing:
                        break
                    restarts += 1
                    logger.warning(
                        "MacOS VPIO helper stream ended unexpectedly (restart #%d); restarting",
                        restarts,
                    )
                    await asyncio.sleep(min(2.0, 0.25 * restarts))
                    await t._restart_helper()
                    continue

                restarts = 0
                if mtype == _MSG_READY:
                    logger.info(
                        "MacOS VPIO helper ready: %s",
                        body.decode("utf-8", errors="replace"),
                    )
                    continue
                if mtype == _MSG_STATS and len(body) == 12:
                    buffered_ms, in_rms, out_rms = struct.unpack("<3f", body)
                    t._buffered_ms = max(0.0, float(buffered_ms))
                    t._last_stats_at = time.monotonic()
                    t._aec_metrics.update(in_rms=in_rms, out_rms=out_rms)
                    continue
                if mtype != _MSG_AUDIO or not body:
                    continue

                self._last_frame_at = time.monotonic()
                pcm = body
                arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                if arr.size:
                    frame_rms = float(np.sqrt(np.mean(arr * arr)))
                    t._aec_metrics.update(
                        in_rms=frame_rms,
                        out_rms=float(t._aec_metrics.last_metrics.get("reference_rms", 0.0)),
                    )

                if t._pipeline_sample_rate != VPIO_SAMPLE_RATE:
                    f = self._down.process(arr)
                    pcm = np.clip(f * 32767.0, -32768, 32767).astype(np.int16).tobytes()
                if not pcm:
                    continue

                if self._vad is not None and VADState is not None:
                    try:
                        state = await self._vad.analyze_audio(pcm)
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
                        audio=pcm,
                        sample_rate=t._pipeline_sample_rate,
                        num_channels=1,
                    )
                )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("MacOS VPIO input pump failed: %s", exc, exc_info=True)

    async def _stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


class MacOsVpioOutputTransport(FrameProcessor):
    def __init__(self, transport: "MacOsVpioAudioTransport"):
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
        self._total_out_secs = 0.0
        self._wall_at_last_audio = 0.0
        self._finish_task: Optional[asyncio.Task] = None
        self._out_resampler = LinearStreamResampler()
        self._out_resampler_src_rate = 0

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, float(volume)))
        self._transport._send_volume_sync(self._volume)

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
        name = (device_name or "").strip() if isinstance(device_name, str) else ""
        if name and name != self._transport._output_device_name:
            self._transport._output_device_name = name
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._transport._restart_helper())
            except RuntimeError:
                pass
        return None

    def _active_output_route(self) -> dict:
        return {
            "backend": "macos_vpio",
            "aec": "apple_vpio",
            "output_device": self._transport._output_device_name or "System Default",
            "input_device": self._transport._input_device_name or "System Default",
            "state": self._state.name,
            "buffered_ms": round(self._transport._buffered_ms, 1),
        }

    def _remaining_playback_secs(self) -> float:
        t = self._transport
        if t._last_stats_at and (time.monotonic() - t._last_stats_at) < 0.5:
            return max(0.0, t._buffered_ms / 1000.0)
        if self._first_audio_at is None:
            return 0.0
        return max(0.0, (self._first_audio_at + self._total_out_secs) - time.monotonic())

    async def wait_for_playback_idle(self, timeout: float = 10.0):
        end = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < end:
            if self._state == AudioPlaybackState.IDLE:
                return
            rem = self._remaining_playback_secs()
            if rem <= 0.02 and self._state in (AudioPlaybackState.DRAINING, AudioPlaybackState.IDLE):
                return
            await asyncio.sleep(min(max(rem, 0.03), 0.08))

    async def _abort_output_stream_async(self):
        """Immediately stop speaker audio (called from command_handler on Stop/PTT)."""
        self._force_silence = True
        self._flush_now()

    def _flush_now(self):
        t = self._transport
        t._send_flush_sync()
        t._buffered_ms = 0.0
        if self._finish_task is not None and not self._finish_task.done():
            self._finish_task.cancel()
            self._finish_task = None
        if t._ref_buf is not None:
            t._ref_buf.set_active(False)
        self._total_out_secs = 0.0
        self._wall_at_last_audio = 0.0
        self._first_audio_at = None
        self._state = AudioPlaybackState.IDLE

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        t = self._transport

        if isinstance(frame, StartFrame):
            await self._ensure_started()

        if isinstance(frame, (LLMFullResponseStartFrame, TTSStartedFrame)):
            self._force_silence = False
            self._pipeline_cut = False
            if self._finish_task is not None and not self._finish_task.done():
                self._finish_task.cancel()
                self._finish_task = None
            if self._state == AudioPlaybackState.IDLE:
                self._first_audio_at = None
                self._total_out_secs = 0.0
                self._state = AudioPlaybackState.SYNTHESIZING

        if isinstance(frame, AudioRawFrame) and isinstance(frame.audio, (bytes, bytearray)) and frame.audio:
            if not self._force_silence and not self._pipeline_cut:
                await self._write(frame)
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

        if isinstance(frame, (LLMFullResponseEndFrame, EndFrame, TTSStoppedFrame)):
            if self._state in (AudioPlaybackState.SYNTHESIZING, AudioPlaybackState.PLAYING):
                self._state = AudioPlaybackState.DRAINING
                self._schedule_finish()

        await self.push_frame(frame, direction)

    async def _ensure_started(self):
        if self._started:
            return
        self._started = True
        await self._transport._ensure_helper_started()
        self._transport._send_volume_sync(self._volume)

    async def _write(self, frame):
        t = self._transport
        src_rate = int(getattr(frame, "sample_rate", t._output_sample_rate) or t._output_sample_rate)
        channels = max(1, int(getattr(frame, "num_channels", 1) or 1))
        data = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32) / 32768.0
        if data.size == 0:
            return
        if channels > 1:
            usable = (data.size // channels) * channels
            data = data[:usable].reshape(-1, channels).mean(axis=1)
        if src_rate != VPIO_SAMPLE_RATE:
            if self._out_resampler_src_rate != src_rate:
                self._out_resampler.configure(src_rate, VPIO_SAMPLE_RATE)
                self._out_resampler_src_rate = src_rate
            data = self._out_resampler.process(data)
        if data.size == 0:
            return
        pcm = np.clip(data * 32767.0, -32768, 32767).astype(np.int16).tobytes()
        if not pcm:
            return

        await t._send_audio(pcm)
        secs = (len(pcm) // 2) / VPIO_SAMPLE_RATE
        self._total_out_secs += secs
        self._wall_at_last_audio = time.monotonic()
        self._state = AudioPlaybackState.PLAYING
        if self._first_audio_at is None:
            self._first_audio_at = time.monotonic()
            if t._ref_buf is not None:
                t._ref_buf.set_active(True)
            self._emit("tts_started", {"source": "macos_vpio", "playback_id": t._playback_id})

    def _schedule_finish(self):
        if self._finish_task is not None and not self._finish_task.done():
            self._finish_task.cancel()
        self._finish_task = asyncio.create_task(self._wait_and_finish())

    async def _wait_and_finish(self):
        try:
            # Wait until the helper's actual playback ring buffer drains.
            deadline = time.monotonic() + max(1.0, self._remaining_playback_secs() + 2.0)
            while time.monotonic() < deadline:
                rem = self._remaining_playback_secs()
                if rem <= 0.03:
                    break
                await asyncio.sleep(min(rem, 0.05))
            if self._transport._ref_buf is not None:
                self._transport._ref_buf.set_active(False)
            self._first_audio_at = None
            self._total_out_secs = 0.0
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


class MacOsVpioAudioTransport:
    """Pipecat transport backed by Apple CoreAudio VoiceProcessingIO."""

    def __init__(
        self,
        *,
        input_device_name: Optional[str] = None,
        output_device_name: Optional[str] = None,
        pipeline_sample_rate: int = 16000,
        output_sample_rate: int = 44100,
        vad_enabled: bool = True,
        vad_analyzer=None,
        event_queue=None,
        aec_reference_buffer=None,
        helper_binary: Optional[str] = None,
    ):
        self._helper_bin = helper_binary or ensure_vpio_helper_binary()
        self._input_device_name = (input_device_name or "").strip()
        self._output_device_name = (output_device_name or "").strip()
        self._pipeline_sample_rate = int(pipeline_sample_rate)
        self._output_sample_rate = int(output_sample_rate)
        self._vad_analyzer = vad_analyzer if vad_analyzer is not None else (
            SileroVADAnalyzer() if vad_enabled else None
        )
        self._event_queue = event_queue
        self._ref_buf = aec_reference_buffer
        self._playback_id: Optional[str] = None
        self._aec_metrics = VpioAecMetrics()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._start_lock = asyncio.Lock()
        self._closing = False
        self._buffered_ms = 0.0
        self._last_stats_at = 0.0
        self._input = MacOsVpioInputTransport(self)
        self._output = MacOsVpioOutputTransport(self)
        self._output._input_transport = self._input

    @property
    def aec_metrics(self) -> VpioAecMetrics:
        return self._aec_metrics

    async def _ensure_helper_started(self):
        async with self._start_lock:
            if self._proc is not None and self._proc.returncode is None:
                return
            await self._spawn_helper_locked()

    async def _restart_helper(self):
        async with self._start_lock:
            await self._terminate_helper_locked()
            if not self._closing:
                await self._spawn_helper_locked()

    async def _spawn_helper_locked(self):
        args = [self._helper_bin]
        if self._input_device_name and self._input_device_name.lower() != "system default":
            args.extend(["--input-device", self._input_device_name])
        if self._output_device_name and self._output_device_name.lower() != "system default":
            args.extend(["--output-device", self._output_device_name])
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info(
            "MacOS VPIO helper started (pid=%s, in=%r, out=%r)",
            self._proc.pid,
            self._input_device_name or "System Default",
            self._output_device_name or "System Default",
        )
        self._send_volume_sync(self._output._volume)

    async def _terminate_helper_locked(self):
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None and proc.returncode is None:
                proc.stdin.write(struct.pack("<BI", _MSG_QUIT, 0))
                await proc.stdin.drain()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except Exception:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _write_packet_sync(self, msg_type: int, payload: bytes = b"") -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            return
        try:
            proc.stdin.write(struct.pack("<BI", msg_type, len(payload)) + payload)
        except Exception:
            pass

    async def _send_audio(self, pcm: bytes) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            return
        try:
            proc.stdin.write(struct.pack("<BI", _MSG_AUDIO, len(pcm)) + pcm)
            await proc.stdin.drain()
        except Exception as exc:
            logger.debug("MacOS VPIO write failed: %s", exc)

    def _send_flush_sync(self) -> None:
        self._write_packet_sync(_MSG_FLUSH)

    def _send_volume_sync(self, volume: float) -> None:
        self._write_packet_sync(_MSG_VOL, struct.pack("<f", float(volume)))

    def new_playback_id(self) -> str:
        import uuid

        self._playback_id = str(uuid.uuid4())
        return self._playback_id

    async def cleanup(self):
        self._closing = True
        await self._input._stop()
        if self._output._finish_task is not None and not self._output._finish_task.done():
            self._output._finish_task.cancel()
        async with self._start_lock:
            await self._terminate_helper_locked()

    def input(self):
        return self._input

    def output(self):
        return self._output
