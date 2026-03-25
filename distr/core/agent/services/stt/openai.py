"""
OpenAI STT Service for Pipecat

OpenAI Whisper/Realtime API-based STT service using Pipecat.
Supports both batch API (for PTT) and Realtime API (for hands-free streaming).
"""

import asyncio
import logging
import string
import time
import base64
import json
import numpy as np
import sys
import os
import io
from typing import Optional
from contextlib import contextmanager

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE,
    AudioRawFrame, InputAudioRawFrame, TranscriptionFrame, ErrorFrame,
    EndFrame, StartFrame, CancelFrame, InterruptionFrame,
    UserStoppedSpeakingFrame, SpeakingStartedFrames, SpeakingStoppedFrames,
)
from distr.core.agent.services.stt.base import BaseSTTService

try:
    from queue import Full
except ImportError:
    Full = Exception

logger = logging.getLogger(__name__)

# Check if OpenAI is available
try:
    from openai import OpenAI, AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None
    AsyncOpenAI = None
    OPENAI_AVAILABLE = False

# Check if websockets is available
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None
    WEBSOCKETS_AVAILABLE = False

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


class OpenAIWhisperSTTService(BaseSTTService):
    """OpenAI Whisper API-based STT service using Pipecat
    
    Uses:
    - Batch API for PTT mode (complete utterance after button release)
    - Realtime API for hands-free mode (streaming audio via WebSocket)
    """

    def __init__(self, api_key: str, model: str = "whisper-1", event_queue=None, is_hands_free=False, **kwargs):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for OpenAIWhisperSTTService")
        if not OPENAI_AVAILABLE:
            raise ImportError("openai library is required for OpenAIWhisperSTTService. Install with: pip install openai")

        super().__init__(event_queue=event_queue, is_hands_free=is_hands_free, **kwargs)

        # OpenAI-specific state
        self.api_key = api_key
        self.model = model
        self.client = OpenAI(api_key=api_key)

        # Keep explicit for clarity (same as base default)
        self._min_audio_duration_ms = 1000

        # Realtime API state
        self._realtime_ws = None
        self._realtime_connected = False
        self._realtime_task = None
        self._pending_transcripts = []
        self._realtime_lock = asyncio.Lock()

        # Realtime API configuration
        self._realtime_model = "gpt-4o-transcribe"
        self._realtime_url = "wss://api.openai.com/v1/realtime"

        logger.info(f"OpenAIWhisperSTTService initialized with model: {model}, is_hands_free={is_hands_free} (PTT mode: {not is_hands_free})")
        if WEBSOCKETS_AVAILABLE:
            logger.debug("  Realtime API available for hands-free mode")
        else:
            logger.warning("  websockets not installed - Realtime API unavailable, using batch API only")
    
    # =========================================================================
    # Realtime API Methods (for hands-free streaming)
    # =========================================================================
    
    async def _connect_realtime(self):
        """Connect to OpenAI Realtime API for streaming transcription"""
        if not WEBSOCKETS_AVAILABLE:
            logger.warning("websockets not installed - cannot use Realtime API")
            return False
        
        if self._realtime_connected:
            return True
        
        try:
            # Use gpt-4o-realtime model for real-time transcription
            ws_url = f"{self._realtime_url}?model=gpt-4o-realtime-preview"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "OpenAI-Beta": "realtime=v1",
            }
            
            logger.info(f"Connecting to OpenAI Realtime API: {ws_url}")
            self._realtime_ws = await websockets.connect(ws_url, additional_headers=headers, max_size=None)
            
            # Wait for session.created
            response = await asyncio.wait_for(self._realtime_ws.recv(), timeout=10.0)
            event = json.loads(response)
            if event.get("type") != "session.created":
                logger.warning(f"Unexpected first event: {event.get('type')}")
            else:
                logger.info("✅ OpenAI Realtime API session created")
            
            # Configure session for audio input transcription only (no output)
            session_config = {
                "type": "session.update",
                "session": {
                    "modalities": ["text"],  # Only text output (transcriptions)
                    "input_audio_format": "pcm16",
                    "input_audio_transcription": {
                        "model": self._realtime_model
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                    },
                }
            }
            await self._realtime_ws.send(json.dumps(session_config))
            logger.info("✅ OpenAI Realtime API session configured for transcription")
            
            self._realtime_connected = True
            
            # Start listener task
            self._realtime_task = asyncio.create_task(self._realtime_listener())
            
            return True
        except Exception as e:
            logger.error(f"Failed to connect to OpenAI Realtime API: {e}")
            self._realtime_connected = False
            return False
    
    async def _disconnect_realtime(self):
        """Disconnect from OpenAI Realtime API"""
        if self._realtime_task:
            self._realtime_task.cancel()
            try:
                await self._realtime_task
            except asyncio.CancelledError:
                pass
            self._realtime_task = None
        
        if self._realtime_ws:
            try:
                await self._realtime_ws.close()
            except Exception as e:
                logger.debug(f"Error closing Realtime WS: {e}")
            self._realtime_ws = None
        
        self._realtime_connected = False
        logger.info("Disconnected from OpenAI Realtime API")
    
    async def _realtime_listener(self):
        """Listen for events from OpenAI Realtime API"""
        try:
            async for message in self._realtime_ws:
                event = json.loads(message)
                event_type = event.get("type", "")
                
                # Handle transcription events
                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    if transcript and transcript.strip():
                        logger.debug(f"🎤 Realtime transcription: '{transcript.strip()}'")
                        self._pending_transcripts.append(transcript.strip())
                
                elif event_type == "conversation.item.input_audio_transcription.delta":
                    # Partial transcripts (real-time)
                    delta = event.get("delta", "")
                    if delta:
                        logger.debug(f"Realtime delta: '{delta}'")
                
                elif event_type == "input_audio_buffer.speech_started":
                    logger.debug("Realtime: Speech started")
                    if self.event_queue:
                        try:
                            self.event_queue.put(('stt_hands_free_glow_on', {}), block=False)
                        except Exception:
                            pass
                
                elif event_type == "input_audio_buffer.speech_stopped":
                    logger.debug("Realtime: Speech stopped")
                    # Only emit glow off signal if in hands-free mode and not in PTT
                    if self._is_hands_free and not self._ptt_active and self.event_queue:
                        try:
                            self.event_queue.put(('stt_hands_free_glow_off', {}), block=False)
                        except Exception:
                            pass
                
                elif event_type == "error":
                    error_info = event.get("error", {})
                    logger.error(f"Realtime API error: {error_info}")
                
                elif event_type in ["session.updated", "session.created"]:
                    logger.debug(f"Realtime event: {event_type}")
                
        except asyncio.CancelledError:
            logger.debug("Realtime listener cancelled")
        except Exception as e:
            logger.error(f"Realtime listener error: {e}")
            self._realtime_connected = False
    
    async def _send_audio_realtime(self, audio_bytes: bytes):
        """Send audio chunk to Realtime API"""
        if not self._realtime_connected or not self._realtime_ws:
            return
        
        try:
            # Base64 encode the audio
            audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
            event = {
                "type": "input_audio_buffer.append",
                "audio": audio_b64
            }
            await self._realtime_ws.send(json.dumps(event))
        except Exception as e:
            logger.warning(f"Failed to send audio to Realtime API: {e}")
            self._realtime_connected = False
    
    async def _process_pending_transcripts(self, direction):
        """Process any pending transcripts from Realtime API"""
        while self._pending_transcripts:
            text = self._pending_transcripts.pop(0)
            if self._is_meaningful_text(text):
                logger.debug("[STT] PICKED UP: %s", text)
                logger.info(f"TRANSCRIPTION [OpenAI Realtime]: '{text}'")

                frame = TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                try:
                    await self.push_frame(frame, direction)
                    logger.debug(f"STT: Pushed Realtime transcription to LLM: '{text}'")
                except Exception as e:
                    logger.error(f"Error pushing transcription frame: {e}")
    
    # =========================================================================
    # PTT Buffer Processing
    # =========================================================================

    async def _process_ptt_buffer_immediate(self, direction):
        """Process PTT buffer immediately when PTT is released"""
        chunk_count = len(self._ptt_buffer_accumulator)
        total_bytes = sum(len(chunk) for chunk in self._ptt_buffer_accumulator) if self._ptt_buffer_accumulator else 0
        pre_duration_ms = (total_bytes / (16000 * 2)) * 1000 if total_bytes > 0 else 0
        logger.debug(f"STT: Processing PTT buffer ({chunk_count} chunks, ~{pre_duration_ms:.0f}ms)")
        
        if not self._ptt_buffer_accumulator:
            logger.warning("STT: _process_ptt_buffer_immediate() called but buffer is empty")
            return
        
        audio_bytes = b"".join(self._ptt_buffer_accumulator)
        self._ptt_buffer_accumulator = []
        
        if len(audio_bytes) > 0:
            sample_rate = 16000
            bytes_per_second = sample_rate * 2
            duration_ms = (len(audio_bytes) / bytes_per_second) * 1000
            
            # Pad if too short
            if duration_ms < 1000:
                silence_needed_ms = 1000 - duration_ms
                silence_bytes = int((silence_needed_ms / 1000) * bytes_per_second)
                audio_bytes = audio_bytes + b'\x00' * silence_bytes
                logger.debug(f"STT: Padded PTT audio from {duration_ms:.0f}ms to 1000ms")
            
            # Send UserStoppedSpeakingFrame to reset TTS state
            try:
                stop_frame = UserStoppedSpeakingFrame()
                await self.push_frame(stop_frame, direction)
            except Exception as e:
                logger.error(f"STT: Error sending UserStoppedSpeakingFrame: {e}")
            
            # Process with batch API
            logger.debug(f"STT: Starting batch transcription for PTT audio ({len(audio_bytes)} bytes)")
            frame_count = 0
            try:
                async for result_frame in self.run_stt(audio_bytes):
                    if self._stt_cancelled:
                        logger.debug("STT: Transcription cancelled - stopping")
                        break
                    frame_count += 1
                    if isinstance(result_frame, TranscriptionFrame):
                        logger.debug(f"STT: Pushing TranscriptionFrame: '{result_frame.text}'")
                    await self.push_frame(result_frame, direction)
                
                if frame_count == 0:
                    logger.warning("STT: run_stt() did not yield any frames!")
                else:
                    logger.debug(f"STT: PTT transcription complete ({frame_count} frames)")
            except Exception as e:
                logger.error(f"STT: Error in run_stt(): {e}", exc_info=True)
    
    async def _send_interruption(self, direction):
        """Send interruption frame to cancel TTS and LLM"""
        try:
            interruption_frame = InterruptionFrame()
            logger.debug("PTT activated - sending InterruptionFrame")
            await self.push_frame(interruption_frame, direction)
        except Exception as e:
            logger.error(f"Error sending InterruptionFrame: {e}")
    
    def set_hands_free(self, enabled: bool):
        """Set hands-free mode state"""
        old_value = self._is_hands_free
        self._is_hands_free = enabled
        logger.info(f"STT hands-free mode: {old_value} -> {enabled}")
        
        # Connect/disconnect Realtime API based on mode
        if enabled and WEBSOCKETS_AVAILABLE:
            if hasattr(self, '_event_loop') and self._event_loop and self._event_loop.is_running():
                asyncio.run_coroutine_threadsafe(self._connect_realtime(), self._event_loop)
        elif not enabled and self._realtime_connected:
            if hasattr(self, '_event_loop') and self._event_loop and self._event_loop.is_running():
                asyncio.run_coroutine_threadsafe(self._disconnect_realtime(), self._event_loop)
    
    def set_dictating(self, enabled: bool):
        """Set dictation mode state"""
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
        """Process audio bytes using OpenAI Whisper batch API"""
        self._stt_cancelled = False
        
        sample_rate = 16000
        bytes_per_second = sample_rate * 2
        duration_ms = (len(audio) / bytes_per_second) * 1000
        
        if duration_ms < self._min_audio_duration_ms:
            silence_needed_ms = self._min_audio_duration_ms - duration_ms
            silence_bytes = int((silence_needed_ms / 1000) * bytes_per_second)
            audio = audio + b'\x00' * silence_bytes

        if self._stt_cancelled:
            return

        loop = asyncio.get_running_loop()
        try:
            audio_np = np.frombuffer(audio, dtype=np.int16)
            
            # Create WAV file in memory
            import wave
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
                    transcript = self.client.audio.transcriptions.create(
                        model=self.model,
                        file=("audio.wav", wav_buffer.read(), "audio/wav"),
                        language="en"
                    )
                    return transcript.text if transcript else None
                except Exception as e:
                    logger.error(f"OpenAI Whisper API error: {e}")
                    raise
            
            text = await loop.run_in_executor(None, transcribe)
            
            if self._stt_cancelled:
                return
            
            if text and text.strip():
                text = text.strip()
                logger.debug("[STT] PICKED UP: %s", text)
                logger.info(f"TRANSCRIPTION [OpenAI {self.model}]: '{text}'")

                if self._is_meaningful_text(text):
                    frame = TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                    yield frame
                else:
                    logger.warning(f"🚫 STT Rejected (artifact/filler): '{text}'")
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
        if self._pending_ptt_process and self._ptt_buffer_accumulator:
            self._pending_ptt_process = False
            await self._process_ptt_buffer_immediate(direction)
        
        # Process pending Realtime transcripts
        if self._is_hands_free and self._pending_transcripts:
            await self._process_pending_transcripts(direction)
        
        # Handle StartFrame - initialize Realtime connection for hands-free
        if isinstance(frame, StartFrame):
            self._event_loop = asyncio.get_running_loop()
            if self._is_hands_free and WEBSOCKETS_AVAILABLE:
                asyncio.create_task(self._connect_realtime())
            await super().process_frame(frame, direction)
            return
        
        # Handle EndFrame
        if isinstance(frame, EndFrame):
            if self._realtime_connected:
                await self._disconnect_realtime()
            await super().process_frame(frame, direction)
            return
        
        # Handle CancelFrame
        if isinstance(frame, CancelFrame):
            self._stt_cancelled = True
            if self._current_stt_task:
                self._current_stt_task.cancel()
            if self._realtime_connected:
                await self._disconnect_realtime()
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
                    self._pending_bargein_check = True
                    self._bargein_consecutive_count = 0
                    return

                self._user_speaking = True
                self._audio_buffer = list(self._pre_buffer)
                # Send pre-buffered audio to Realtime API if connected
                if self._realtime_connected:
                    for chunk in self._pre_buffer:
                        await self._send_audio_realtime(chunk)
                pre_buf_ms = len(self._pre_buffer) * 20
                self._pre_buffer.clear()
                logger.debug(f"STT: User started speaking, seeded {pre_buf_ms}ms pre-buffer")

                # Trigger a proper Pipecat interruption via PipelineTask.
                # push_interruption_task_frame_and_wait() sends InterruptionTaskFrame
                # upstream to PipelineTask, which broadcasts InterruptionFrame downstream
                # through the ENTIRE pipeline. This is the correct mechanism — it works
                # regardless of _allow_interruptions on the input transport.
                logger.info("STT: Barge-in confirmed — triggering pipeline interruption")
                await self.push_interruption_task_frame_and_wait()

                # Cancel the welcome message task if still running
                if self._cancel_welcome_callback:
                    self._cancel_welcome_callback()
                    logger.info("STT: Cancelled welcome task via callback (barge-in)")

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
                
                # For hands-free: If using Realtime API, transcripts come via WebSocket
                # If not connected, fall back to batch processing
                if self._audio_buffer:
                    if self._is_hands_free and self._realtime_connected:
                        # Audio was already streamed to Realtime API
                        # Transcripts will arrive via _realtime_listener
                        logger.debug("STT: Audio was streamed to Realtime API - awaiting transcription")
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
        
        # Handle audio frames (transport emits InputAudioRawFrame; both needed for PTT)
        if isinstance(frame, (AudioRawFrame, InputAudioRawFrame)):
            if self._ptt_active:
                # PTT mode: accumulate for batch processing
                self._ptt_buffer_accumulator.append(frame.audio)
                if len(self._ptt_buffer_accumulator) % 50 == 0:
                    total_bytes = sum(len(c) for c in self._ptt_buffer_accumulator)
                    duration_ms = (total_bytes / (16000 * 2)) * 1000
                    logger.debug(f"STT: PTT buffer: {len(self._ptt_buffer_accumulator)} chunks, ~{duration_ms:.0f}ms")
            elif not self._ptt_active and self._ptt_buffer_accumulator:
                # PTT just released - process buffer
                await self._process_ptt_buffer_immediate(direction)
            elif self._is_hands_free and self._user_speaking:
                # Hands-free mode: stream to Realtime API or buffer for batch
                self._audio_buffer.append(frame.audio)
                if self._realtime_connected:
                    await self._send_audio_realtime(frame.audio)
                # Check if TTS started playing again while user is still talking
                await self._check_continuous_speech_interruption(frame.audio, direction)
            elif self._is_dictating and self._user_speaking:
                # Dictation mode: buffer for batch
                self._audio_buffer.append(frame.audio)
                await self._check_continuous_speech_interruption(frame.audio, direction)
            elif (self._is_hands_free or self._is_dictating):
                # Not yet speaking: maintain rolling pre-buffer for speech onset capture
                self._pre_buffer.append(frame.audio)
                await self._check_pending_bargein(frame.audio, direction)
            return
        
        # Pass through other frames
        await super().process_frame(frame, direction)
    
    def get_sample_rate(self) -> int:
        """Return the required sample rate (16kHz)"""
        return 16000
    
    def transcribe_file(self, audio_file_path: str) -> Optional[str]:
        """Transcribe an audio file using the OpenAI Whisper API."""
        try:
            logger.info(f"OpenAIWhisperSTTService: Transcribing file {audio_file_path}")
            
            with open(audio_file_path, 'rb') as audio_file:
                transcript = self.client.audio.transcriptions.create(
                    model=self.model,
                    file=audio_file,
                    language="en"
                )
                text = transcript.text if transcript else None
                logger.info(f"OpenAIWhisperSTTService: Transcription complete ({len(text) if text else 0} chars)")
                return text
        except Exception as e:
            logger.error(f"OpenAIWhisperSTTService: Error transcribing file: {e}", exc_info=True)
            return None
