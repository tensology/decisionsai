import asyncio
import logging
import time
import numpy as np
import sys
import os
from contextlib import contextmanager
from typing import Optional

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE,
    AudioRawFrame, InputAudioRawFrame, TranscriptionFrame, ErrorFrame, UserStoppedSpeakingFrame,
    StartFrame, EndFrame, CancelFrame, InterruptionFrame,
    pwc, WHISPER_AVAILABLE
)
from distr.core.agent.services.stt.base import BaseSTTService

try:
    from queue import Full
except ImportError:
    # multiprocessing.Queue doesn't use queue.Full
    Full = Exception

logger = logging.getLogger(__name__)

@contextmanager
def suppress_stderr():
    """Context manager to suppress stderr output from C libraries (whisper.cpp).

    Redirects the actual file descriptor (fd 2) so C-level writes to stderr
    are also captured, not just Python's sys.stderr.
    """
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    old_stderr_fd = os.dup(2)
    os.dup2(devnull_fd, 2)
    old_py_stderr = sys.stderr
    sys.stderr = open(os.devnull, 'w')
    try:
        yield
    finally:
        sys.stderr.close()
        sys.stderr = old_py_stderr
        os.dup2(old_stderr_fd, 2)
        os.close(old_stderr_fd)
        os.close(devnull_fd)


class WhisperSTTService(BaseSTTService):
    """Whisper-based STT service using Pipecat"""

    def __init__(self, model_path: str = "base.en", event_queue=None, is_hands_free=False, **kwargs):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for WhisperSTTService")
        if not WHISPER_AVAILABLE:
            raise ImportError("pywhispercpp is required for WhisperSTTService")

        super().__init__(event_queue=event_queue, is_hands_free=is_hands_free, **kwargs)
        self.model_path = model_path
        self.model = None

        # Suppress whisper C library verbose output during initialization
        try:
            with suppress_stderr():
                logger.debug(f"Initializing Whisper model (Metal GPU auto-detected if available)")
                self.model = pwc.Model(model_path, print_progress=False, redirect_whispercpp_logs_to=os.devnull)
        except Exception as e:
            logger.error(f"Failed to initialize Whisper model: {e}")
            raise RuntimeError(f"Could not initialize Whisper model: {e}") from e

        logger.info(f"WhisperSTTService initialized with model: {model_path}, is_hands_free={is_hands_free}")

    def __del__(self):
        """Cleanup Whisper model resources on deletion"""
        try:
            self.cleanup()
        except Exception as e:
            # Suppress cleanup errors during garbage collection
            logger.debug(f"Error during WhisperSTTService cleanup in __del__: {e}")

    def cleanup(self):
        """Explicitly cleanup Whisper model resources"""
        try:
            if hasattr(self, 'model') and self.model is not None:
                logger.debug("Cleaning up Whisper model resources")
                # Clear the model reference - this should trigger C++ destructor
                # Wrap in try-except to catch Metal cleanup errors
                try:
                    del self.model
                except Exception as e:
                    # Log but don't crash on Metal cleanup errors
                    logger.warning(f"Error during Whisper model cleanup (Metal backend): {e}")
                finally:
                    self.model = None
                logger.debug("Whisper model cleanup complete")
        except Exception as e:
            logger.warning(f"Error during WhisperSTTService cleanup: {e}")

    async def _process_ptt_buffer_immediate(self, direction):
        """Process PTT buffer immediately when PTT is released - processes FULL accumulated audio"""
        chunk_count = len(self._ptt_buffer_accumulator)
        total_bytes = sum(len(chunk) for chunk in self._ptt_buffer_accumulator) if self._ptt_buffer_accumulator else 0
        pre_duration_ms = (total_bytes / (16000 * 2)) * 1000 if total_bytes > 0 else 0
        logger.info(f"STT: Processing PTT buffer ({chunk_count} chunks, ~{pre_duration_ms:.0f}ms)")
        
        if not self._ptt_buffer_accumulator:
            logger.warning("STT: _process_ptt_buffer_immediate() called but buffer is empty")
            return
        
        # Extract ALL accumulated audio chunks
        audio_bytes = b"".join(self._ptt_buffer_accumulator)
        chunk_count = len(self._ptt_buffer_accumulator)
        self._ptt_buffer_accumulator = []  # Clear buffer immediately
        logger.debug(f"STT: Extracted {len(audio_bytes)} bytes from {chunk_count} chunks")
        
        if len(audio_bytes) > 0:
            # Calculate duration and pad if too short (Whisper requires at least 1000ms)
            sample_rate = 16000
            bytes_per_second = sample_rate * 2
            duration_ms = (len(audio_bytes) / bytes_per_second) * 1000
            logger.debug(f"STT: Processing FULL PTT buffer: {duration_ms:.0f}ms ({len(audio_bytes)} bytes)")
            
            if duration_ms < 1000:
                # Pad with silence to reach 1000ms minimum
                silence_needed_ms = 1000 - duration_ms
                silence_bytes = int((silence_needed_ms / 1000) * bytes_per_second)
                silence = b'\x00' * silence_bytes
                audio_bytes = audio_bytes + silence
                logger.debug(f"STT: Padded PTT audio from {duration_ms:.0f}ms to 1000ms")
            
            # Send UserStoppedSpeakingFrame to reset TTS cancelled state
            try:
                stop_frame = UserStoppedSpeakingFrame()
                logger.debug("STT: Sending UserStoppedSpeakingFrame to reset TTS cancelled state")
                await self.push_frame(stop_frame, direction)
            except Exception as e:
                logger.error(f"STT: Error sending UserStoppedSpeakingFrame: {e}")
            
            # Process the FULL transcription
            logger.debug(f"STT: Starting run_stt() for FULL PTT audio ({len(audio_bytes)} bytes)")
            frame_count = 0
            try:
                async for result_frame in self.run_stt(audio_bytes):
                    # Check if cancelled during processing
                    if self._stt_cancelled:
                        logger.debug("STT: Transcription cancelled during processing - stopping")
                        break
                    frame_count += 1
                    logger.debug(f"STT: run_stt() yielded frame #{frame_count}: {type(result_frame).__name__}")
                    if isinstance(result_frame, TranscriptionFrame):
                        logger.info(f"STT: PTT transcription: '{result_frame.text}'")
                    try:
                        await self.push_frame(result_frame, direction)
                        if isinstance(result_frame, TranscriptionFrame):
                            logger.info(f"STT: Pushed TranscriptionFrame to LLM pipeline")
                    except Exception as e:
                        logger.error(f"STT: Error pushing frame: {e}", exc_info=True)
                if frame_count == 0:
                    if not self._is_hands_free:
                        logger.debug("STT: run_stt() yielded no frames in PTT mode (likely silence/artifact)")
                    else:
                        logger.warning("STT: run_stt() did not yield any frames!")
                    # CRITICAL: Even if STT produces no frames, we should still try to send an empty TranscriptionFrame
                    # This allows the LLM to check for "change mode" even if transcription fails
                    # Only do this in PTT mode to allow mode switching
                    if not self._is_hands_free:
                        logger.debug("STT: No transcription frames in PTT mode - sending empty TranscriptionFrame to allow 'change mode' check")
                        try:
                            # Send empty TranscriptionFrame so LLM can check for "change mode" command
                            empty_frame = TranscriptionFrame(text="", user_id="", timestamp=time.time())
                            await self.push_frame(empty_frame, direction)
                            logger.debug("STT: Sent empty TranscriptionFrame for 'change mode' check in PTT mode")
                        except Exception as e:
                            logger.error(f"STT: Error sending empty TranscriptionFrame: {e}", exc_info=True)
                else:
                    logger.debug(f"STT: Finished processing FULL PTT transcription ({frame_count} frames)")
            except Exception as e:
                logger.error(f"STT: Error in run_stt() loop: {e}", exc_info=True)
        else:
            logger.error("STT: audio_bytes is empty after extraction!")
    
    async def run_stt(self, audio: bytes):
        """Process audio bytes and yield transcription frames"""
        # Reset cancellation flag for new transcription
        self._stt_cancelled = False
        
        # Check minimum audio duration (16kHz, 2 bytes per sample)
        sample_rate = 16000
        bytes_per_second = sample_rate * 2
        duration_ms = (len(audio) / bytes_per_second) * 1000
        
        if duration_ms < self._min_audio_duration_ms:
            logger.debug(f"Audio too short ({duration_ms:.0f}ms < {self._min_audio_duration_ms}ms), padding with silence")
            # Pad with silence to reach minimum duration
            silence_needed_ms = self._min_audio_duration_ms - duration_ms
            silence_bytes = int((silence_needed_ms / 1000) * bytes_per_second)
            silence = b'\x00' * silence_bytes
            audio = audio + silence
            logger.debug(f"Padded audio to {self._min_audio_duration_ms}ms")

        # Check if cancelled before starting transcription
        if self._stt_cancelled:
            logger.debug("STT: Transcription cancelled before starting")
            return

        audio_np = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        loop = asyncio.get_running_loop()
        try:
            # Run in executor to avoid blocking the event loop
            # Note: We can't easily cancel executor tasks, but we check cancellation flag after
            segments = await loop.run_in_executor(None, lambda: self.model.transcribe(audio_np))
            
            # Check if cancelled after transcription
            if self._stt_cancelled:
                logger.debug("STT: Transcription cancelled after completion - discarding result")
                return
            
            text = " ".join([seg.text for seg in segments]).strip()
            if text:
                logger.info("[STT] PICKED UP: %s", text)
                logger.debug(f"TRANSCRIPTION [Whisper.cpp {self.model_path}]: '{text}'")
                
                # CRITICAL: In PTT mode, always send transcriptions that contain "change mode" commands
                # This ensures mode switching works even if text would normally be filtered as artifact
                text_lower = text.lower()
                is_change_mode_command = any(
                    pattern in text_lower 
                    for pattern in ["change mode", "switch mode", "toggle mode", "ptt mode", "continuous mode", "hands free mode"]
                )
                
                # CRITICAL: In PTT mode, ALWAYS send transcriptions (don't filter as artifacts)
                # The LLM will handle filtering - we need to send everything so "change mode" can be detected
                if not self._is_hands_free:
                    # PTT mode: send most transcriptions to LLM, but still filter obvious audio artifacts
                    if not is_change_mode_command and text_lower in self._audio_artifacts:
                        logger.debug(f"STT: Rejected audio artifact in PTT mode: '{text}'")
                    else:
                        logger.debug(f"STT: PTT mode - sending transcription to LLM: '{text}'")
                        frame = TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                        yield frame
                        logger.debug(f"STT: TranscriptionFrame yielded successfully (PTT mode)")
                elif is_change_mode_command or self._is_meaningful_text(text):
                    # Hands-free mode: normal filtering
                    if is_change_mode_command:
                        logger.debug(f"STT: Allowing 'change mode' command through: '{text}'")
                    else:
                        logger.debug(f"STT: Text is meaningful, yielding TranscriptionFrame: '{text}'")
                    frame = TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                    yield frame
                    logger.debug(f"STT: TranscriptionFrame yielded successfully")
                else:
                    logger.warning(f"🚫 STT Rejected (artifact/filler): '{text}'")
            else:
                logger.warning("STT: run_stt() produced empty text")
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            yield ErrorFrame(error=str(e))

    async def _on_speaking_stopped(self, frame, direction):
        """Process accumulated audio when user stops speaking."""
        if not self._audio_buffer:
            return
        audio_bytes = b"".join(self._audio_buffer)
        self._audio_buffer = []
        duration_ms = (len(audio_bytes) / (16000 * 2)) * 1000
        logger.debug(f"STT: Processing hands-free audio: {duration_ms:.0f}ms ({len(audio_bytes)} bytes)")
        async for result_frame in self.run_stt(audio_bytes):
            if self._stt_cancelled:
                break
            if isinstance(result_frame, TranscriptionFrame):
                logger.debug(f"STT: Transcription: '{result_frame.text}'")
            await self.push_frame(result_frame, direction)

    async def process_frame(self, frame, direction):
        """Process frames from the transport"""
        # --- Common preamble (base class helpers) ---
        if self._should_filter_interruption(frame):
            return
        await self._handle_pending_interruption(direction)
        self._store_pipeline_context(direction)

        # Process PTT buffer if PTT was just deactivated
        if self._pending_ptt_process and self._ptt_buffer_accumulator:
            self._pending_ptt_process = False
            await self._process_ptt_buffer_immediate(direction)

        # --- SpeakingStarted / Stopped (delegated to base) ---
        if await self._handle_speaking_started(frame, direction):
            return
        if await self._handle_speaking_stopped(frame, direction):
            return

        # --- Audio frames ---
        # Transport emits InputAudioRawFrame; both must be handled for PTT capture
        if isinstance(frame, (AudioRawFrame, InputAudioRawFrame)):
            if self._ptt_active:
                self._ptt_buffer_accumulator.append(frame.audio)
                if len(self._ptt_buffer_accumulator) == 1:
                    logger.debug("STT: PTT receiving audio (first chunk)")
            elif not self._ptt_active and self._ptt_buffer_accumulator:
                logger.warning("STT: PTT buffer exists but PTT inactive - processing as fallback")
                await self._process_ptt_buffer_immediate(direction)
            elif (self._is_hands_free or self._is_dictating) and self._user_speaking:
                self._audio_buffer.append(frame.audio)
                # Check if TTS started playing again while user is still talking.
                # VAD won't fire a new SpeakingStartedFrame because the user never
                # stopped, so we need to re-interrupt from the audio frame path.
                await self._check_continuous_speech_interruption(frame.audio, direction)
            elif self._is_hands_free or self._is_dictating:
                self._pre_buffer.append(frame.audio)
                # Check if a SpeakingStartedFrame was suppressed by the echo gate.
                # The user might actually be speaking — keep checking energy per-frame.
                await self._check_pending_bargein(frame.audio, direction)
            return

        # Handle system frames (StartFrame, EndFrame, etc.)
        await super().process_frame(frame, direction)
        
        # FrameProcessor consumes StartFrame and InterruptionFrame but doesn't push them.
        # We must push them manually to propagate them downstream.
        # NOTE: InterruptionFrames are already filtered at the top of this method
        if isinstance(frame, (StartFrame, EndFrame, CancelFrame, InterruptionFrame)):
            # InterruptionFrames were already filtered at the top - if we reach here, it's hands-free mode or PTT-generated
            if isinstance(frame, InterruptionFrame):
                # This should only happen in hands-free mode (VAD-generated) or from PTT (explicit)
                logger.debug(f"STT: Pushing InterruptionFrame downstream (hands_free={self._is_hands_free}, ptt_active={self._ptt_active})")
            
            if isinstance(frame, StartFrame):
                logger.debug(f"STT: Pushing StartFrame downstream. Direction: {direction}")
                logger.debug(f"STT: self._next processor: {self._next}")
            await self.push_frame(frame, direction)
        elif isinstance(frame, TranscriptionFrame):
            # Pass through TranscriptionFrames (e.g., from tests or other sources)
            logger.debug(f"STT: Passing through TranscriptionFrame: '{frame.text}'")
            await self.push_frame(frame, direction)
    
    def get_sample_rate(self) -> int:
        """Return the required sample rate for Whisper (16kHz)"""
        return 16000
    
    def transcribe_file(self, audio_file_path: str) -> Optional[str]:
        """Transcribe an audio file using the already-loaded Whisper model.
        
        Args:
            audio_file_path: Path to the audio file to transcribe
            
        Returns:
            Transcribed text or None if transcription fails
        """
        try:
            import numpy as np
            import soundfile as sf
            
            logger.info(f"WhisperSTTService: Transcribing file {audio_file_path} using already-loaded model {self.model_path}")
            
            # Load audio file and convert to 16kHz mono
            data, sample_rate = sf.read(audio_file_path)
            
            # Convert to mono if stereo
            if len(data.shape) > 1:
                data = np.mean(data, axis=1)
            
            # Resample to 16kHz if needed
            if sample_rate != 16000:
                from scipy import signal
                num_samples = int(len(data) * 16000 / sample_rate)
                data = signal.resample(data, num_samples)
                sample_rate = 16000
            
            # Convert to float32 in range [-1, 1]
            if data.dtype != np.float32:
                if data.dtype == np.int16:
                    data = data.astype(np.float32) / 32768.0
                elif data.dtype == np.int32:
                    data = data.astype(np.float32) / 2147483648.0
                else:
                    data = data.astype(np.float32)
                    if data.max() > 1.0:
                        data = data / data.max()
            
            # Transcribe using the already-loaded model
            segments = self.model.transcribe(data)
            
            # Extract text from segments
            if isinstance(segments, list):
                text = " ".join([seg.text for seg in segments if hasattr(seg, 'text')]).strip()
            elif isinstance(segments, dict):
                text = segments.get('text', '').strip()
            else:
                text = str(segments).strip()
            
            logger.debug(f"WhisperSTTService: File transcription completed ({len(text)} chars)")
            return text if text else None
            
        except ImportError as e:
            logger.error(f"WhisperSTTService: Missing dependency for file transcription: {e}")
            return None
        except Exception as e:
            logger.error(f"WhisperSTTService: Error transcribing file: {e}", exc_info=True)
            return None