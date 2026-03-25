import asyncio
import logging
import re
import numpy as np
import os
import io
import time

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE, TTSService,
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame, TTSStartedFrame, TTSStoppedFrame, ErrorFrame, StartFrame, EndFrame, CancelFrame, InterruptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame, AudioRawFrame, OutputAudioRawFrame,
    ElevenLabs, ELEVENLABS_AVAILABLE,
    sf, SOUNDFILE_AVAILABLE,
    AudioSegment, PYDUB_AVAILABLE
)

logger = logging.getLogger(__name__)


class ElevenLabsTTSService(TTSService):
    """ElevenLabs-based TTS service using Pipecat"""
    
    def __init__(self, api_key: str, voice_id: str, voice_name: str = None, stt_service=None, playback_speed: float = 1.0, event_queue=None, speech_volume: int = 100, stability: float = 0.5, similarity_boost: float = 0.6, style: float = 0.25, use_speaker_boost: bool = True, on_quota_exceeded=None, **kwargs):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for ElevenLabsTTSService")
        if not ELEVENLABS_AVAILABLE:
            raise ImportError("elevenlabs library is required for ElevenLabsTTSService")
        if not SOUNDFILE_AVAILABLE and not PYDUB_AVAILABLE:
            raise ImportError("soundfile or pydub is required for ElevenLabsTTSService")
        
        # Initialize TTSService
        super().__init__(**kwargs)
        
        self._on_quota_exceeded = on_quota_exceeded  # Callback returning fallback TTS (e.g. Kokoro) when quota exceeded
        self.client = ElevenLabs(api_key=api_key)
        self.voice_id = voice_id
        self.voice_name = voice_name or voice_id
        self.playback_speed = playback_speed
        self._text_buffer = ""
        self._frame_id_counter = 10000
        self._stt_service = stt_service
        self._cancelled = False
        self._is_hands_free = False  # Track hands-free mode state
        self._ptt_active = False  # Track PTT state
        self.event_queue = event_queue  # Queue to send events back to main process
        self._tts_session_active = False  # Track if we're in an active TTS session (between LLMFullResponseStartFrame and LLMFullResponseEndFrame)
        self._total_audio_duration = 0.0
        self._tts_started_emitted = False  # Track if we've emitted tts_started for this session  # Accumulate total audio duration for the entire response
        self._processed_sentences = set()  # Track processed sentences (normalized text) to prevent duplicates
        # Convert speech_volume (0-100) to multiplier (0.0-1.0)
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        # ElevenLabs voice_settings (0-1; updated live via set_elevenlabs_voice_settings)
        self._stability = max(0.0, min(1.0, stability))
        self._similarity_boost = max(0.0, min(1.0, similarity_boost))
        self._style = max(0.0, min(1.0, style))
        self._use_speaker_boost = bool(use_speaker_boost)
        
        logger.info(f"ElevenLabsTTSService initialized with voice: {self.voice_name} (ID: {self.voice_id}), volume: {speech_volume}%, stability: {self._stability}, similarity: {self._similarity_boost}, style: {self._style}, speaker_boost: {self._use_speaker_boost}")
    
    def set_playback_speed(self, speed: float):
        """Update playback speed in real-time"""
        self.playback_speed = speed
        logger.debug(f"ElevenLabs TTS playback speed updated to {speed:.1f}x")
    
    def set_speech_volume(self, volume: int):
        """Update speech volume in real-time (0-100)"""
        # Convert volume (0-100) to multiplier (0.0-1.0)
        self._speech_volume = max(0.0, min(1.0, volume / 100.0))
        logger.debug(f"ElevenLabs TTS speech volume updated to {volume}% (multiplier: {self._speech_volume:.2f})")
    
    def set_hands_free(self, enabled: bool):
        """Set hands-free mode state - affects how CancelFrame/InterruptionFrame are handled"""
        self._is_hands_free = enabled
        logger.debug(f"TTS hands-free mode: {enabled}")
    
    def set_ptt_active(self, active: bool):
        """
        Set PTT state.
        """
        self._ptt_active = active
        logger.debug(f"TTS: set_ptt_active(active={active})")

    def set_elevenlabs_voice_settings(self, stability: float = None, similarity_boost: float = None, style: float = None, use_speaker_boost: bool = None):
        """Update ElevenLabs voice_settings in real time (0-1 for floats). Omitted args are left unchanged."""
        if stability is not None:
            self._stability = max(0.0, min(1.0, stability))
        if similarity_boost is not None:
            self._similarity_boost = max(0.0, min(1.0, similarity_boost))
        if style is not None:
            self._style = max(0.0, min(1.0, style))
        if use_speaker_boost is not None:
            self._use_speaker_boost = bool(use_speaker_boost)
        logger.debug(f"ElevenLabs voice_settings updated: stability={self._stability}, similarity_boost={self._similarity_boost}, style={self._style}, use_speaker_boost={self._use_speaker_boost}")

    def _extract_complete_sentences(self, text: str):
        """Extract complete sentences from text buffer"""
        sentences = []
        remaining = text
        
        # Require at least one word character before terminal punctuation to avoid matching
        # single-char fragments like "g." from mid-stream tokens (e.g. "ing." split mid-word).
        while True:
            match = re.search(r'([^\.!\?]*\w[^\.!\?]*[\.!\?]+)(\s+|$)', remaining)
            if not match:
                break
            
            sentence = match.group(1).strip()
            if sentence:
                sentences.append(sentence)
            
            remaining = remaining[len(match.group(0)):]
            
        return sentences, remaining

    def _generate_audio(self, text: str):
        """Generate audio from text using ElevenLabs API with retry logic for transient errors"""
        max_retries = 2
        retry_delay = 0.5
        
        for attempt in range(max_retries + 1):
            try:
                # ElevenLabs API supports 'speed' parameter (range 0.7-1.2). Default 0.90.
                # Generate audio using text_to_speech.convert
                # Default voice settings: stability 50%, similarity 60%, speed 0.90
                default_speed = 0.90
                api_speed = max(0.7, min(1.2, float(self.playback_speed)))
                if api_speed == 1.0:
                    api_speed = default_speed
                audio_stream = self.client.text_to_speech.convert(
                    text=text,
                    voice_id=self.voice_id,
                    model_id="eleven_multilingual_v2",
                    output_format="mp3_44100_128",
                    voice_settings={
                        "stability": self._stability,
                        "similarity_boost": self._similarity_boost,
                        "style": self._style,
                        "use_speaker_boost": self._use_speaker_boost,
                        "speed": api_speed
                    }
                )
                
                # Collect all audio chunks into bytes
                audio_bytes = b""
                for chunk in audio_stream:
                    audio_bytes += chunk
                
                # Convert MP3 bytes to numpy array
                # Prefer pydub for MP3 files as soundfile doesn't support MP3 properly
                if PYDUB_AVAILABLE:
                    # Use pydub to read from bytes (better MP3 support)
                    audio_segment = AudioSegment.from_mp3(io.BytesIO(audio_bytes))
                    # Convert to mono if stereo
                    if audio_segment.channels > 1:
                        audio_segment = audio_segment.set_channels(1)
                    # Get sample rate
                    sample_rate = audio_segment.frame_rate
                    # Convert to numpy array and normalize to [-1, 1]
                    audio_data = np.array(audio_segment.get_array_of_samples(), dtype=np.float32)
                    audio_data = audio_data / 32768.0  # Normalize from int16 range
                elif SOUNDFILE_AVAILABLE:
                    # Try soundfile as fallback (may not work for MP3)
                    try:
                        # Don't specify format parameter - let soundfile auto-detect
                        # But this will likely fail for MP3
                        audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))
                    except (TypeError, RuntimeError, Exception) as e:
                        logger.error(f"soundfile cannot read MP3: {e}. Please install pydub for MP3 support.")
                        raise ImportError("soundfile cannot read MP3 files. Please install pydub: pip install pydub")
                else:
                    raise ImportError("Neither soundfile nor pydub is available. Please install pydub: pip install pydub")
                
                # Convert to mono if stereo
                if audio_data.ndim > 1:
                    audio_data = np.mean(audio_data, axis=1)
                
                # Ensure float32 and normalize to [-1, 1] range
                if audio_data.dtype != np.float32:
                    if audio_data.dtype == np.int16:
                        audio_data = audio_data.astype(np.float32) / 32768.0
                    elif audio_data.dtype == np.int32:
                        audio_data = audio_data.astype(np.float32) / 2147483648.0
                    else:
                        audio_data = audio_data.astype(np.float32)
                
                return audio_data, sample_rate
                
            except Exception as e:
                # Check if it's a quota exceeded error from ElevenLabs API
                error_str = str(e)
                error_body = None
                status_code = None
                
                # Try to extract error details from ElevenLabs ApiError
                if hasattr(e, 'body'):
                    error_body = e.body
                if hasattr(e, 'status_code'):
                    status_code = e.status_code
                
                # Check for quota exceeded in various formats
                is_quota_exceeded = False
                quota_message = None
                
                if error_body and isinstance(error_body, dict):
                    detail = error_body.get('detail', {})
                    if isinstance(detail, dict):
                        status = detail.get('status', '')
                        if status == 'quota_exceeded':
                            is_quota_exceeded = True
                            quota_message = detail.get('message', 'ElevenLabs quota exceeded')
                
                # Also check error string for quota messages
                if 'quota_exceeded' in error_str.lower() or ('quota' in error_str.lower() and 'exceeded' in error_str.lower()):
                    is_quota_exceeded = True
                    if not quota_message:
                        # Extract message from error string if available
                        if 'message' in error_str:
                            try:
                                import json
                                # Try to parse JSON from error string
                                if 'body:' in error_str:
                                    body_start = error_str.find("body:") + 5
                                    body_end = error_str.find("}", body_start) + 1
                                    if body_end > body_start:
                                        body_json = error_str[body_start:body_end]
                                        parsed = json.loads(body_json)
                                        detail = parsed.get('detail', {})
                                        if isinstance(detail, dict):
                                            quota_message = detail.get('message', 'ElevenLabs quota exceeded')
                            except (json.JSONDecodeError, ValueError, KeyError):
                                pass
                
                if is_quota_exceeded:
                    user_message = quota_message or 'ElevenLabs quota exceeded. Please upgrade your plan or switch to Kokoro (Offline) TTS in Settings > General > Voice Setup.'
                    logger.error(f"ElevenLabs quota exceeded: {quota_message or error_str}")
                    # Emit user-friendly error message via event queue
                    if self.event_queue:
                        try:
                            self.event_queue.put(('tts_error', {
                                'provider': 'ElevenLabs',
                                'error_type': 'quota_exceeded',
                                'message': user_message
                            }), block=False)
                        except Exception:
                            pass
                    # Raise a more specific exception - don't retry quota errors
                    raise ValueError(f"ElevenLabs quota exceeded: {user_message}")
                
                # Check if this is a retryable error (transient network/API issues)
                is_retryable = False
                error_str_lower = error_str.lower()
                
                # Retry on: network errors, rate limits (429), server errors (5xx), timeout errors
                if any(keyword in error_str_lower for keyword in ['timeout', 'connection', 'network', 'rate limit', '429', '500', '502', '503', '504']):
                    is_retryable = True
                elif hasattr(e, 'status_code'):
                    # Retry on 429 (rate limit) and 5xx (server errors)
                    if e.status_code == 429 or (500 <= e.status_code < 600):
                        is_retryable = True
                
                if is_retryable and attempt < max_retries:
                    logger.warning(f"ElevenLabs API error (attempt {attempt + 1}/{max_retries + 1}): {e}. Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
                else:
                    # Not retryable or out of retries
                    logger.error(f"Error generating ElevenLabs audio (attempt {attempt + 1}/{max_retries + 1}): {e}", exc_info=True)
                    raise

    async def run_tts(self, text: str):
        """Process text and yield audio frames"""
        if self._cancelled:
            logger.debug("TTS: run_tts() called but cancelled - returning")
            return

        # Delegate to Kokoro when quota fallback is active (pipeline swap may fail, so we delegate in-process)
        fallback = getattr(self, '_fallback_tts', None)
        if fallback and fallback is not self:
            logger.debug("TTS: Delegating to Kokoro fallback (quota exceeded)")
            if hasattr(fallback, '_cancelled'):
                fallback._cancelled = self._cancelled
            if hasattr(fallback, '_is_hands_free'):
                fallback._is_hands_free = self._is_hands_free
            yield TTSStartedFrame()
            async for frame in fallback.run_tts(text):
                if isinstance(frame, TTSStartedFrame):
                    continue
                yield frame
            return

        yield TTSStartedFrame()
        
        # Check again after yielding start frame
        if self._cancelled:
            return
        
        audio_duration_seconds = 0.0
        
        try:
            loop = asyncio.get_running_loop()
            # Run in executor to avoid blocking
            audio, sample_rate = await loop.run_in_executor(
                None, 
                lambda: self._generate_audio(text)
            )
            
            if self._cancelled:
                return

            if audio is not None and len(audio) > 0:
                # Verify sample rate matches expected (44100 Hz)
                if sample_rate != 44100:
                    logger.warning(f"ElevenLabs returned unexpected sample rate: {sample_rate} Hz (expected 44100 Hz). Resampling may cause glitches.")
                
                # Apply volume
                # This prevents per-chunk inconsistencies and reduces conversion artifacts
                audio_scaled = audio * self._speech_volume
                
                # Convert to int16 with proper clamping to prevent clipping
                audio_int16 = np.clip(audio_scaled * 32767.0, -32768, 32767).astype(np.int16)
                
                # Calculate chunk size aligned to sample boundaries (20ms chunks)
                # Ensure chunk size is even (2 bytes per sample for int16)
                samples_per_chunk = int(sample_rate * 0.02)  # 20ms chunks
                bytes_per_sample = 2  # int16 = 2 bytes
                chunk_size = samples_per_chunk * bytes_per_sample
                # Ensure chunk size is even and at least 320 bytes
                chunk_size = max(320, chunk_size - (chunk_size % 2))
                
                FrameClass = OutputAudioRawFrame if OutputAudioRawFrame else AudioRawFrame
                
                frames_yielded = 0
                for i in range(0, len(audio_int16) * bytes_per_sample, chunk_size):
                    if self._cancelled:
                        break
                    
                    # Calculate sample indices
                    sample_start = i // bytes_per_sample
                    sample_end = min(sample_start + samples_per_chunk, len(audio_int16))
                    
                    if sample_start >= len(audio_int16):
                        break
                    
                    # Extract chunk directly from numpy array to avoid conversion artifacts
                    chunk_samples = audio_int16[sample_start:sample_end]
                    
                    if len(chunk_samples) > 0:
                        # Emit tts_started when we yield the FIRST audio frame
                        if frames_yielded == 0 and self._tts_session_active and not self._tts_started_emitted:
                            self._tts_started_emitted = True
                            if self.event_queue:
                                try:
                                    self.event_queue.put(('tts_started', {}), block=False)
                                except Exception:
                                    pass
                        
                        # Convert to bytes only once, at the end
                        chunk = chunk_samples.tobytes()
                        
                        frame = FrameClass(
                            audio=chunk,
                            sample_rate=sample_rate,
                            num_channels=1
                        )
                        
                        # Compatibility attributes
                        if not hasattr(frame, 'id') or frame.id is None:
                            frame.id = self._frame_id_counter
                            self._frame_id_counter += 1
                        if not hasattr(frame, 'transport_destination'):
                            frame.transport_destination = None
                        if not hasattr(frame, 'pts'):
                            frame.pts = None
                        
                        yield frame
                        frames_yielded += 1
                
                # Calculate audio duration from samples
                audio_duration_seconds = len(audio_int16) / sample_rate if sample_rate > 0 else 0
            else:
                audio_duration_seconds = 0
        except ValueError as e:
            # Handle quota exceeded and other user-friendly errors
            error_str = str(e)
            if 'quota exceeded' in error_str.lower():
                logger.error(f"ElevenLabs TTS quota exceeded: {error_str}")
                # Emit user-friendly error message for main app UI
                if self.event_queue:
                    try:
                        self.event_queue.put(('tts_error', {
                            'provider': 'ElevenLabs',
                            'error_type': 'quota_exceeded',
                            'message': error_str
                        }), block=False)
                    except Exception:
                        pass
                # Fallback to Kokoro when callback is provided (avoids looping on repeated failures)
                if self._on_quota_exceeded:
                    try:
                        fallback_tts = self._on_quota_exceeded()
                        if fallback_tts and fallback_tts is not self:
                            logger.debug("ElevenLabs quota exceeded: falling back to Kokoro for this text")
                            # Set delegation so subsequent sentences use Kokoro without re-calling ElevenLabs
                            self._fallback_tts = fallback_tts
                            # Propagate cancellation/hands-free state to fallback
                            if hasattr(fallback_tts, '_cancelled'):
                                fallback_tts._cancelled = self._cancelled
                            if hasattr(fallback_tts, '_is_hands_free'):
                                fallback_tts._is_hands_free = self._is_hands_free
                            async for frame in fallback_tts.run_tts(text):
                                if isinstance(frame, TTSStartedFrame):
                                    continue  # Already yielded at start of run_tts
                                yield frame
                            return
                    except Exception as fb_err:
                        logger.warning(f"Quota fallback to Kokoro failed: {fb_err}")
            yield ErrorFrame(error=error_str)
            audio_duration_seconds = 0
        except Exception as e:
            logger.error(f"TTS Error: {e}", exc_info=True)
            yield ErrorFrame(error=str(e))
            audio_duration_seconds = 0
        finally:
            # Add delay to ensure TTSStoppedFrame is sent after audio playback completes
            # This prevents premature player closure and audio glitches when audio is still in buffers
            # ElevenLabs needs longer buffer delays due to API latency and streaming nature
            if audio_duration_seconds > 0:
                # Add delay proportional to audio duration - longer for ElevenLabs to account for API latency
                # Minimum 200ms, maximum 800ms to handle buffer processing and API streaming delays
                buffer_delay = max(0.2, min(0.8, audio_duration_seconds * 0.15))
                logger.debug(f"TTS: Adding {buffer_delay:.3f}s delay before TTSStoppedFrame to ensure playback completion")
                try:
                    await asyncio.sleep(buffer_delay)
                except Exception as e:
                    logger.warning(f"TTS: Could not add delay before TTSStoppedFrame: {e}")
            
            yield TTSStoppedFrame()
            self._total_audio_duration += audio_duration_seconds

    async def process_frame(self, frame, direction):
        """Process TextFrame frames and generate audio"""
        
        frame_type = type(frame).__name__
        if frame_type == 'TextFrame':
            logger.debug(f"TTS: TextFrame: '{frame.text[:60]}' (buf={len(self._text_buffer)}, cancelled={self._cancelled})")
        elif frame_type == 'InterruptionFrame':
            logger.debug(f"TTS: InterruptionFrame (hands_free={self._is_hands_free})")
        elif frame_type not in ['AudioRawFrame', 'InputAudioRawFrame', 'UserSpeakingFrame']:
            logger.debug(f"TTS: Frame: {frame_type}")
        
        if isinstance(frame, StartFrame):
            logger.debug(f"TTS: StartFrame received")
        
        # CancelFrame handling:
        # - Hands-free mode: Pass through (needed for proper interruption)
        # - Push-to-talk mode: STOP here (transport breaks if it receives these)
        if isinstance(frame, CancelFrame):
            # CancelFrame is handled for backwards compatibility; PTT uses InterruptionFrame
            self._cancelled = True
            self._text_buffer = ""
            logger.warning(f"TTS: CancelFrame received - cancelling TTS (hands_free={self._is_hands_free}, ptt_active={self._ptt_active})")
            
            # Only pass CancelFrame through in hands-free mode
            if self._is_hands_free:
                logger.debug("TTS: Passing CancelFrame through (hands-free mode)")
                await self.push_frame(frame, direction)
            return
        
        # Handle InterruptionFrame - KILLS AUDIO IMMEDIATELY
        # InterruptionFrame always interrupts, regardless of mode.
        if isinstance(frame, InterruptionFrame):
            logger.debug("TTS: InterruptionFrame received - stopping playback")
            self._cancelled = True
            self._text_buffer = ""
            
            # Emit tts_stopped on interrupt to close player
            if self._tts_session_active:
                self._tts_session_active = False
                self._total_audio_duration = 0.0
                self._tts_started_emitted = False
            
            if self.event_queue:
                try:
                    self.event_queue.put(('tts_stopped', {'duration': 0.0}), block=False)
                except Exception as e:
                    logger.debug(f"Could not emit tts_stopped event (non-blocking): {e}")
            
            # Pass InterruptionFrame through to transport
            await self.push_frame(frame, direction)
            return
        
        # Only pass it through in hands-free mode
        if isinstance(frame, UserStartedSpeakingFrame):
            if self._is_hands_free:
                await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserStoppedSpeakingFrame):
            # NOTE: Do NOT reset _cancelled here - only reset on LLMFullResponseStartFrame
            logger.debug("TTS: User stopped speaking - NOT resetting cancelled state (will reset on new LLM response)")
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            logger.debug(f"TTS: Received TextFrame: '{frame.text[:100]}...' (buffer_len={len(self._text_buffer)}, cancelled={self._cancelled}, ptt_active={self._ptt_active})")
            
            # CRITICAL: If cancelled (e.g., by CancelFrame from PTT), DROP all TextFrames
            # Do NOT reset cancelled state - only LLMFullResponseStartFrame should do that
            if self._cancelled:
                logger.debug("TTS: TextFrame dropped - TTS is cancelled, not processing")
                # Don't process, don't accumulate, just drop it
                return
            
            # Accumulate text and process incrementally
            self._text_buffer += frame.text
            
            # CRITICAL: Check cancellation before extracting sentences
            # This prevents processing new sentences if we're already cancelled
            if self._cancelled:
                logger.warning("TTS: Cancelled before extracting sentences - clearing buffer and dropping TextFrame")
                self._text_buffer = ""
                return
            
            # Extract complete sentences and process them immediately
            sentences, remaining = self._extract_complete_sentences(self._text_buffer)
            self._text_buffer = remaining
            
            if sentences:
                logger.debug(f"TTS: Processing {len(sentences)} sentence(s) from TextFrame")
                for sentence in sentences:
                    # CRITICAL: Check cancellation before processing each sentence
                    # This ensures we stop immediately if cancelled during sentence processing
                    if self._cancelled:
                        logger.warning(f"TTS: Cancelled before processing sentence '{sentence[:50]}...' - stopping sentence loop")
                        # Clear remaining buffer since we're cancelling
                        self._text_buffer = ""
                        break
                    
                    # CRITICAL: Prevent duplicate processing of the same sentence
                    normalized_sentence = sentence.strip().lower()
                    if normalized_sentence in self._processed_sentences:
                        logger.warning(f"TTS: Skipping duplicate sentence: '{sentence[:50]}...' (already processed)")
                        continue
                    
                    # Only skip when current is SUBSET of processed (redundant). Do NOT skip when
                    # processed is subset of current - that drops longer sentences (streaming).
                    is_duplicate = False
                    if len(normalized_sentence) > 20:
                        for processed in self._processed_sentences:
                            if len(processed) > 20:
                                if normalized_sentence in processed:
                                    logger.warning(f"TTS: Skipping duplicate (subset): '{sentence[:50]}...'")
                                    is_duplicate = True
                                    break
                                words1 = set(normalized_sentence.split())
                                words2 = set(processed.split())
                                if len(words1) > 4 and len(words2) > 4:
                                    overlap = len(words1 & words2)
                                    total_unique = len(words1 | words2)
                                    if total_unique > 0 and overlap / total_unique > 0.9:
                                        logger.warning(f"TTS: Skipping duplicate (word overlap {overlap}/{total_unique}): '{sentence[:50]}...'")
                                        is_duplicate = True
                                        break
                    
                    if is_duplicate:
                        continue
                    
                    # Mark sentence as processed and limit set size to prevent memory growth
                    self._processed_sentences.add(normalized_sentence)
                    if len(self._processed_sentences) > 100:
                        self._processed_sentences = set(list(self._processed_sentences)[-50:])
                    
                    logger.debug(f"TTS: Synthesizing: '{sentence[:60]}'")
                    frame_count = 0
                    audio_frame_count = 0
                    async for audio_frame in self.run_tts(sentence):
                        # CRITICAL: Check cancellation BEFORE processing each frame
                        if self._cancelled:
                            logger.warning("TTS: Generation cancelled - stopping immediately")
                            break
                        
                        # CRITICAL: Check cancellation BEFORE pushing frame
                        if self._cancelled:
                            logger.debug("TTS: Cancelled before pushing frame - dropping")
                            break
                        
                        frame_count += 1
                        
                        # Debug: Log frame details before pushing
                        if isinstance(audio_frame, AudioRawFrame) or (OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)):
                            if audio_frame_count == 0:
                                logger.debug(f"TTS: About to push first audio frame: {len(audio_frame.audio)} bytes, sample_rate={audio_frame.sample_rate}, direction={direction}")
                        
                        try:
                            # Final check before pushing
                            if self._cancelled:
                                logger.debug("TTS: Cancelled right before push - dropping frame")
                                break
                            
                            # CRITICAL: Apply speech volume
                            if isinstance(audio_frame, AudioRawFrame) or (OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)):
                                # Apply speech volume
                                audio_array = np.frombuffer(audio_frame.audio, dtype=np.int16)
                                audio_array = (audio_array * self._speech_volume).astype(np.int16)
                                
                                audio_frame.audio = audio_array.tobytes()
                            
                            await self.push_frame(audio_frame, direction)
                            # Count actual audio frames (not TTSStartedFrame/TTSStoppedFrame)
                            if isinstance(audio_frame, AudioRawFrame) or (OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)):
                                audio_frame_count += 1
                                if audio_frame_count == 1:
                                    logger.debug(f"TTS: First audio frame pushed successfully to direction={direction}")
                                elif audio_frame_count % 50 == 0:
                                    logger.debug(f"TTS: Pushed {audio_frame_count} audio frames so far...")
                        except Exception as e:
                            logger.error(f"TTS: Error pushing frame: {e}", exc_info=True)
                            break
                    
                    if audio_frame_count > 0:
                        logger.debug(f"TTS: {audio_frame_count} audio frames for sentence")
                    else:
                        logger.warning(f"TTS: No audio frames generated for sentence: '{sentence[:50]}...'")
            else:
                logger.debug(f"TTS: No complete sentences yet, buffering (buffer: '{self._text_buffer[:50]}...')")
            
            # CRITICAL: Do NOT push TextFrame through - TTS services consume TextFrames and generate audio
            # Pushing TextFrames through would cause them to be processed again downstream, leading to repetition
            # TextFrames are consumed here and converted to AudioRawFrames, which are what get pushed downstream
            return
        
        if isinstance(frame, LLMFullResponseStartFrame):
            logger.debug("TTS: LLMFullResponseStartFrame - resetting TTS state for new response")
            self._text_buffer = ""
            self._cancelled = False  # Reset cancelled state to allow new audio generation
            self._processed_sentences.clear()  # Clear processed sentences for new response
            
            # Start new TTS session - reset duration accumulator and mark session as active
            self._tts_session_active = True
            self._total_audio_duration = 0.0
            self._tts_started_emitted = False  # Reset flag - will emit when first audio frame is yielded
            
            # DON'T emit tts_started here - wait until we actually start playing audio
            # This prevents opening the player before audio actually plays
            
            await self.push_frame(frame, direction)
            return
            
        elif isinstance(frame, LLMFullResponseEndFrame):
            logger.debug(f"TTS: LLMFullResponseEndFrame received - processing remaining buffer: '{self._text_buffer[:50]}...'")
            # Process any remaining text
            if self._text_buffer.strip() and not self._cancelled:
                text = self._text_buffer.strip()
                self._text_buffer = ""
                logger.debug(f"TTS: Processing remaining text: '{text[:50]}...'")
                audio_frame_count = 0
                frame_count = 0
                async for audio_frame in self.run_tts(text):
                    if self._cancelled:
                        logger.debug("TTS: Generation cancelled during remaining text processing")
                        break
                    frame_count += 1
                    
                    # Debug: Log frame details before pushing (same as regular TextFrame processing)
                    if isinstance(audio_frame, AudioRawFrame) or (OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)):
                        if audio_frame_count == 0:
                            logger.debug(f"TTS: About to push first audio frame from remaining text: {len(audio_frame.audio)} bytes, sample_rate={audio_frame.sample_rate}, direction={direction}")
                    
                    try:
                        # CRITICAL: Apply speech volume
                        if isinstance(audio_frame, AudioRawFrame) or (OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)):
                            # Apply speech volume
                            audio_array = np.frombuffer(audio_frame.audio, dtype=np.int16)
                            audio_array = (audio_array * self._speech_volume).astype(np.int16)
                            
                            audio_frame.audio = audio_array.tobytes()
                        
                        await self.push_frame(audio_frame, direction)
                        # Count actual audio frames (not TTSStartedFrame/TTSStoppedFrame)
                        if isinstance(audio_frame, AudioRawFrame) or (OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)):
                            audio_frame_count += 1
                            if audio_frame_count == 1:
                                logger.debug(f"TTS: First audio frame from remaining text pushed successfully to direction={direction}")
                            elif audio_frame_count % 50 == 0:
                                logger.debug(f"TTS: Pushed {audio_frame_count} audio frames from remaining text so far...")
                    except Exception as e:
                        logger.error(f"TTS: Error pushing frame from remaining text: {e}", exc_info=True)
                        break
                
                if audio_frame_count > 0:
                    logger.debug(f"TTS: {audio_frame_count} audio frames from remaining text")
                else:
                    logger.warning(f"TTS: No audio frames generated from remaining text")
            else:
                logger.debug("TTS: No remaining text to process (buffer empty or cancelled)")
            
            # End TTS session - emit stop event with total accumulated duration
            if self._tts_session_active:
                total_duration = self._total_audio_duration
                self._tts_session_active = False
                self._total_audio_duration = 0.0
                
                # Emit TTS stopped event to main process with total audio duration
                # The GUI will wait for this duration before hiding the player window
                if self.event_queue:
                    try:
                        self.event_queue.put(('tts_stopped', {'duration': total_duration}), block=False)
                        logger.debug(f"TTS: Sent tts_stopped event to main process (total duration: {total_duration:.2f}s)")
                    except Exception as e:
                        logger.debug(f"Could not emit tts_stopped event (non-blocking): {e}")
            
            await self.push_frame(frame, direction)
            return
        
        # Pass through any other frames
        # IMPORTANT: We must NOT pass TextFrame to super() if we handled it, 
        # otherwise base TTSService might also try to process it (causing double speech).
        # But we MUST pass other frames (like StartFrame, EndFrame) so FrameProcessor logic works.
        
        if not isinstance(frame, TextFrame):
            await super().process_frame(frame, direction)
            
            # Propagate system frames
            # NOTE: CancelFrame and InterruptionFrame are handled above with context-aware logic
            # (passed through in hands-free mode, stopped in push-to-talk mode)
            if isinstance(frame, (StartFrame, EndFrame)):
                if isinstance(frame, StartFrame):
                    logger.debug(f"TTS: Processed StartFrame (super called). New _started={getattr(self, '_FrameProcessor__started', 'UNKNOWN')}")
                    logger.debug(f"TTS: Pushing StartFrame downstream. Direction: {direction}")
                await self.push_frame(frame, direction)
    
    def get_sample_rate(self) -> int:
        """Return the output sample rate for ElevenLabs (44.1kHz)"""
        return 44100

