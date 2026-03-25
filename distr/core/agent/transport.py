import asyncio
import logging
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)
import threading
import uuid
import time
from distr.core.audio.time_stretcher import TimeStretcher
from .libs import (
    LocalAudioTransport, LocalAudioInputTransport, LocalAudioOutputTransport, LocalAudioTransportParams,
    AudioRawFrame, EndFrame, Frame, TTSStoppedFrame, LLMFullResponseEndFrame, OutputAudioRawFrame,
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

class HotSwappableLocalAudioInputTransport(LocalAudioInputTransport):
    """Local audio input transport that supports hot-swapping devices."""

    def set_device(self, device_index: int):
        """Switch the input device on the fly."""
        if self._params.input_device_index == device_index:
            return

        logger.info(f"Switching input device to index {device_index}")
        self._params.input_device_index = device_index

        # 1. Stop using the current stream immediately
        old_stream = self._in_stream
        self._in_stream = None

        if old_stream:
            try:
                old_stream.stop_stream()
                old_stream.close()
            except Exception as e:
                logger.error(f"Error closing input stream: {e}")
            
        # Re-open the stream with the new device
        try:
            num_frames = int(self._sample_rate / 100) * 2  # 20ms of audio
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
            logger.info(f"Input device switched successfully to index {device_index}")
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

    def __init__(self, py_audio, params, event_queue=None, aec_reference_buffer=None):
        super().__init__(py_audio, params)
        self.event_queue = event_queue
        self._volume = 1.0
        self._speed = 1.0
        
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

    async def write_audio_frame(self, frame) -> bool:
        """Override to write silence when interrupted and recover dead streams."""
        if self._force_silence:
            # Replace audio with silence so the OS buffer drains quietly
            frame.audio = b'\x00' * len(frame.audio)
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
            logger.info("Transport: Output stream recreated successfully")
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
            
        elif new_state == AudioPlaybackState.DRAINING:
            if self._playback_monitor_task and not self._playback_monitor_task.done():
                self._playback_monitor_task.cancel()
            loop = asyncio.get_event_loop()
            self._playback_monitor_task = loop.create_task(self._wait_for_playback_complete())

    # Hardware buffer drain margin.  PyAudio's blocking write() returns once
    # the data is accepted into the OS/driver buffer, which is typically
    # 100-300 ms deep.  0.6 s gives comfortable headroom without the long
    # dead-time the old 3.5 s value caused (user had to wait 3.5 s after
    # audio finished before the system became responsive).
    _PLAYBACK_BUFFER_MARGIN = 0.6
    # Minimum wait even for very short utterances ("Done", "OK").
    _PLAYBACK_MIN_WAIT = 0.8

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
            sleep_secs = max(self._PLAYBACK_MIN_WAIT, remaining + self._PLAYBACK_BUFFER_MARGIN)

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
                await self._time_stretcher.async_flush()
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
                remaining_audio = await self._time_stretcher.async_flush()
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
            remaining_audio = await self._time_stretcher.async_flush()
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

                frame_bytes = len(frame.audio)
                bytes_per_sample = 2
                channels = getattr(frame, 'num_channels', 1)
                sample_rate = getattr(frame, 'sample_rate', self._original_sample_rate)
                
                if sample_rate > 0:
                    frame_duration = frame_bytes / (sample_rate * bytes_per_sample * channels)
                    self._total_audio_duration += frame_duration
                    self._last_audio_frame_time = time.time()
                    
                    if self._tts_session_start_time is None:
                        self._tts_session_start_time = time.time()
                        
                    if self._state == AudioPlaybackState.SYNTHESIZING:
                        await self._transition_to(AudioPlaybackState.PLAYING)
                
                # Convert to float, time-stretch, apply volume, send
                audio_data = np.frombuffer(frame.audio, dtype=np.int16).astype(np.float32) / 32768.0
                effective_speed = self._map_speed(self._speed)
                processed_audio = await self._time_stretcher.async_process(audio_data, effective_speed)
                
                if len(processed_audio) > 0:
                    if self._volume != 1.0:
                        processed_audio = processed_audio * self._volume
                    
                    processed_audio = np.clip(processed_audio, -1.0, 1.0)
                    output_bytes = (processed_audio * 32767.0).astype(np.int16).tobytes()
                    
                    # Compute actual output duration (after time-stretching)
                    output_duration = len(output_bytes) / (self._original_sample_rate * bytes_per_sample * channels)
                    
                    # Advance playback watermark using output duration.
                    # If the queue was empty (generation gap), start from now.
                    now_wm = time.time()
                    if self._playback_watermark < now_wm:
                        self._playback_watermark = now_wm + output_duration
                    else:
                        self._playback_watermark += output_duration
                    
                    frame.audio = output_bytes
                    self._total_output_bytes += len(output_bytes)
                    self._last_burst_output_bytes += len(output_bytes)
                    
                    # Feed AEC reference buffer
                    if self._aec_ref_buf is not None:
                        self._aec_ref_buf.push(processed_audio)
                    
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

    def set_device(self, device_index: int):
        """Switch the output device on the fly."""
        logger.debug(f"set_device called on thread: {threading.current_thread().name}")

        if self._params.output_device_index == device_index:
            return

        self._hardware_check_disabled = True

        logger.info(f"Switching output device to index {device_index}")
        self._params.output_device_index = device_index

        old_stream = self._out_stream
        self._out_stream = None

        if old_stream:
            if hasattr(self, '_executor'):
                try:
                    self._executor.submit(lambda: None).result()
                except Exception as e:
                    logger.warning(f"Error waiting for executor flush: {e}")
            try:
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
            logger.info(f"Output device switched successfully to index {device_index}")
        except Exception as e:
            logger.error(f"Error opening new output stream: {e}")
            self._out_stream = None


class HotSwappableLocalAudioTransport(LocalAudioTransport):
    """Complete local audio transport with hot-swapping capabilities."""
    
    def __init__(self, params, event_queue=None, aec_reference_buffer=None):
        super().__init__(params)
        self.event_queue = event_queue
        self._aec_ref_buf = aec_reference_buffer

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
            )
            # Give output transport a reference to input so it can toggle
            # _allow_interruptions during TTS playback.
            self._output._input_transport = self.input()
        return self._output
