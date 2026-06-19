"""
AssemblyAI STT Service for Pipecat

AssemblyAI real-time streaming transcription service using Pipecat.
Uses the v3 WebSocket-based streaming API for low-latency transcription.
Supports both hands-free and push-to-talk modes.
"""

import asyncio
import logging
import time
import io
import string
import wave
import numpy as np
from typing import Optional

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE,
    AudioRawFrame, InputAudioRawFrame, TranscriptionFrame, ErrorFrame,
    EndFrame, StartFrame, CancelFrame, InterruptionFrame,
    UserStoppedSpeakingFrame,
    SpeakingStartedFrames, SpeakingStoppedFrames,
)
from distr.core.agent.services.stt.base import BaseSTTService

try:
    from queue import Full
except ImportError:
    Full = Exception

logger = logging.getLogger(__name__)

# Check if AssemblyAI is available
try:
    import assemblyai as aai
    ASSEMBLYAI_AVAILABLE = True
    # Check for v3 streaming API
    try:
        from assemblyai.streaming.v3 import StreamingClient, StreamingClientOptions, StreamingParameters, StreamingEvents, BeginEvent, TurnEvent, TerminationEvent, StreamingError
        ASSEMBLYAI_V3_AVAILABLE = True
    except ImportError:
        ASSEMBLYAI_V3_AVAILABLE = False
        logger.warning("AssemblyAI v3 streaming API not available - update assemblyai package")
except ImportError:
    aai = None
    ASSEMBLYAI_AVAILABLE = False
    ASSEMBLYAI_V3_AVAILABLE = False
    logger.warning("AssemblyAI library not available - AssemblyAISTTService requires it. Install with: pip install assemblyai")


class AssemblyAISTTService(BaseSTTService):
    """AssemblyAI real-time streaming STT service using Pipecat
    
    Uses the v3 streaming API for low-latency WebSocket-based streaming.
    Falls back to batch API if v3 is not available.
    """

    def __init__(self, api_key: str, model: str = None, event_queue=None, is_hands_free=False, **kwargs):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for AssemblyAISTTService")
        if not ASSEMBLYAI_AVAILABLE:
            raise ImportError("assemblyai library is required for AssemblyAISTTService. Install with: pip install assemblyai")

        super().__init__(event_queue=event_queue, is_hands_free=is_hands_free, **kwargs)

        # AssemblyAI-specific state
        self.api_key = api_key
        self.speech_model = model or "universal"
        aai.settings.api_key = api_key

        # V3 streaming state
        self._streaming_client: Optional['StreamingClient'] = None
        self._streaming_connected = False
        self._pending_transcripts: asyncio.Queue = None

        # AssemblyAI uses a shorter minimum than the base default (1000ms)
        self._min_audio_duration_ms = 500

        if ASSEMBLYAI_V3_AVAILABLE:
            logger.info(f"AssemblyAISTTService initialized with v3 API, is_hands_free={is_hands_free}")
        else:
            logger.info(f"AssemblyAISTTService initialized (batch mode only), is_hands_free={is_hands_free}")

    # =========================================================================
    # V3 Streaming API Methods
    # =========================================================================
    
    def _on_begin(self, client, event: 'BeginEvent'):
        """Callback when v3 streaming session begins"""
        logger.info(f"AssemblyAI v3: Session started with ID: {event.id}")
        self._streaming_connected = True
    
    def _on_turn(self, client, event: 'TurnEvent'):
        """Callback when v3 streaming transcript is received"""
        text = event.transcript.strip() if event.transcript else ""
        
        if event.end_of_turn and text:
            # Final transcript for this turn
            if self._is_meaningful_text(text):
                logger.debug("[STT] PICKED UP: %s", text)
                logger.info(f"TRANSCRIPTION [AssemblyAI {self.speech_model} (Streaming)]: '{text}'")

                # Queue the transcript for async processing
                if self._pending_transcripts and self._event_loop:
                    try:
                        self._event_loop.call_soon_threadsafe(
                            self._pending_transcripts.put_nowait,
                            text
                        )
                    except Exception as e:
                        logger.error(f"AssemblyAI: Error queueing transcript: {e}")
            else:
                logger.debug(f"AssemblyAI v3: Filtered out: '{text}'")
        else:
            # Partial transcript
            if text:
                logger.debug(f"AssemblyAI v3 partial: '{text}'")
    
    def _on_terminated(self, client, event: 'TerminationEvent'):
        """Callback when v3 streaming session terminates"""
        logger.info(f"AssemblyAI v3: Session terminated - {event.audio_duration_seconds:.1f}s audio processed")
        self._streaming_connected = False
    
    def _on_error(self, client, error: 'StreamingError'):
        """Callback when v3 streaming error occurs"""
        logger.error(f"AssemblyAI v3 error: {error}")
        self._streaming_connected = False
    
    async def _connect_streaming(self):
        """Connect to AssemblyAI v3 streaming API"""
        if not ASSEMBLYAI_V3_AVAILABLE:
            logger.warning("AssemblyAI v3 API not available")
            return False
        
        if self._streaming_connected:
            return True
        
        try:
            logger.info("AssemblyAI: Connecting to v3 streaming API...")
            
            # Create streaming client
            self._streaming_client = StreamingClient(
                StreamingClientOptions(api_key=self.api_key)
            )
            
            # Register event handlers
            self._streaming_client.on(StreamingEvents.Begin, self._on_begin)
            self._streaming_client.on(StreamingEvents.Turn, self._on_turn)
            self._streaming_client.on(StreamingEvents.Termination, self._on_terminated)
            self._streaming_client.on(StreamingEvents.Error, self._on_error)
            
            # Connect in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._streaming_client.connect(
                    StreamingParameters(
                        sample_rate=16000,
                        formatted_finals=True,
                    )
                )
            )
            
            # Wait briefly for connection
            for _ in range(10):
                if self._streaming_connected:
                    logger.info("AssemblyAI v3: Connection established")
                    return True
                await asyncio.sleep(0.1)
            
            logger.warning("AssemblyAI v3: Connection timeout")
            return False
            
        except Exception as e:
            logger.error(f"AssemblyAI v3: Failed to connect: {e}")
            self._streaming_client = None
            return False
    
    async def _disconnect_streaming(self):
        """Disconnect from v3 streaming API"""
        if self._streaming_client is not None:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._streaming_client.disconnect)
            except Exception as e:
                logger.debug(f"Error disconnecting streaming client: {e}")
            finally:
                self._streaming_client = None
                self._streaming_connected = False
                logger.debug("AssemblyAI v3: Disconnected")
    
    def _stream_audio(self, audio_bytes: bytes):
        """Stream audio to v3 API"""
        if not self._streaming_connected or self._streaming_client is None:
            return
        
        try:
            self._streaming_client.send_audio(audio_bytes)
        except Exception as e:
            logger.warning(f"AssemblyAI v3: Error sending audio: {e}")
            self._streaming_connected = False
    
    async def _process_pending_transcripts(self, direction):
        """Process any pending transcripts from streaming API"""
        if self._pending_transcripts is None:
            return
        
        while not self._pending_transcripts.empty():
            try:
                text = self._pending_transcripts.get_nowait()
                frame = TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                await self.push_frame(frame, direction)
                logger.debug(f"STT: Pushed v3 transcription to LLM: '{text}'")
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.error(f"Error processing transcript: {e}")

    # =========================================================================
    # PTT and State Management
    # =========================================================================

    async def _process_ptt_buffer_immediate(self, direction):
        """Process PTT buffer using batch API"""
        if not self._ptt_buffer_accumulator:
            logger.warning("STT: PTT buffer empty")
            return
        
        audio_bytes = b"".join(self._ptt_buffer_accumulator)
        chunk_count = len(self._ptt_buffer_accumulator)
        self._ptt_buffer_accumulator = []
        
        total_bytes = len(audio_bytes)
        duration_ms = (total_bytes / (16000 * 2)) * 1000
        logger.debug(f"STT: Processing PTT buffer ({chunk_count} chunks, ~{duration_ms:.0f}ms)")
        
        if duration_ms < self._min_audio_duration_ms:
            logger.warning(f"STT: Audio too short ({duration_ms:.0f}ms < {self._min_audio_duration_ms}ms) - skipping")
            return
        
        # Pad if needed
        if duration_ms < 1000:
            silence_bytes = int((1000 - duration_ms) / 1000 * 16000 * 2)
            audio_bytes = audio_bytes + b'\x00' * silence_bytes
        
        # Send UserStoppedSpeakingFrame
        try:
            await self.push_frame(UserStoppedSpeakingFrame(), direction)
        except Exception as e:
            logger.error(f"STT: Error sending UserStoppedSpeakingFrame: {e}")
        
        # Process with batch API
        frame_count = 0
        try:
            async for result_frame in self.run_stt(audio_bytes):
                if self._stt_cancelled:
                    break
                frame_count += 1
                await self.push_frame(result_frame, direction)
            
            if frame_count == 0:
                logger.warning("STT: run_stt() did not yield any frames")
            else:
                logger.debug(f"STT: PTT transcription complete ({frame_count} frames)")
        except Exception as e:
            logger.error(f"STT: Error in run_stt(): {e}", exc_info=True)
    
    async def _send_interruption(self, direction):
        """Send interruption frame"""
        try:
            await self.push_frame(InterruptionFrame(), direction)
            logger.debug("STT: InterruptionFrame sent")
        except Exception as e:
            logger.error(f"Error sending InterruptionFrame: {e}")
    
    def set_hands_free(self, enabled: bool):
        """Set hands-free mode"""
        old_value = self._is_hands_free
        self._is_hands_free = enabled
        logger.info(f"STT hands-free mode: {old_value} -> {enabled}")
        
        if enabled and ASSEMBLYAI_V3_AVAILABLE:
            if hasattr(self, '_event_loop') and self._event_loop and self._event_loop.is_running():
                asyncio.run_coroutine_threadsafe(self._connect_streaming(), self._event_loop)
        elif not enabled and self._streaming_connected:
            if hasattr(self, '_event_loop') and self._event_loop and self._event_loop.is_running():
                asyncio.run_coroutine_threadsafe(self._disconnect_streaming(), self._event_loop)
    
    def set_dictating(self, enabled: bool):
        """Set dictation mode"""
        old_value = self._is_dictating
        self._is_dictating = enabled
        logger.info(f"STT dictation mode: {old_value} -> {enabled}")

    def _is_meaningful_text(self, text: str) -> bool:
        if not text:
            return False
        text_lower = text.strip().lower()
        if text_lower in self._audio_artifacts:
            return False
        text_no_punct = text_lower.translate(str.maketrans('', '', string.punctuation)).strip()
        if text_no_punct in self._filler_words:
            return False
        return True

    # =========================================================================
    # Batch API (for PTT mode)
    # =========================================================================
    
    async def run_stt(self, audio: bytes):
        """Process audio bytes using AssemblyAI batch API"""
        self._stt_cancelled = False
        
        sample_rate = 16000
        bytes_per_second = sample_rate * 2
        duration_ms = (len(audio) / bytes_per_second) * 1000
        
        if duration_ms < self._min_audio_duration_ms:
            logger.debug(f"Audio too short ({duration_ms:.0f}ms), padding")
            silence_bytes = int((self._min_audio_duration_ms - duration_ms) / 1000 * bytes_per_second)
            audio = audio + b'\x00' * silence_bytes

        if self._stt_cancelled:
            return

        loop = asyncio.get_running_loop()
        try:
            # Create WAV file in memory
            audio_np = np.frombuffer(audio, dtype=np.int16)
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(audio_np.tobytes())
            
            wav_buffer.seek(0)
            
            def transcribe():
                if self._stt_cancelled:
                    return None
                try:
                    transcriber = aai.Transcriber()
                    config = aai.TranscriptionConfig(language_code="en")
                    transcript = transcriber.transcribe(wav_buffer, config=config)
                    return transcript.text if transcript and transcript.text else None
                except Exception as e:
                    logger.error(f"AssemblyAI batch API error: {e}")
                    raise
            
            text = await loop.run_in_executor(None, transcribe)
            
            if self._stt_cancelled:
                return
            
            if text and text.strip():
                text = text.strip()
                logger.debug("[STT] PICKED UP: %s", text)
                logger.info(f"TRANSCRIPTION [AssemblyAI {self.speech_model}]: '{text}'")

                if self._is_meaningful_text(text):
                    yield TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                else:
                    logger.warning(f"🚫 STT Rejected: '{text}'")
            else:
                logger.warning("STT: run_stt() produced empty text")
        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            yield ErrorFrame(error=str(e))

    # =========================================================================
    # Frame Processing
    # =========================================================================
    
    async def process_frame(self, frame, direction):
        """Process frames from the transport"""
        # Store direction and event loop
        self._pipeline_direction = direction
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                self._event_loop = asyncio.get_event_loop()
            except RuntimeError:
                self._event_loop = None
        
        # Initialize pending transcripts queue
        if self._pending_transcripts is None:
            self._pending_transcripts = asyncio.Queue()
        
        # Handle InterruptionFrame
        if isinstance(frame, InterruptionFrame):
            if self._is_hands_free or self._is_dictating:
                await super().process_frame(frame, direction)
            else:
                logger.debug("STT: Filtering InterruptionFrame in PTT mode")
                return
        
        # Send pending interruption
        if self._pending_interruption:
            self._pending_interruption = False
            try:
                await self._send_interruption(direction)
            except Exception as e:
                logger.error(f"STT: FORCE INTERRUPTION failed: {e}")
                self._pending_interruption = True
        
        # Process pending PTT buffer
        if (
            self._pending_ptt_process
            and self._ptt_buffer_accumulator
            and not getattr(self, "_ptt_flush_scheduled", False)
        ):
            self._pending_ptt_process = False
            await self._process_ptt_buffer_immediate(direction)
        
        # Process pending streaming transcripts
        if (self._is_hands_free or self._is_dictating) and self._pending_transcripts:
            await self._process_pending_transcripts(direction)
        
        # Handle StartFrame
        if isinstance(frame, StartFrame):
            self._event_loop = asyncio.get_running_loop()
            if self._is_hands_free and ASSEMBLYAI_V3_AVAILABLE:
                asyncio.create_task(self._connect_streaming())
            await super().process_frame(frame, direction)
            return
        
        # Handle EndFrame
        if isinstance(frame, EndFrame):
            if self._streaming_connected:
                await self._disconnect_streaming()
            await super().process_frame(frame, direction)
            return
        
        # Handle CancelFrame
        if isinstance(frame, CancelFrame):
            self._stt_cancelled = True
            if self._current_stt_task:
                self._current_stt_task.cancel()
            if self._streaming_connected:
                await self._disconnect_streaming()
            await super().process_frame(frame, direction)
            return
        
        # Handle VAD speaking frames
        if isinstance(frame, SpeakingStartedFrames):
            if self._ptt_active:
                return
            
            if self._is_hands_free or self._is_dictating:
                # --- Echo gate: suppress false VAD triggers during TTS playback ---
                if self._is_tts_playing() and not self._check_bargein_energy():
                    logger.debug("STT: Suppressing VAD speaking-started (TTS playing, low mic energy — echo)")
                    return

                self._user_speaking = True
                self._audio_buffer = list(self._pre_buffer)
                # Send pre-buffered audio to streaming API if connected
                if self._streaming_connected:
                    for chunk in self._pre_buffer:
                        self._stream_audio(chunk)
                pre_buf_ms = len(self._pre_buffer) * 20
                self._pre_buffer.clear()
                logger.debug(f"STT: User started speaking, seeded {pre_buf_ms}ms pre-buffer")

                # CRITICAL: Send InterruptionFrame to stop current TTS/LLM response
                # Without this, new speech just queues up as additional prompts
                logger.debug("STT: Sending InterruptionFrame (hands-free voice interruption)")
                await self._send_interruption(direction)

                if self._is_hands_free and self.event_queue:
                    try:
                        self.event_queue.put(('stt_hands_free_glow_on', {}), block=False)
                    except Exception:
                        pass

                await self.push_frame(frame, direction)
            return

        if isinstance(frame, SpeakingStoppedFrames):
            if (self._is_hands_free or self._is_dictating) and self._user_speaking and not self._ptt_active:
                self._user_speaking = False

                # Only emit glow off signal if in hands-free mode (not dictation, not PTT)
                if self._is_hands_free and not self._ptt_active and self.event_queue:
                    try:
                        self.event_queue.put(('stt_hands_free_glow_off', {}), block=False)
                    except Exception:
                        pass
                
                # For hands-free: If streaming connected, transcripts come via callback
                # Otherwise fall back to batch processing
                if self._audio_buffer:
                    if self._streaming_connected:
                        logger.debug("STT: Audio streamed to v3 API - awaiting transcription")
                    else:
                        # Fall back to batch API
                        audio_bytes = b"".join(self._audio_buffer)
                        self._audio_buffer = []
                        duration_ms = (len(audio_bytes) / (16000 * 2)) * 1000
                        logger.debug(f"STT: Processing hands-free audio with batch API: {duration_ms:.0f}ms")
                        
                        async for result_frame in self.run_stt(audio_bytes):
                            if self._stt_cancelled:
                                break
                            await self.push_frame(result_frame, direction)
            
            await self.push_frame(frame, direction)
            return
        
        # Handle audio frames
        if isinstance(frame, (AudioRawFrame, InputAudioRawFrame)):
            if self._ptt_active:
                # PTT mode: accumulate for batch processing
                self._ptt_buffer_accumulator.append(frame.audio)
                if len(self._ptt_buffer_accumulator) % 50 == 0:
                    total_bytes = sum(len(c) for c in self._ptt_buffer_accumulator)
                    duration_ms = (total_bytes / (16000 * 2)) * 1000
                    logger.debug(f"STT: PTT buffer: {len(self._ptt_buffer_accumulator)} chunks, ~{duration_ms:.0f}ms")
            elif not self._ptt_active and self._ptt_buffer_accumulator:
                # PTT just released
                await self._process_ptt_buffer_immediate(direction)
            elif (self._is_hands_free or self._is_dictating) and self._user_speaking:
                # Hands-free mode: stream to v3 API or buffer for batch
                self._audio_buffer.append(frame.audio)
                if self._streaming_connected:
                    self._stream_audio(frame.audio)
            elif (self._is_hands_free or self._is_dictating):
                # Not yet speaking: maintain rolling pre-buffer for speech onset capture
                self._pre_buffer.append(frame.audio)
            return
        
        # Pass through other frames
        await super().process_frame(frame, direction)
    
    def get_sample_rate(self) -> int:
        """Return the required sample rate (16kHz)"""
        return 16000
    
    def transcribe_file(self, audio_file_path: str) -> Optional[str]:
        """Transcribe an audio file using AssemblyAI batch API."""
        try:
            logger.info(f"AssemblyAISTTService: Transcribing file {audio_file_path}")
            transcriber = aai.Transcriber()
            config = aai.TranscriptionConfig(language_code="en")
            transcript = transcriber.transcribe(audio_file_path, config=config)
            text = transcript.text if transcript else None
            logger.info(f"AssemblyAISTTService: Transcription complete ({len(text) if text else 0} chars)")
            return text
        except Exception as e:
            logger.error(f"AssemblyAISTTService: Error transcribing file: {e}", exc_info=True)
            return None
