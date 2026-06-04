import asyncio
import logging
from typing import Optional
import numpy as np
from math import gcd

logger = logging.getLogger(__name__)
import threading
import uuid
import time
from distr.core.audio.time_stretcher import TimeStretcher
from .libs import (
    LocalAudioTransport, LocalAudioInputTransport, LocalAudioOutputTransport, LocalAudioTransportParams,
    AudioRawFrame, InputAudioRawFrame, EndFrame, Frame, TTSStoppedFrame, LLMFullResponseEndFrame, OutputAudioRawFrame,
    TTSStartedFrame, InterruptionFrame,
    librosa, LIBROSA_AVAILABLE,
    pyaudio, PYAUDIO_AVAILABLE
)
from enum import IntEnum, auto

class AudioPlaybackState(IntEnum):
    IDLE = auto()           # No active session
    SYNTHESIZING = auto()   # TTS started, waiting for audio
    PLAYING = auto()        # Receiving and playing audio frames
    DRAINING = auto()       # TTS finished, waiting for buffer/hardware to finish
    COMPLETED = auto()      # Playback finished, safe to close


def _normalize_output_device_name(device_name: Optional[str]) -> str:
    if not device_name:
        return "System Default"
    text = str(device_name).strip()
    if not text or text.lower() in {"system default", "system_default", "default"}:
        return "System Default"
    return text


class HotSwappableLocalAudioInputTransport(LocalAudioInputTransport):
    """Local audio input transport that supports hot-swapping devices."""

    def __init__(self, py_audio, params):
        super().__init__(py_audio, params)
        self._input_callback_count = 0
        self._input_callback_bytes = 0
        self._input_callback_errors = 0
        self._input_last_callback_at = 0.0
        self._input_last_callback_peak = 0

    def _ensure_audio_task_ready(self):
        """Ensure Pipecat's downstream audio queue exists after an idle resume."""
        self._paused = False
        task = getattr(self, "_audio_task", None)
        if task is not None and getattr(task, "done", lambda: False)():
            try:
                exc = task.exception()
            except Exception as exc:
                logger.warning("Audio input task ended unexpectedly and will be recreated: %s", exc)
            else:
                if exc:
                    logger.warning("Audio input task failed and will be recreated: %s", exc)
                else:
                    logger.info("Audio input task ended and will be recreated")
            self._audio_task = None

        if getattr(self._params, "audio_in_enabled", True) and getattr(self, "_audio_task", None) is None:
            self._create_audio_task()

    def _open_input_stream(self):
        """Open and start the PortAudio input stream if it is not already active."""
        if self._in_stream:
            try:
                if self._in_stream.is_active():
                    return
            except Exception:
                pass

        if not self._sample_rate:
            self._sample_rate = getattr(self._params, "audio_in_sample_rate", None) or 16000
        num_frames = int(self._sample_rate / 100) * 2  # 20ms of audio
        logger.info(
            "Opening audio input stream: device=%s rate=%s channels=%s frames=%s",
            self._params.input_device_index,
            self._sample_rate,
            self._params.audio_in_channels,
            num_frames,
        )
        self._in_stream = self._py_audio.open(
            format=self._py_audio.get_format_from_width(2),
            channels=self._params.audio_in_channels,
            rate=self._sample_rate,
            frames_per_buffer=num_frames,
            stream_callback=self._audio_in_callback,
            input=True,
            input_device_index=self._params.input_device_index,
        )
        self._in_stream.start_stream()

    def _audio_in_callback(self, in_data, frame_count, time_info, status):
        """PortAudio callback with health logging around Pipecat enqueue."""
        self._input_callback_count += 1
        self._input_callback_bytes += len(in_data or b"")
        self._input_last_callback_at = time.time()
        if status:
            logger.warning("Audio input callback status: %s", status)

        if in_data:
            try:
                audio_data = np.frombuffer(in_data, dtype=np.int16)
                self._input_last_callback_peak = int(np.max(np.abs(audio_data))) if audio_data.size else 0
            except Exception:
                self._input_last_callback_peak = 0

        try:
            frame = InputAudioRawFrame(
                audio=in_data,
                sample_rate=self._sample_rate,
                num_channels=self._params.audio_in_channels,
            )
            future = asyncio.run_coroutine_threadsafe(
                self.push_audio_frame(frame),
                self.get_event_loop(),
            )

            def _log_enqueue_failure(done_future):
                try:
                    done_future.result()
                except Exception as exc:
                    self._input_callback_errors += 1
                    logger.error("Audio input callback could not enqueue frame: %s", exc, exc_info=True)

            future.add_done_callback(_log_enqueue_failure)
        except Exception as exc:
            self._input_callback_errors += 1
            logger.error("Audio input callback failed before enqueue: %s", exc, exc_info=True)

        return (None, pyaudio.paContinue)

    def _close_input_stream(self):
        """Close the PortAudio input stream if it is open."""
        old_stream = self._in_stream
        self._in_stream = None
        if old_stream:
            try:
                if old_stream.is_active():
                    old_stream.stop_stream()
                old_stream.close()
            except Exception as e:
                logger.error(f"Error closing input stream: {e}")

    def pause_idle_input(self):
        """Stop forwarding mic frames while keeping CoreAudio warm for instant capture."""
        self._params.audio_in_enabled = False
        logger.info("Audio input forwarding paused while idle: %s", self.get_input_health())

    def resume_input(self):
        """Ensure the mic/CoreAudio input stream is ready for capture."""
        self._params.audio_in_enabled = True
        try:
            self._ensure_audio_task_ready()
            self._open_input_stream()
            logger.info("Audio input stream resumed: %s", self.get_input_health())
        except Exception as e:
            logger.error(f"Error opening input stream: {e}")
            self._in_stream = None

    def get_input_health(self):
        stream_active = False
        if self._in_stream:
            try:
                stream_active = bool(self._in_stream.is_active())
            except Exception:
                stream_active = False
        return {
            "enabled": bool(getattr(self._params, "audio_in_enabled", False)),
            "stream_active": stream_active,
            "sample_rate": self._sample_rate,
            "device_index": self._params.input_device_index,
            "callbacks": self._input_callback_count,
            "bytes": self._input_callback_bytes,
            "last_peak": self._input_last_callback_peak,
            "callback_errors": self._input_callback_errors,
            "audio_task_alive": bool(getattr(self, "_audio_task", None)),
        }

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

    def set_device(self, device_index: int):
        """Switch the input device on the fly."""
        if self._params.input_device_index == device_index:
            return

        logger.info(f"Switching input device to index {device_index}")
        self._params.input_device_index = device_index

        # 1. Stop using the current stream immediately
        self._close_input_stream()

        # Re-open the stream with the new device
        try:
            if getattr(self._params, "audio_in_enabled", True):
                self._ensure_audio_task_ready()
                self._open_input_stream()
                logger.info(f"Input device switched successfully to index {device_index}")
            else:
                logger.info(f"Input device updated to index {device_index}; stream remains paused")
        except Exception as e:
            logger.error(f"Error opening new input stream: {e}")
            self._in_stream = None

class HotSwappableLocalAudioOutputTransport(LocalAudioOutputTransport):
    """Local audio output transport that supports hot-swapping devices."""
    
    _librosa_available = None
    
    @classmethod
    def _check_librosa(cls):
        """Check if librosa is available and working."""
        if cls._librosa_available is None:
            try:
                if librosa is None:
                    raise ImportError("librosa not imported")
                test_audio = np.array([0.0, 0.1, 0.2, 0.1, 0.0], dtype=np.float32)
                librosa.effects.time_stretch(test_audio, rate=1.0)
                cls._librosa_available = True
            except Exception:
                cls._librosa_available = False
        return cls._librosa_available

    def __init__(self, py_audio, params, event_queue=None, aec_reference_buffer=None, output_device_name=None):
        super().__init__(py_audio, params)
        self.event_queue = event_queue
        self._volume = 1.0
        self._speed = 1.0
        self._output_device_name = _normalize_output_device_name(output_device_name)
        self._resolved_output_device_index = getattr(params, 'output_device_index', None)
        
        # AEC reference buffer — output audio is pushed here so the input
        # filter can subtract it from the mic signal.
        self._aec_ref_buf = aec_reference_buffer
        
        # Reference to input transport — set by HotSwappableLocalAudioTransport
        # after both transports are created. Used to toggle _allow_interruptions
        # during TTS playback so the echo gate in STT is the sole decision-maker.
        self._input_transport = None
        
        self._original_sample_rate = getattr(params, 'audio_out_sample_rate', None) or getattr(params, 'output_sample_rate', 24000)
        
        # Initialize TimeStretcher
        self._time_stretcher = TimeStretcher(sample_rate=self._original_sample_rate)
        
        # Playback tracking
        self._total_output_bytes = 0
        self._total_audio_duration = 0.0
        self._tts_session_start_time = None
        self._last_audio_frame_time = None
        self._pending_playback_finished_task = None
        self._playback_task_id = 0
        self._software_audio_start_time = None
        self._stream_start_time = None
        self._hardware_check_disabled = False
        self._last_burst_output_bytes = 0  # bytes queued since last TTSStartedFrame
        self._burst_needs_reset = False  # reset burst counter on next audio frame
        self._tts_started_event_emitted = False
        # Playback watermark: the wall-clock time at which all currently-queued
        # audio will have finished playing.  Accounts for generation gaps
        # (silence between sentences while Kokoro generates the next one).
        self._playback_watermark = 0.0
        
        # Interruption state — on interrupt we drop all audio frames until the
        # next response starts.
        self._pipeline_cut = False
        self._force_silence = False  # When True, write silence instead of audio
        
        # Stream health tracking — detect dead PortAudio streams and recover
        self._stream_error_count = 0
        self._stream_error_logged = False  # Only log the first error per failure burst
        
        # State machine
        self._state = AudioPlaybackState.IDLE
        self._playback_monitor_task = None

    def _get_default_output_device_index(self) -> Optional[int]:
        try:
            info = self._py_audio.get_default_output_device_info()
            return int(info.get("index")) if info is not None else None
        except Exception as exc:
            logger.warning("Transport: Could not resolve system default output device: %s", exc)
            return None

    def _iter_output_devices(self):
        try:
            count = self._py_audio.get_device_count()
        except Exception as exc:
            logger.warning("Transport: Could not enumerate output devices: %s", exc)
            return
        for index in range(count):
            try:
                info = self._py_audio.get_device_info_by_index(index)
            except Exception:
                continue
            if int(info.get("maxOutputChannels") or 0) > 0:
                yield int(info.get("index", index)), str(info.get("name") or ""), info

    def _resolve_configured_output_device_index(self) -> Optional[int]:
        configured_name = _normalize_output_device_name(self._output_device_name)
        if configured_name == "System Default":
            return self._get_default_output_device_index()

        wanted = configured_name.lower()
        output_devices = list(self._iter_output_devices())
        for index, name, _info in output_devices:
            if name == configured_name:
                return index
        for index, name, _info in output_devices:
            if name.strip().lower() == wanted:
                return index
        for index, name, _info in output_devices:
            lname = name.strip().lower()
            if wanted in lname or lname in wanted:
                logger.info(
                    "Transport: Output device match (substring): requested '%s' -> using '%s' (index %d)",
                    configured_name,
                    name,
                    index,
                )
                return index

        logger.warning(
            "Transport: Configured output device '%s' is unavailable; falling back to system default",
            configured_name,
        )
        return self._get_default_output_device_index()

    def _describe_output_device(self, device_index: Optional[int]) -> str:
        if device_index is None:
            return "System Default"
        try:
            info = self._py_audio.get_device_info_by_index(device_index)
            return str(info.get("name") or f"index {device_index}")
        except Exception:
            return f"index {device_index}"

    def _output_stream_is_active(self) -> bool:
        if not self._out_stream:
            return False
        try:
            return bool(self._out_stream.is_active())
        except Exception:
            return False

    def _ensure_output_stream_for_configured_device(self, *, reason: str) -> None:
        target_index = self._resolve_configured_output_device_index()
        current_index = self._resolved_output_device_index
        if (
            self._output_stream_is_active()
            and current_index == target_index
            and self._params.output_device_index == target_index
        ):
            return

        logger.info(
            "Transport: Refreshing output stream for %s: configured='%s' current=%s target=%s (%s)",
            reason,
            self._output_device_name,
            current_index,
            target_index,
            self._describe_output_device(target_index),
        )
        self._params.output_device_index = target_index
        self._reopen_output_stream(target_index)

    async def _ensure_output_stream_ready_async(self, *, reason: str):
        if hasattr(self, '_executor'):
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self._executor,
                lambda: self._ensure_output_stream_for_configured_device(reason=reason),
            )
            return
        self._ensure_output_stream_for_configured_device(reason=reason)

    def set_base_vad_confidence(self, confidence: float):
        """No-op — echo gate in STT service handles suppression."""
        pass

    def set_volume(self, volume: float):
        """Set volume multiplier (0.0 to 1.0)."""
        self._volume = max(0.0, min(1.0, volume))
        logger.debug(f"Transport output volume set to {self._volume:.2f}")
    
    def _map_speed(self, ui_speed: float) -> float:
        """Map UI speed (0.5-2.0) to narrower internal effective speed (0.8-1.4)."""
        return 1.0 + (ui_speed - 1.0) * 0.4

    def _effective_playback_speed(self) -> float:
        return self._map_speed(self._speed)

    def _uses_time_stretch(self) -> bool:
        return abs(self._effective_playback_speed() - 1.0) >= 0.01

    async def _process_playback_audio(self, audio_data: np.ndarray) -> np.ndarray:
        """Apply time-stretch only when playback speed is not 1.0x (avoids block-edge crackle)."""
        if len(audio_data) == 0:
            return audio_data
        if not self._uses_time_stretch():
            return audio_data
        return await self._time_stretcher.async_process(audio_data, self._effective_playback_speed())

    async def _flush_playback_tail(self) -> np.ndarray:
        if not self._uses_time_stretch():
            return np.array([], dtype=np.float32)
        return await self._time_stretcher.async_flush()

    def set_speed(self, speed: float):
        """Set playback speed multiplier (0.5 to 2.0)."""
        self._speed = max(0.5, min(2.0, speed))
        mapped_speed = self._map_speed(self._speed)
        logger.debug(f"Transport output speed set to {self._speed:.2f}x (Mapped: {mapped_speed:.2f}x)")

    def _ensure_frame_attributes(self, frame):
        """Ensure frame has required attributes for BaseOutputTransport."""
        if not hasattr(frame, 'transport_destination'):
            frame.transport_destination = None
        if not hasattr(frame, 'pts'):
            frame.pts = None
        if not hasattr(frame, 'id'):
            frame.id = str(uuid.uuid4())

    @staticmethod
    def _resample_float_audio(audio_data: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
        """Resample mono float32 audio while preserving duration."""
        if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate or len(audio_data) == 0:
            return audio_data.astype(np.float32, copy=False)

        try:
            from scipy.signal import resample_poly

            divisor = gcd(source_rate, target_rate)
            up = target_rate // divisor
            down = source_rate // divisor
            return resample_poly(audio_data, up, down).astype(np.float32, copy=False)
        except Exception as exc:
            logger.warning(
                "Transport: scipy resample failed (%s); using linear fallback for %dHz -> %dHz",
                exc,
                source_rate,
                target_rate,
            )
            target_len = max(1, int(round(len(audio_data) * target_rate / source_rate)))
            old_x = np.linspace(0.0, 1.0, num=len(audio_data), endpoint=False)
            new_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
            return np.interp(new_x, old_x, audio_data).astype(np.float32)

    @classmethod
    def _decode_pcm16_mono(
        cls,
        audio: bytes,
        source_rate: int,
        source_channels: int,
        target_rate: int,
    ) -> np.ndarray:
        """Decode a PCM16 frame to mono float32 at the output transport rate."""
        audio_data = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        channels = max(1, int(source_channels or 1))
        if channels > 1:
            usable = (len(audio_data) // channels) * channels
            audio_data = audio_data[:usable].reshape(-1, channels).mean(axis=1)
        return cls._resample_float_audio(audio_data, int(source_rate or target_rate), target_rate)

    async def write_audio_frame(self, frame) -> bool:
        """Override to write silence when interrupted and recover dead streams."""
        if self._force_silence:
            # Replace audio with silence so the OS buffer drains quietly
            frame.audio = b'\x00' * len(frame.audio)
        if not self._output_stream_is_active():
            await self._ensure_output_stream_ready_async(reason="inactive stream before audio write")
        try:
            result = await super().write_audio_frame(frame)
            # Successful write — reset error tracking
            if self._stream_error_count > 0:
                logger.info("Transport: Audio stream recovered after %d errors", self._stream_error_count)
                self._stream_error_count = 0
                self._stream_error_logged = False
            return result
        except Exception as e:
            self._stream_error_count += 1
            if not self._stream_error_logged:
                logger.error("Transport: Audio write failed: %s — attempting stream recovery", e)
                self._stream_error_logged = True
            # After 3 consecutive errors, try to recreate the stream
            if self._stream_error_count == 3:
                await self._recover_output_stream()
            # After 50 errors, stop trying — something is fundamentally broken
            elif self._stream_error_count > 50:
                if self._stream_error_count == 51:
                    logger.error("Transport: 50+ consecutive audio errors — giving up until next session")
            return False

    async def _recover_output_stream(self):
        """Recreate the PortAudio output stream after a fatal error."""
        logger.warning("Transport: Attempting to recreate output stream")
        self._params.output_device_index = self._resolve_configured_output_device_index()
        old_stream = self._out_stream
        self._out_stream = None
        if old_stream:
            try:
                old_stream.close()
            except Exception:
                pass
        try:
            self._out_stream = self._py_audio.open(
                format=self._py_audio.get_format_from_width(2),
                channels=self._params.audio_out_channels,
                rate=self._sample_rate,
                output=True,
                output_device_index=self._params.output_device_index,
            )
            self._out_stream.start_stream()
            self._resolved_output_device_index = self._params.output_device_index
            logger.info(
                "Transport: Output stream recreated successfully on %s",
                self._describe_output_device(self._resolved_output_device_index),
            )
            self._stream_error_count = 0
            self._stream_error_logged = False
        except Exception as e:
            logger.error("Transport: Failed to recreate output stream: %s", e)

    async def _transition_to(self, new_state: AudioPlaybackState):
        """Handle state transitions and side effects."""
        if self._state == new_state:
            return
            
        old_state = self._state
        self._state = new_state
        logger.debug(f"Transport: {old_state.name} -> {new_state.name}")
        
        if new_state == AudioPlaybackState.IDLE:
            if self._playback_monitor_task and not self._playback_monitor_task.done():
                self._playback_monitor_task.cancel()
                self._playback_monitor_task = None
            self._total_output_bytes = 0
            self._total_audio_duration = 0.0
            self._tts_session_start_time = None
            self._software_audio_start_time = None
            self._stream_start_time = None
            self._playback_watermark = 0.0
            self._tts_started_event_emitted = False
            
        elif new_state == AudioPlaybackState.SYNTHESIZING:
            if self._playback_monitor_task and not self._playback_monitor_task.done():
                self._playback_monitor_task.cancel()
                self._playback_monitor_task = None
            # Always clear pipeline cut — new TTS session means new response.
            self._pipeline_cut = False
            # Always reset trackers on new TTS session — even if the previous
            # response was still DRAINING.  Stale counters cause the duration
            # to accumulate across responses ("stacking").
            self._total_output_bytes = 0
            self._total_audio_duration = 0.0
            self._tts_session_start_time = time.time()
            self._software_audio_start_time = None
            self._stream_start_time = None
            self._playback_watermark = 0.0
            self._tts_started_event_emitted = False
            
        elif new_state == AudioPlaybackState.DRAINING:
            if self._playback_monitor_task and not self._playback_monitor_task.done():
                self._playback_monitor_task.cancel()
            loop = asyncio.get_event_loop()
            self._playback_monitor_task = loop.create_task(self._wait_for_playback_complete())

    # Hardware buffer drain margin.  PyAudio's blocking write() returns once
    # the data is accepted into the OS/driver buffer (often ~100–250 ms).
    # Keep a small tail so AEC/mic does not open on speaker echo; avoid long
    # “dead air” after TTS ends (player + idle UX).
    _PLAYBACK_BUFFER_MARGIN = 0.35
    # Floor only when the watermark is already in the past (no queued audio).
    _PLAYBACK_MIN_WAIT = 0.22

    async def _wait_for_playback_complete(self):
        """Wait for playback to finish using the playback watermark.
        
        The watermark tracks the wall-clock time at which all queued audio
        will have finished playing, correctly accounting for generation gaps
        (silence between sentences while Kokoro synthesises the next one).
        """
        task_id = getattr(self, '_playback_task_id', 0) + 1
        self._playback_task_id = task_id

        try:
            now = time.time()
            remaining = self._playback_watermark - now
            # Wait for queued audio + driver buffer; do not add a second large
            # floor on top of a positive remaining (that doubled tail latency).
            sleep_secs = remaining + self._PLAYBACK_BUFFER_MARGIN
            if sleep_secs < self._PLAYBACK_MIN_WAIT:
                sleep_secs = self._PLAYBACK_MIN_WAIT

            logger.debug(
                f"Transport: [Complete {task_id}] output_bytes={self._total_output_bytes}, "
                f"total_audio={self._total_audio_duration:.3f}s, "
                f"watermark_remaining={remaining:.3f}s, "
                f"sleep={sleep_secs:.3f}s"
            )
            await asyncio.sleep(sleep_secs)

            # NOW deactivate AEC — the speaker hardware has finished draining.
            # Before this point, echo from the tail-end of TTS playback could
            # leak into the mic and trigger false VAD → STT transcription.
            if self._aec_ref_buf is not None:
                self._aec_ref_buf.set_active(False)

            await self._transition_to(AudioPlaybackState.COMPLETED)
            if self.event_queue:
                self.event_queue.put(('playback_finished', {}), block=False)
            logger.debug(f"Transport: [Complete {task_id}] Emitted playback_finished")

        except asyncio.CancelledError:
            logger.debug(f"Transport: [Complete {task_id}] Cancelled")
            # AEC deactivation handled by InterruptionFrame handler
        except Exception as e:
            logger.error(f"Transport: [Complete {task_id}] Error: {e}", exc_info=True)
            # Deactivate AEC on error to avoid stuck state
            if self._aec_ref_buf is not None:
                self._aec_ref_buf.set_active(False)
            if self.event_queue:
                self.event_queue.put(('playback_finished', {}), block=False)

    async def process_frame(self, frame, direction):
        self._ensure_frame_attributes(frame)
        
        # ── TTSStartedFrame ─────────────────────────────────────────────
        if isinstance(frame, TTSStartedFrame):
            logger.debug(
                "Transport: TTSStartedFrame state=%s pipeline_cut=%s",
                self._state.name, self._pipeline_cut,
            )
            await self._ensure_output_stream_ready_async(reason="TTS session start")
            # Signal AEC that speaker is active
            if self._aec_ref_buf is not None:
                self._aec_ref_buf.set_active(True)
            # Suppress Pipecat's automatic VAD→InterruptionFrame during TTS.
            # The echo gate in STT decides barge-in via energy check.
            if self._input_transport is not None:
                self._input_transport._allow_interruptions = False

            # New response starting — clear any leftover interrupt state
            self._pipeline_cut = False
            self._force_silence = False
            self._stream_error_count = 0
            self._stream_error_logged = False

            # Only reset session counters for a genuinely NEW response
            # (IDLE/COMPLETED/DRAINING).  Kokoro yields TTSStartedFrame
            # per-sentence, so mid-response (SYNTHESIZING/PLAYING) we must
            # NOT wipe the accumulated counters — that would make
            # _wait_for_playback_complete think only the last sentence
            # exists, causing premature player close.
            if self._state in (AudioPlaybackState.IDLE, AudioPlaybackState.COMPLETED, AudioPlaybackState.DRAINING):
                await self._transition_to(AudioPlaybackState.SYNTHESIZING)
            else:
                # Mid-response sentence boundary — just reset the burst
                # counter so it tracks only this sentence's bytes.
                logger.debug("Transport: TTSStartedFrame mid-response (state=%s) — keeping session counters", self._state.name)

            # Reset per-burst byte counter so we can measure the last sentence
            self._last_burst_output_bytes = 0
            self._burst_needs_reset = False
        
        # ── InterruptionFrame ───────────────────────────────────────────
        elif isinstance(frame, InterruptionFrame):
            logger.debug("Transport: Interrupted")
            # Signal AEC that speaker stopped
            if self._aec_ref_buf is not None:
                self._aec_ref_buf.set_active(False)
            # Restore Pipecat's automatic interruptions
            if self._input_transport is not None:
                self._input_transport._allow_interruptions = True
            
            # Cancel pending playback_finished
            if self._pending_playback_finished_task and not self._pending_playback_finished_task.done():
                self._pending_playback_finished_task.cancel()
                self._pending_playback_finished_task = None

            # Flush TimeStretcher (discard buffered audio)
            try:
                await self._flush_playback_tail()
            except Exception as e:
                logger.error(f"Transport: Error flushing TimeStretcher: {e}")

            # Drop all audio frames until the next response starts.
            self._pipeline_cut = True
            self._force_silence = True
            await self._transition_to(AudioPlaybackState.IDLE)

            # Abort the PyAudio output stream to kill any buffered audio
            # that's still playing in the OS/driver buffer.
            # IMPORTANT: Don't call stop_stream()/start_stream() directly from
            # the event loop — the executor thread may be mid-write, and
            # PortAudio doesn't handle concurrent stop+write gracefully (causes
            # Internal PortAudio error -9986 which kills the stream permanently).
            # Instead, schedule the abort on the executor so it runs AFTER any
            # pending write completes.
            self._stream_error_count = 0
            self._stream_error_logged = False
            if self._out_stream and hasattr(self, '_executor'):
                def _safe_abort():
                    try:
                        if self._out_stream and self._out_stream.is_active():
                            self._out_stream.stop_stream()
                            self._out_stream.start_stream()
                    except Exception as e:
                        logger.warning("Transport: Stream abort failed: %s (will recover on next write)", e)
                try:
                    loop = asyncio.get_event_loop()
                    loop.run_in_executor(self._executor, _safe_abort)
                except Exception:
                    pass

            # CRITICAL: Pass InterruptionFrame to Pipecat's base transport so it
            # clears its internal frame queue (_start_interruption → __reset_process_queue).
            # Without this, queued audio frames from the old response survive and
            # play on top of the next response ("stacking" / overlapping audio).
            await super().process_frame(frame, direction)
        
        # ── TTSStoppedFrame / EndFrame / LLMFullResponseEndFrame ────────
        elif isinstance(frame, (TTSStoppedFrame, EndFrame, LLMFullResponseEndFrame)):
            # On full-response end, restore auto-interruptions but keep AEC
            # reference buffer active until playback actually finishes.
            # The speaker hardware buffer still has audio draining — if we
            # deactivate AEC now, the echo gate won't engage and the mic
            # picks up the tail-end echo as "user speech" (the agent
            # transcribes its own TTS output).
            if isinstance(frame, (EndFrame, LLMFullResponseEndFrame)):
                # DON'T deactivate AEC here — do it in _wait_for_playback_complete
                # after the hardware has finished draining.
                if self._input_transport is not None:
                    self._input_transport._allow_interruptions = True

        # ── Flush audio buffer on TTS lifecycle frames ──────────────────
        if isinstance(frame, (TTSStoppedFrame, EndFrame, LLMFullResponseEndFrame)):
            frame_type = type(frame).__name__
            
            if isinstance(frame, TTSStoppedFrame):
                # Per-sentence — flush buffer, consume frame (don't pass through)
                remaining_audio = await self._flush_playback_tail()
                if len(remaining_audio) > 0:
                    tail_duration = len(remaining_audio) / self._original_sample_rate
                    if self._volume != 1.0:
                        remaining_audio = remaining_audio * self._volume
                    remaining_audio = np.clip(remaining_audio, -1.0, 1.0)
                    output_bytes = (remaining_audio * 32767.0).astype(np.int16).tobytes()
                    self._total_output_bytes += len(output_bytes)
                    self._last_burst_output_bytes += len(output_bytes)
                    self._total_audio_duration += tail_duration
                    # Advance watermark for flushed tail audio
                    now_wm = time.time()
                    if self._playback_watermark < now_wm:
                        self._playback_watermark = now_wm + tail_duration
                    else:
                        self._playback_watermark += tail_duration
                    tail_frame = AudioRawFrame(
                        audio=output_bytes,
                        sample_rate=self._original_sample_rate,
                        num_channels=1
                    )
                    tail_frame.id = str(uuid.uuid4())
                    tail_frame.transport_destination = getattr(frame, 'transport_destination', None)
                    tail_frame.pts = getattr(frame, 'pts', None)
                    await super().process_frame(tail_frame, direction)
                # Mark that a sentence boundary passed — the burst counter
                # will be reset when the *next* sentence's first audio frame
                # arrives.  We can't reset here because _wait_for_playback_complete
                # needs the last sentence's byte count, and TTSStoppedFrame fires
                # AFTER that sentence's audio frames.
                self._burst_needs_reset = True
                return  # Consume TTSStoppedFrame
            else:
                # End of full response
                logger.debug(
                    "Transport: %s - vol=%.2f, pipeline_cut=%s, total_audio=%.3f, state=%s",
                    frame_type, self._volume, self._pipeline_cut,
                    self._total_audio_duration, self._state.name,
                )
            
            # Flush remaining audio
            remaining_audio = await self._flush_playback_tail()
            if len(remaining_audio) > 0:
                tail_duration = len(remaining_audio) / self._original_sample_rate
                if self._volume != 1.0:
                    remaining_audio = remaining_audio * self._volume
                remaining_audio = np.clip(remaining_audio, -1.0, 1.0)
                output_bytes = (remaining_audio * 32767.0).astype(np.int16).tobytes()
                self._total_output_bytes += len(output_bytes)
                self._last_burst_output_bytes += len(output_bytes)
                self._total_audio_duration += tail_duration
                # Advance watermark for flushed tail audio
                now_wm = time.time()
                if self._playback_watermark < now_wm:
                    self._playback_watermark = now_wm + tail_duration
                else:
                    self._playback_watermark += tail_duration
                tail_frame = AudioRawFrame(
                    audio=output_bytes,
                    sample_rate=self._original_sample_rate,
                    num_channels=1
                )
                tail_frame.id = str(uuid.uuid4())
                tail_frame.transport_destination = getattr(frame, 'transport_destination', None)
                tail_frame.pts = getattr(frame, 'pts', None)
                await super().process_frame(tail_frame, direction)
            
            # Schedule playback_finished on full response end
            if self.event_queue and isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
                if self._total_output_bytes <= 0:
                    # Common when a response is interrupted before any audio is emitted.
                    if self._pipeline_cut or self._state == AudioPlaybackState.IDLE:
                        logger.debug("Transport: No audio produced after interruption, finishing immediately")
                    else:
                        logger.warning("Transport: No audio produced, finishing immediately")
                    if self._aec_ref_buf is not None:
                        self._aec_ref_buf.set_active(False)
                    await self._transition_to(AudioPlaybackState.COMPLETED)
                    self.event_queue.put(('playback_finished', {}), block=False)
                else:
                    await self._transition_to(AudioPlaybackState.DRAINING)

        # ── Audio frames ────────────────────────────────────────────────
        is_audio_frame = (hasattr(frame, 'audio') and isinstance(frame.audio, bytes) and len(frame.audio) > 0 and 
            hasattr(frame, 'sample_rate') and hasattr(frame, 'num_channels'))
        
        # Drop audio while pipeline is cut (interrupted).
        # If TTSStartedFrame was missed, the first audio frame of a new
        # response clears the cut automatically.
        if is_audio_frame and self._pipeline_cut:
            # Check if this looks like a new response (state is IDLE/COMPLETED
            # after interrupt). TTS services sometimes don't push TTSStartedFrame.
            if self._state in (AudioPlaybackState.IDLE, AudioPlaybackState.COMPLETED):
                logger.debug("Transport: First audio after interrupt — treating as new session")
                self._pipeline_cut = False
                self._force_silence = False
                await self._transition_to(AudioPlaybackState.SYNTHESIZING)
                # Fall through to process the frame
            else:
                return  # Still in old response, drop
            
        if is_audio_frame:
            try:
                # If a sentence boundary passed (TTSStoppedFrame), reset the
                # burst counter now — this audio frame is the first of the
                # next sentence.
                if self._burst_needs_reset:
                    self._last_burst_output_bytes = 0
                    self._burst_needs_reset = False

                bytes_per_sample = 2
                channels = max(1, int(getattr(frame, 'num_channels', 1) or 1))
                sample_rate = int(getattr(frame, 'sample_rate', self._original_sample_rate) or self._original_sample_rate)
                audio_data = self._decode_pcm16_mono(
                    frame.audio,
                    sample_rate,
                    channels,
                    self._original_sample_rate,
                )
                
                if len(audio_data) > 0:
                    frame_duration = len(audio_data) / self._original_sample_rate
                    self._total_audio_duration += frame_duration
                    self._last_audio_frame_time = time.time()
                    
                    if self._tts_session_start_time is None:
                        self._tts_session_start_time = time.time()
                        
                    if self._state == AudioPlaybackState.SYNTHESIZING:
                        await self._transition_to(AudioPlaybackState.PLAYING)
                
                processed_audio = await self._process_playback_audio(audio_data)
                
                if len(processed_audio) > 0:
                    if self._volume != 1.0:
                        processed_audio = processed_audio * self._volume
                    
                    processed_audio = np.clip(processed_audio, -1.0, 1.0)
                    output_bytes = (processed_audio * 32767.0).astype(np.int16).tobytes()
                    
                    # Compute actual output duration (after time-stretching)
                    output_duration = len(output_bytes) / (self._original_sample_rate * bytes_per_sample)
                    
                    # Advance playback watermark using output duration.
                    # If the queue was empty (generation gap), start from now.
                    now_wm = time.time()
                    if self._playback_watermark < now_wm:
                        self._playback_watermark = now_wm + output_duration
                    else:
                        self._playback_watermark += output_duration
                    
                    frame.audio = output_bytes
                    frame.sample_rate = self._original_sample_rate
                    frame.num_channels = 1
                    self._total_output_bytes += len(output_bytes)
                    self._last_burst_output_bytes += len(output_bytes)
                    
                    # Feed AEC reference buffer
                    if self._aec_ref_buf is not None:
                        self._aec_ref_buf.push(processed_audio)

                    has_audible_samples = bool(np.max(np.abs(processed_audio)) > 1e-4)
                    if not self._tts_started_event_emitted and self.event_queue and has_audible_samples:
                        try:
                            self.event_queue.put(
                                (
                                    'tts_started',
                                    {
                                        'source': 'transport',
                                        'bytes': len(output_bytes),
                                        'sample_rate': self._original_sample_rate,
                                    },
                                ),
                                block=False,
                            )
                            self._tts_started_event_emitted = True
                            logger.debug(
                                "Transport: emitted confirmed tts_started bytes=%d sample_rate=%s",
                                len(output_bytes),
                                self._original_sample_rate,
                            )
                        except Exception as e:
                            logger.debug("Transport: could not emit confirmed tts_started: %s", e)
                    
                    await super().process_frame(frame, direction)
                    
                    if self._stream_start_time is None:
                        try:
                            if self._out_stream and self._out_stream.is_active():
                                self._stream_start_time = self._out_stream.get_time()
                        except Exception:
                            pass
                    if self._software_audio_start_time is None:
                         self._software_audio_start_time = time.time()

            except Exception as e:
                logger.error(f"Error processing audio frame: {e}")
                await super().process_frame(frame, direction)
        else:
            await super().process_frame(frame, direction)

    def _reopen_output_stream(self, device_index: Optional[int]) -> None:
        """Recreate the PortAudio output stream (must run off the asyncio event loop)."""
        if not self._sample_rate:
            self._sample_rate = (
                getattr(self._params, 'audio_out_sample_rate', None)
                or getattr(self._params, 'output_sample_rate', None)
                or self._original_sample_rate
                or 24000
            )
        old_stream = self._out_stream
        self._out_stream = None
        if old_stream:
            try:
                if old_stream.is_active():
                    old_stream.stop_stream()
                old_stream.close()
            except Exception as e:
                logger.error(f"Error closing output stream: {e}")

        try:
            self._out_stream = self._py_audio.open(
                format=self._py_audio.get_format_from_width(2),
                channels=self._params.audio_out_channels,
                rate=self._sample_rate,
                output=True,
                output_device_index=self._params.output_device_index,
            )
            self._out_stream.start_stream()
            self._stream_error_count = 0
            self._stream_error_logged = False
            self._resolved_output_device_index = device_index
            logger.info(
                "Output device switched successfully to %s",
                self._describe_output_device(device_index),
            )
        except Exception as e:
            logger.error(f"Error opening new output stream: {e}")
            self._out_stream = None

    def set_device(self, device_index: Optional[int], device_name: Optional[str] = None):
        """Switch the output device on the fly."""
        logger.debug(f"set_device called on thread: {threading.current_thread().name}")

        if device_name is not None:
            self._output_device_name = _normalize_output_device_name(device_name)
        elif device_index is None:
            self._output_device_name = "System Default"

        target_index = self._resolve_configured_output_device_index()

        if (
            self._params.output_device_index == target_index
            and self._resolved_output_device_index == target_index
            and self._output_stream_is_active()
        ):
            return

        self._hardware_check_disabled = True

        logger.info(
            "Switching output device: configured='%s' target=%s (%s)",
            self._output_device_name,
            target_index,
            self._describe_output_device(target_index),
        )
        self._params.output_device_index = target_index

        # Reopen on the PortAudio executor so we never close the stream while a
        # write is in flight (causes crackling / PortAudio -9986 errors).
        if hasattr(self, '_executor'):
            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(
                    self._executor,
                    lambda: self._reopen_output_stream(target_index),
                )
                return
            except RuntimeError:
                pass

        self._reopen_output_stream(target_index)


class HotSwappableLocalAudioTransport(LocalAudioTransport):
    """Complete local audio transport with hot-swapping capabilities."""
    
    def __init__(self, params, event_queue=None, aec_reference_buffer=None, output_device_name=None):
        super().__init__(params)
        self.event_queue = event_queue
        self._aec_ref_buf = aec_reference_buffer
        self._output_device_name = _normalize_output_device_name(output_device_name)

    def input(self) -> HotSwappableLocalAudioInputTransport:
        if not self._input:
            self._input = HotSwappableLocalAudioInputTransport(self._pyaudio, self._params)
        return self._input

    def output(self) -> HotSwappableLocalAudioOutputTransport:
        if not self._output:
            self._output = HotSwappableLocalAudioOutputTransport(
                self._pyaudio, self._params,
                event_queue=self.event_queue,
                aec_reference_buffer=self._aec_ref_buf,
                output_device_name=self._output_device_name,
            )
            # Give output transport a reference to input so it can toggle
            # _allow_interruptions during TTS playback.
            self._output._input_transport = self.input()
        return self._output
