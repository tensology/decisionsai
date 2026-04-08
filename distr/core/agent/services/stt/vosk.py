import asyncio
import logging
import time
import numpy as np
import sys
import os
import json
from contextlib import contextmanager

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE,
    AudioRawFrame, InputAudioRawFrame, TranscriptionFrame, ErrorFrame,
    InterruptionFrame,
    SpeakingStartedFrames, SpeakingStoppedFrames,
    UserStoppedSpeakingFrame,
    vosk, VOSK_AVAILABLE
)
from distr.core.agent.services.stt.base import BaseSTTService

logger = logging.getLogger(__name__)

@contextmanager
def suppress_stderr():
    """Context manager to suppress stderr output"""
    with open(os.devnull, 'w') as devnull:
        old_stderr = sys.stderr
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.stderr = old_stderr


class VoskSTTService(BaseSTTService):
    """Vosk-based STT service using Pipecat"""

    def __init__(self, model_path: str, event_queue=None, is_hands_free=False, **kwargs):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for VoskSTTService")
        if not VOSK_AVAILABLE:
            raise ImportError("vosk is required for VoskSTTService. Install with: pip install vosk")

        super().__init__(event_queue=event_queue, is_hands_free=is_hands_free, **kwargs)
        self.model_path = model_path

        # Initialize Vosk model
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Vosk model not found at {model_path}")

        with suppress_stderr():
            self.model = vosk.Model(model_path)

        # Vosk-specific state
        self.recognizer = None  # Created per-transcription in run_stt
        self._continuous_recognizer = None  # Persistent recognizer for continuous recognition
        self._last_transcription_time = None
        self._accumulated_audio_bytes = b''  # Accumulate audio for continuous recognition
        self._min_audio_duration_ms = 2000  # Vosk needs longer than the base default of 1000

        logger.info(f"VoskSTTService initialized with model: {model_path}, is_hands_free={is_hands_free} (PTT mode: {not is_hands_free})")
    
    async def _process_ptt_buffer_immediate(self, direction):
        """Process PTT buffer immediately when PTT is released"""
        logger.debug(f"STT: Processing PTT buffer immediately ({len(self._ptt_buffer_accumulator)} chunks)")
        
        if not self._ptt_buffer_accumulator:
            logger.warning("STT: _process_ptt_buffer_immediate() called but buffer is empty")
            return
        
        audio_bytes = b"".join(self._ptt_buffer_accumulator)
        chunk_count = len(self._ptt_buffer_accumulator)
        self._ptt_buffer_accumulator = []
        logger.debug(f"STT: Extracted {len(audio_bytes)} bytes from {chunk_count} chunks")
        
        if len(audio_bytes) > 0:
            sample_rate = 16000
            bytes_per_second = sample_rate * 2
            duration_ms = (len(audio_bytes) / bytes_per_second) * 1000
            logger.debug(f"STT: Processing FULL PTT buffer: {duration_ms:.0f}ms ({len(audio_bytes)} bytes)")
            
            if duration_ms < 1000:
                silence_needed_ms = 1000 - duration_ms
                silence_bytes = int((silence_needed_ms / 1000) * bytes_per_second)
                silence = b'\x00' * silence_bytes
                audio_bytes = audio_bytes + silence
                logger.debug(f"STT: Padded PTT audio from {duration_ms:.0f}ms to 1000ms")
            
            try:
                stop_frame = UserStoppedSpeakingFrame()
                logger.debug("STT: Sending UserStoppedSpeakingFrame to reset TTS cancelled state")
                await self.push_frame(stop_frame, direction)
            except Exception as e:
                logger.error(f"STT: Error sending UserStoppedSpeakingFrame: {e}")
            
            logger.debug(f"STT: Starting run_stt() for FULL PTT audio ({len(audio_bytes)} bytes)")
            frame_count = 0
            try:
                async for result_frame in self.run_stt(audio_bytes):
                    if self._stt_cancelled:
                        logger.debug("STT: Transcription cancelled during processing - stopping")
                        break
                    frame_count += 1
                    logger.debug(f"STT: run_stt() yielded frame #{frame_count}: {type(result_frame).__name__}")
                    if isinstance(result_frame, TranscriptionFrame):
                        logger.debug(f"STT: Pushing TranscriptionFrame to LLM: '{result_frame.text}'")
                    try:
                        await self.push_frame(result_frame, direction)
                        if isinstance(result_frame, TranscriptionFrame):
                            logger.debug(f"STT: Successfully pushed TranscriptionFrame to pipeline")
                    except Exception as e:
                        logger.error(f"STT: Error pushing frame: {e}", exc_info=True)
                if frame_count == 0:
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
    
    async def _process_continuous_audio_chunk(self, audio: bytes, direction):
        """Process a chunk of audio using continuous recognizer (for hands-free mode).
        This processes audio incrementally as it arrives to capture the first word.
        """
        if self._continuous_recognizer is None:
            sample_rate = 16000
            self._continuous_recognizer = vosk.KaldiRecognizer(self.model, sample_rate)
            self._continuous_recognizer.SetWords(True)
        
        if self._stt_cancelled:
            return
        
        # Check audio level
        try:
            audio_np = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            max_amplitude = np.max(np.abs(audio_np))
            
            SILENCE_THRESHOLD = 0.001
            if max_amplitude < SILENCE_THRESHOLD:
                logger.debug(f"STT: Skipping continuous chunk processing - audio too quiet (max_amplitude={max_amplitude:.6f})")
                return
        except Exception as e:
            logger.debug(f"STT: Audio level check failed: {e}, proceeding")
        
        loop = asyncio.get_running_loop()
        try:
            # Process audio chunk with continuous recognizer
            def process_chunk():
                if self._stt_cancelled:
                    return None
                
                # Process the chunk
                if self._continuous_recognizer.AcceptWaveform(audio):
                    # Got a final result - this means a complete phrase was recognized
                    result = json.loads(self._continuous_recognizer.Result())
                    if 'text' in result and result['text']:
                        return result['text'].strip()
                else:
                    # Get partial result - this is what we want for incremental processing
                    # Partial results allow us to see words as they're being recognized
                    partial = json.loads(self._continuous_recognizer.PartialResult())
                    if 'partial' in partial and partial['partial']:
                        return partial['partial'].strip()
                
                return None
            
            text = await loop.run_in_executor(None, process_chunk)
            
            if self._stt_cancelled:
                return
            
            if text:
                logger.debug("[STT] PICKED UP: %s", text)
                logger.debug(f"🔊 Continuous Transcription (incremental): '{text}'")
                # CRITICAL: Always send transcriptions that contain "change mode" commands
                text_lower = text.lower()
                is_change_mode_command = any(
                    pattern in text_lower 
                    for pattern in ["change mode", "switch mode", "toggle mode", "ptt mode", "continuous mode", "hands free mode"]
                )
                
                if is_change_mode_command or self._is_meaningful_text(text):
                    if is_change_mode_command:
                        logger.debug(f"STT: Allowing 'change mode' command through: '{text}'")
                    else:
                        logger.debug(f"STT: Text is meaningful, yielding TranscriptionFrame: '{text}'")
                    frame = TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                    await self.push_frame(frame, direction)
                    logger.debug(f"STT: TranscriptionFrame pushed successfully")
                else:
                    logger.debug(f"🚫 STT Rejected (artifact/filler): '{text}'")
        except Exception as e:
            logger.error(f"Continuous transcription chunk error: {e}", exc_info=True)
            await self.push_frame(ErrorFrame(error=str(e)), direction)
    
    async def _process_continuous_audio(self, audio: bytes, direction):
        """Process audio using continuous recognizer (for hands-free mode).
        This is the legacy method - kept for backwards compatibility but not used in incremental mode.
        """
        if self._continuous_recognizer is None:
            sample_rate = 16000
            self._continuous_recognizer = vosk.KaldiRecognizer(self.model, sample_rate)
            self._continuous_recognizer.SetWords(True)
        
        if self._stt_cancelled:
            return
        
        # Check audio level
        try:
            audio_np = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            max_amplitude = np.max(np.abs(audio_np))
            
            SILENCE_THRESHOLD = 0.001
            if max_amplitude < SILENCE_THRESHOLD:
                logger.debug(f"STT: Skipping continuous processing - audio too quiet (max_amplitude={max_amplitude:.6f})")
                return
        except Exception as e:
            logger.debug(f"STT: Audio level check failed: {e}, proceeding")
        
        loop = asyncio.get_running_loop()
        try:
            # Process audio in chunks with continuous recognizer
            chunk_size = 4000  # 4KB chunks
            text_parts = []
            
            def process_audio_chunks():
                nonlocal text_parts
                for i in range(0, len(audio), chunk_size):
                    if self._stt_cancelled:
                        return None
                    chunk = audio[i:i+chunk_size]
                    if self._continuous_recognizer.AcceptWaveform(chunk):
                        result = json.loads(self._continuous_recognizer.Result())
                        if 'text' in result and result['text']:
                            text_parts.append(result['text'])
                    else:
                        # Get partial result (for real-time feedback, but we'll use final results)
                        partial = json.loads(self._continuous_recognizer.PartialResult())
                        # Partial results are not used here, only final results
                
                # Get any final result
                final_result = json.loads(self._continuous_recognizer.FinalResult())
                if 'text' in final_result and final_result['text']:
                    text_parts.append(final_result['text'])
                
                return " ".join(text_parts).strip()
            
            text = await loop.run_in_executor(None, process_audio_chunks)
            
            if self._stt_cancelled:
                return
            
            if text:
                logger.debug("[STT] PICKED UP: %s", text)
                logger.debug(f"🔊 Continuous Transcription: '{text}'")
                # CRITICAL: Always send transcriptions that contain "change mode" commands
                text_lower = text.lower()
                is_change_mode_command = any(
                    pattern in text_lower 
                    for pattern in ["change mode", "switch mode", "toggle mode", "ptt mode", "continuous mode", "hands free mode"]
                )
                
                if is_change_mode_command or self._is_meaningful_text(text):
                    if is_change_mode_command:
                        logger.debug(f"STT: Allowing 'change mode' command through: '{text}'")
                    else:
                        logger.debug(f"STT: Text is meaningful, yielding TranscriptionFrame: '{text}'")
                    frame = TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                    await self.push_frame(frame, direction)
                    logger.debug(f"STT: TranscriptionFrame pushed successfully")
                else:
                    logger.debug(f"🚫 STT Rejected (artifact/filler): '{text}'")
        except Exception as e:
            logger.error(f"Continuous transcription error: {e}", exc_info=True)
            await self.push_frame(ErrorFrame(error=str(e)), direction)

    async def run_stt(self, audio: bytes):
        """Process audio bytes and yield transcription frames"""
        self._stt_cancelled = False
        
        sample_rate = 16000
        bytes_per_second = sample_rate * 2
        duration_ms = (len(audio) / bytes_per_second) * 1000
        
        if duration_ms < self._min_audio_duration_ms:
            logger.debug(f"Audio too short ({duration_ms:.0f}ms < {self._min_audio_duration_ms}ms), padding with silence")
            silence_needed_ms = self._min_audio_duration_ms - duration_ms
            silence_bytes = int((silence_needed_ms / 1000) * bytes_per_second)
            silence = b'\x00' * silence_bytes
            audio = audio + silence
            logger.debug(f"Padded audio to {self._min_audio_duration_ms}ms")

        if self._stt_cancelled:
            logger.debug("STT: Transcription cancelled before starting")
            return

        # Check audio level before transcription to avoid unnecessary processing and log spam
        try:
            import numpy as np
            audio_np = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            max_amplitude = np.max(np.abs(audio_np))
            rms = np.sqrt(np.mean(audio_np**2))
            
            # Thresholds for audio level detection
            SILENCE_THRESHOLD = 0.001  # Below this is considered silence
            LOW_AUDIO_THRESHOLD = 0.005  # Below this is very low audio
            
            # Skip transcription if audio is too quiet (likely silence)
            if max_amplitude < SILENCE_THRESHOLD:
                logger.debug(f"STT: Skipping transcription - audio too quiet (max_amplitude={max_amplitude:.6f}, likely silence)")
                return
            elif max_amplitude < LOW_AUDIO_THRESHOLD:
                # Very low audio - log at debug level to reduce spam
                logger.debug(f"STT: Very low audio level (max_amplitude={max_amplitude:.6f}, rms={rms:.6f}) - transcription may fail")
        except ImportError:
            # numpy not available, proceed without audio level check
            logger.debug("STT: numpy not available, skipping audio level check")
        except Exception as e:
            # If audio level check fails, proceed anyway
            logger.debug(f"STT: Audio level check failed: {e}, proceeding with transcription")

        # Create recognizer for this transcription
        self.recognizer = vosk.KaldiRecognizer(self.model, sample_rate)
        self.recognizer.SetWords(True)
        
        loop = asyncio.get_running_loop()
        try:
            # Process audio in chunks (Vosk expects chunks)
            chunk_size = 4000  # Process in 4KB chunks
            text_parts = []
            
            def process_audio():
                nonlocal text_parts
                for i in range(0, len(audio), chunk_size):
                    if self._stt_cancelled:
                        return None
                    chunk = audio[i:i+chunk_size]
                    if self.recognizer.AcceptWaveform(chunk):
                        result = json.loads(self.recognizer.Result())
                        if 'text' in result and result['text']:
                            text_parts.append(result['text'])
                    else:
                        partial = json.loads(self.recognizer.PartialResult())
                        if 'partial' in partial and partial['partial']:
                            # Store partial for final result
                            pass
                
                # Get final result
                try:
                    final_result = json.loads(self.recognizer.FinalResult())
                except (json.JSONDecodeError, ValueError):
                    final_result = {}
                if 'text' in final_result and final_result['text']:
                    text_parts.append(final_result['text'])
                
                return " ".join(text_parts).strip()
            
            text = await loop.run_in_executor(None, process_audio)
            
            if self._stt_cancelled:
                logger.debug("STT: Transcription cancelled after completion - discarding result")
                return
            
            if text:
                logger.debug("[STT] PICKED UP: %s", text)
                model_name = os.path.basename(self.model_path)
                logger.info(f"TRANSCRIPTION [Vosk {model_name}]: '{text}'")
                # CRITICAL: In PTT mode, ALWAYS send transcriptions (don't filter as artifacts)
                # The LLM will handle filtering - we need to send everything so "change mode" can be detected
                if not self._is_hands_free:
                    # PTT mode: send all transcriptions to LLM (LLM will filter)
                    logger.debug(f"STT: PTT mode - sending transcription to LLM (will be filtered by LLM if not 'change mode'): '{text}'")
                    frame = TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                    yield frame
                    logger.debug(f"STT: TranscriptionFrame yielded successfully (PTT mode)")
                else:
                    # Hands-free mode: normal filtering
                    text_lower = text.lower()
                    is_change_mode_command = any(
                        pattern in text_lower 
                        for pattern in ["change mode", "switch mode", "toggle mode", "ptt mode", "continuous mode", "hands free mode"]
                    )
                    
                    if is_change_mode_command or self._is_meaningful_text(text):
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
                # Only log warning if audio level was reasonable (not silence)
                # This prevents log spam from expected empty transcriptions during silence
                try:
                    if max_amplitude >= LOW_AUDIO_THRESHOLD:
                        # Audio level was reasonable but no transcription - this is unexpected
                        logger.warning(f"STT: run_stt() produced empty text (audio_level={max_amplitude:.6f})")
                    else:
                        # Audio was too quiet - expected empty result, log at debug level
                        logger.debug(f"STT: Empty transcription (expected - audio too quiet: {max_amplitude:.6f})")
                except (NameError, UnboundLocalError):
                    # max_amplitude not available (numpy check failed), use warning
                    logger.debug("STT: run_stt() produced empty text (audio level check unavailable)")
        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            yield ErrorFrame(error=str(e))
        finally:
            self.recognizer = None

    async def process_frame(self, frame, direction):
        """Process frames from the transport - same logic as WhisperSTTService"""
        # Store direction and event loop for PTT interruption
        self._pipeline_direction = direction
        if self._event_loop is None:
            try:
                self._event_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        
        # Handle pending interruption from PTT activation
        if self._pending_interruption:
            logger.debug("STT: Sending pending InterruptionFrame (from PTT activation)")
            await self._send_interruption(direction)
            self._pending_interruption = False
        
        # Filter VAD-generated InterruptionFrames based on hands-free mode
        if isinstance(frame, InterruptionFrame):
            if not self._is_hands_free:
                logger.debug("STT: Filtering InterruptionFrame (PTT mode - interruptions disabled)")
                return
            else:
                logger.debug("STT: Passing InterruptionFrame through (hands-free mode)")
        
        # Handle pending PTT buffer processing
        if self._pending_ptt_process:
            self._pending_ptt_process = False
            await self._process_ptt_buffer_immediate(direction)
        
        # Handle UserStartedSpeakingFrame - initialize continuous recognizer
        if isinstance(frame, SpeakingStartedFrames):
            if self._is_hands_free or self._is_dictating:
                # --- Echo gate: suppress false VAD triggers during TTS playback ---
                if self._is_tts_playing() and not self._check_bargein_energy():
                    logger.debug("STT: Suppressing VAD speaking-started (TTS playing, low mic energy — echo)")
                    return

                # Reset recognizer for new utterance, seeding with pre-buffered audio
                mode_name = "dictation mode" if self._is_dictating else "hands-free mode"
                pre_buf_ms = len(self._pre_buffer) * 20
                self._continuous_recognizer = None
                self._audio_buffer = list(self._pre_buffer)
                self._accumulated_audio_bytes = b''.join(self._pre_buffer)
                self._pre_buffer.clear()
                self._user_speaking = True
                logger.debug(f"STT: User started speaking ({mode_name}), seeded {pre_buf_ms}ms pre-buffer")

                # CRITICAL: Send InterruptionFrame to stop current TTS/LLM response
                # Without this, new speech just queues up as additional prompts
                logger.debug("STT: Sending InterruptionFrame (hands-free voice interruption)")
                await self._send_interruption(direction)
        
        # Handle UserStoppedSpeakingFrame - reset continuous recognizer
        if isinstance(frame, SpeakingStoppedFrames):
            self._user_speaking = False
            if self._continuous_recognizer is not None:
                # Get final result before resetting
                try:
                    final_result = json.loads(self._continuous_recognizer.FinalResult())
                    if 'text' in final_result and final_result['text']:
                        text = final_result['text'].strip()
                        if text:
                            # CRITICAL: Always send transcriptions that contain "change mode" commands
                            text_lower = text.lower()
                            is_change_mode_command = any(
                                pattern in text_lower 
                                for pattern in ["change mode", "switch mode", "toggle mode", "ptt mode", "continuous mode", "hands free mode"]
                            )
                            
                            if is_change_mode_command or self._is_meaningful_text(text):
                                logger.debug("[STT] PICKED UP: %s", text)
                                if is_change_mode_command:
                                    logger.debug(f"STT: Allowing 'change mode' command through (final): '{text}'")
                                else:
                                    logger.debug(f"🔊 Final transcription on stop: '{text}'")
                                transcription_frame = TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                                await self.push_frame(transcription_frame, direction)
                except Exception as e:
                    logger.debug(f"Error getting final result: {e}")
                
                # Reset recognizer for next utterance
                self._continuous_recognizer = None
                self._accumulated_audio_bytes = b''
                self._audio_buffer = []
                logger.debug("STT: Reset continuous recognizer on UserStoppedSpeakingFrame")
            
            # Pass through the frame
            await super().process_frame(frame, direction)
            return
        
        # Handle UserStartedSpeakingFrame - pass through after handling
        if isinstance(frame, SpeakingStartedFrames):
            await super().process_frame(frame, direction)
            return
        
        # Handle audio frames (transport emits InputAudioRawFrame; both needed for PTT)
        if isinstance(frame, (AudioRawFrame, InputAudioRawFrame)):
            if self._ptt_active:
                # PTT mode: accumulate audio in buffer
                self._ptt_buffer_accumulator.append(frame.audio)
                n = len(self._ptt_buffer_accumulator)
                total_bytes = sum(len(c) for c in self._ptt_buffer_accumulator)
                if n == 1:
                    logger.debug(f"STT: PTT receiving audio (first chunk, {len(frame.audio)} bytes)")
                logger.debug(f"STT: Accumulated PTT audio chunk ({len(frame.audio)} bytes, total: {n} chunks, {total_bytes} bytes)")
            elif (self._is_hands_free or self._is_dictating) and self._user_speaking:
                # Hands-free mode or dictation mode: accumulate audio for continuous recognition (only when user is speaking)
                self._audio_buffer.append(frame.audio)
                self._accumulated_audio_bytes += frame.audio
                
                # Initialize continuous recognizer if needed
                if self._continuous_recognizer is None:
                    sample_rate = 16000
                    self._continuous_recognizer = vosk.KaldiRecognizer(self.model, sample_rate)
                    self._continuous_recognizer.SetWords(True)
                    logger.debug("STT: Initialized continuous Vosk recognizer")
                
                # Process audio incrementally as it arrives to capture the first word
                # Use a smaller chunk size (0.5 seconds) for immediate processing
                sample_rate = 16000
                bytes_per_second = sample_rate * 2
                incremental_chunk_bytes = int(0.5 * bytes_per_second)  # 0.5 seconds = 16000 bytes
                
                # Process audio in incremental chunks to avoid missing the first word
                if len(self._accumulated_audio_bytes) >= incremental_chunk_bytes:
                    # Extract a chunk for processing (keep the rest for next iteration)
                    audio_chunk = self._accumulated_audio_bytes[:incremental_chunk_bytes]
                    self._accumulated_audio_bytes = self._accumulated_audio_bytes[incremental_chunk_bytes:]
                    
                    if len(audio_chunk) > 0:
                        # Process this chunk immediately with continuous recognizer
                        await self._process_continuous_audio_chunk(audio_chunk, direction)
            elif (self._is_hands_free or self._is_dictating):
                # Not yet speaking: maintain rolling pre-buffer for speech onset capture
                self._pre_buffer.append(frame.audio)

            # Don't pass AudioRawFrame downstream - it's consumed here
            return
        
        # Pass through other frames
        await super().process_frame(frame, direction)

