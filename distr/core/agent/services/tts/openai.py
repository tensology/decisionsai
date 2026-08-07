import asyncio
import logging
import time
import numpy as np
import io

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE, TTSService,
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame, TTSStartedFrame, TTSStoppedFrame, ErrorFrame, StartFrame, EndFrame, CancelFrame, InterruptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame, AudioRawFrame, OutputAudioRawFrame,
    sf, SOUNDFILE_AVAILABLE,
    AudioSegment, PYDUB_AVAILABLE
)
from distr.core.agent.services.llm.text_utils import clean_text_for_tts
from distr.core.agent.services.tts.sentence_split import (
    extract_complete_sentences,
    tts_chunk_has_enough_weight,
)
from distr.core.agent.services.tts.tts_pipeline_mixin import (
    STALE_INTERRUPT_GRACE_SEC,
    TTSPipelineMixin,
)
from distr.core.agent.services.tts.openai_tts_config import (
    openai_tts_supports_instructions,
    resolve_openai_tts_model,
)

logger = logging.getLogger(__name__)

_OPENAI_TTS_TIMEOUT_SEC = 45.0

# Check if OpenAI is available
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None
    OPENAI_AVAILABLE = False


class OpenAITTSService(TTSPipelineMixin, TTSService):
    """OpenAI-based TTS service using Pipecat"""
    
    def __init__(
        self,
        api_key: str,
        voice_id: str,
        voice_name: str = None,
        stt_service=None,
        playback_speed: float = 1.0,
        event_queue=None,
        speech_volume: int = 100,
        model: str = None,
        instructions: str = None,
        **kwargs,
    ):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for OpenAITTSService")
        if not OPENAI_AVAILABLE:
            raise ImportError("openai library is required for OpenAITTSService")
        if not SOUNDFILE_AVAILABLE and not PYDUB_AVAILABLE:
            raise ImportError("soundfile or pydub is required for OpenAITTSService")
        
        # Initialize TTSService
        super().__init__(**kwargs)
        
        self.client = OpenAI(api_key=api_key)
        self.voice_id = voice_id
        self.voice_name = voice_name or voice_id
        self.model = resolve_openai_tts_model(model)
        self.instructions = (instructions or "").strip()
        self.playback_speed = playback_speed
        self._text_buffer = ""
        self._frame_id_counter = 10000
        self._stt_service = stt_service
        self._cancelled = False
        self._volume_in_run_tts = True
        self._init_tts_pipeline_state()
        self._is_hands_free = False  # Track hands-free mode state
        self._ptt_active = False  # Track PTT state
        self.event_queue = event_queue  # Queue to send events back to main process
        self._tts_session_active = False  # Track if we're in an active TTS session (between LLMFullResponseStartFrame and LLMFullResponseEndFrame)
        self._total_audio_duration = 0.0
        self._tts_started_emitted = False  # Track if we've emitted tts_started for this session
        self._processed_sentences = set()  # Track processed sentences (normalized text) to prevent duplicates
        self._tts_sentence_batch_size = 3
        self._sentence_batch_hold: list[str] = []
        self._last_processed_text_hash = None  # Track last processed text to prevent duplicate processing
        # Timestamp when LLMFullResponseStartFrame last received - used to ignore stale InterruptionFrames
        # from interrupt_tts that ran before process_text_input (they arrive late and wrongly cancel new response)
        self._llm_response_started_at = 0.0
        # Convert speech_volume (0-100) to multiplier (0.0-1.0)
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        
        logger.info(
            "OpenAITTSService initialized with voice: %s (ID: %s), model: %s, volume: %s%%",
            self.voice_name,
            self.voice_id,
            self.model,
            speech_volume,
        )
    
    def set_playback_speed(self, speed: float):
        """Update playback speed in real-time"""
        self.playback_speed = speed
        logger.debug(f"OpenAI TTS playback speed updated to {speed:.1f}x")
    
    def set_speech_volume(self, volume: int):
        """Update speech volume in real-time (0-100)"""
        # Convert volume (0-100) to multiplier (0.0-1.0)
        self._speech_volume = max(0.0, min(1.0, volume / 100.0))
        logger.debug(f"OpenAI TTS speech volume updated to {volume}% (multiplier: {self._speech_volume:.2f})")
    
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

    def _extract_complete_sentences(self, text: str):
        """Extract complete sentences from text buffer."""
        return extract_complete_sentences(text)

    def _get_sentence_batch_size(self) -> int:
        """How many complete sentences to synthesize per API call (1 = legacy per-sentence)."""
        return max(1, int(getattr(self, "_tts_sentence_batch_size", 1) or 1))

    def _is_duplicate_sentence(self, sentence: str) -> bool:
        normalized = (sentence or "").strip().lower()
        if not normalized:
            return True
        if normalized in self._processed_sentences:
            return True
        if len(normalized) > 20:
            for processed in self._processed_sentences:
                if len(processed) > 20 and normalized in processed:
                    return True
        return False

    def _mark_sentence_processed(self, sentence: str) -> None:
        normalized = (sentence or "").strip().lower()
        if not normalized:
            return
        self._processed_sentences.add(normalized)
        if len(self._processed_sentences) > 100:
            self._processed_sentences = set(list(self._processed_sentences)[-50:])

    async def _enqueue_new_sentences(self, sentences: list[str], direction) -> None:
        """Dedupe, optionally batch (e.g. Pixazo), and enqueue for synthesis."""
        batch_size = self._get_sentence_batch_size()
        hold: list[str] = getattr(self, "_sentence_batch_hold", None) or []

        async def _flush_hold() -> None:
            nonlocal hold
            if not hold:
                return
            chunk = " ".join(hold)
            hold = []
            self._sentence_batch_hold = hold
            await self._enqueue_sentence(chunk, direction)

        for sentence in sentences:
            if self._cancelled:
                self._text_buffer = ""
                hold = []
                self._sentence_batch_hold = hold
                break
            if self._is_duplicate_sentence(sentence):
                logger.debug("TTS: Skipping duplicate sentence: %r", sentence[:50])
                continue
            self._mark_sentence_processed(sentence)
            hold.append(sentence)
            if tts_chunk_has_enough_weight(hold, max_sentences=max(3, batch_size)):
                chunk = " ".join(hold)
                hold = []
                await self._enqueue_sentence(chunk, direction)

        self._sentence_batch_hold = hold

    async def _flush_sentence_batch_hold(self, direction) -> None:
        """Speak any trailing sentences fewer than batch_size (end of LLM response)."""
        hold: list[str] = getattr(self, "_sentence_batch_hold", None) or []
        if not hold:
            return
        self._sentence_batch_hold = []
        await self._enqueue_sentence(" ".join(hold), direction)

    def _speech_create_kwargs(self, text: str) -> dict:
        """Build Speech API kwargs for the selected model."""
        # Speed is forced to 1.0; time stretching is handled by the Transport layer.
        kwargs = {
            "model": self.model,
            "voice": self.voice_id,
            "input": text,
            "speed": 1.0,
        }
        if self.instructions and openai_tts_supports_instructions(self.model):
            kwargs["instructions"] = self.instructions
        return kwargs

    def _generate_audio(self, text: str):
        """Generate audio from text using OpenAI TTS API"""
        try:
            response = self.client.audio.speech.create(**self._speech_create_kwargs(text))
            
            # Get audio bytes from response
            audio_bytes = response.content
            
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
            logger.error(f"Error generating OpenAI audio: {e}", exc_info=True)
            raise

    async def run_tts(self, text: str):
        """Process text and yield audio frames"""
        if self._cancelled:
            logger.debug("TTS: run_tts() called but cancelled - returning")
            return

        yield TTSStartedFrame()
        
        # Check again after yielding start frame
        if self._cancelled:
            return
        
        audio_duration_seconds = 0.0
        
        try:
            loop = asyncio.get_running_loop()
            audio, sample_rate = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: self._generate_audio(text)),
                timeout=_OPENAI_TTS_TIMEOUT_SEC,
            )
            
            if self._cancelled:
                return

            if audio is not None and len(audio) > 0:
                # OpenAI API handles playback speed internally
                audio_int16 = (audio * 32767).astype(np.int16)
                audio_bytes = audio_int16.tobytes()
                
                chunk_size = int(sample_rate * 0.02 * 2)
                chunk_size = max(chunk_size, 320)
                
                FrameClass = OutputAudioRawFrame if OutputAudioRawFrame else AudioRawFrame
                
                frames_yielded = 0
                yielded_audio_bytes = 0
                for i in range(0, len(audio_bytes), chunk_size):
                    if self._cancelled:
                        break
                        
                    chunk = audio_bytes[i:i + chunk_size]
                    if len(chunk) > 0:
                        # Emit tts_started when we yield the FIRST audio frame
                        if frames_yielded == 0 and self._tts_session_active and not self._tts_started_emitted:
                            self._tts_started_emitted = True
                            if self.event_queue:
                                try:
                                    self.event_queue.put(
                                        ('tts_started', {"source": "direct_desktop"}),
                                        block=False,
                                    )
                                except Exception:
                                    pass
                        
                        # Apply speech volume with proper clipping
                        audio_array = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                        audio_array = audio_array / 32767.0  # Normalize to [-1, 1]
                        audio_array = audio_array * self._speech_volume
                        
                        # Clip to prevent overflow and convert back to int16
                        audio_array = np.clip(audio_array, -1.0, 1.0)
                        audio_array = (audio_array * 32767.0).astype(np.int16)
                        
                        chunk = audio_array.tobytes()
                        
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
                        yielded_audio_bytes += len(chunk)
                
                # Duration tracks audio actually yielded (interrupted synthesis may stop early).
                bytes_per_second = sample_rate * 2 * 1  # sample_rate * bytes_per_sample * num_channels
                audio_duration_seconds = (
                    yielded_audio_bytes / bytes_per_second if bytes_per_second > 0 else 0
                )
                logger.debug(
                    "TTS: Calculated audio duration: %.3fs (yielded_bytes=%s, sample_rate=%s)",
                    audio_duration_seconds,
                    yielded_audio_bytes,
                    sample_rate,
                )
            else:
                audio_duration_seconds = 0
        except asyncio.TimeoutError:
            logger.error(
                "TTS: OpenAI API timed out after %ss (text=%r)",
                _OPENAI_TTS_TIMEOUT_SEC,
                text[:120],
            )
            yield ErrorFrame(error=f"OpenAI TTS timed out after {_OPENAI_TTS_TIMEOUT_SEC}s")
            audio_duration_seconds = 0
        except Exception as e:
            logger.error(f"TTS Error: {e}", exc_info=True)
            yield ErrorFrame(error=str(e))
            audio_duration_seconds = 0
        finally:
            # FIX: Add delay to ensure TTSStoppedFrame is sent after audio playback completes
            # This prevents premature player closure when audio is still in buffers
            if audio_duration_seconds > 0:
                # Add a small delay proportional to audio duration to allow playback to complete
                # Minimum 100ms, maximum 500ms to handle buffer processing
                buffer_delay = max(0.1, min(0.5, audio_duration_seconds * 0.1))
                logger.debug(f"TTS: Adding {buffer_delay:.3f}s delay before TTSStoppedFrame to ensure playback completion (audio_duration={audio_duration_seconds:.3f}s)")
                try:
                    await asyncio.sleep(buffer_delay)
                except Exception as e:
                    logger.warning(f"TTS: Could not add delay before TTSStoppedFrame: {e}")
            
            yield TTSStoppedFrame()
            self._total_audio_duration += audio_duration_seconds
            logger.debug(f"TTS: Accumulated duration after sentence: {self._total_audio_duration:.3f}s (added {audio_duration_seconds:.3f}s)")

    async def process_frame(self, frame, direction):
        """Process TextFrame frames and generate audio"""
        
        frame_type = type(frame).__name__
        # Don't log TextFrame here - it's logged in detail below when processed
        if frame_type == 'InterruptionFrame':
            # Log InterruptionFrame immediately when received
            logger.debug(f"TTS: InterruptionFrame in process_frame (hands_free={self._is_hands_free}, ptt_active={self._ptt_active})")
        elif frame_type not in ['AudioRawFrame', 'InputAudioRawFrame', 'UserSpeakingFrame', 'TextFrame']:
            if frame_type in ['LLMFullResponseStartFrame', 'LLMFullResponseEndFrame']:
                logger.debug(f"TTS: Received frame: {frame_type}")
            else:
                logger.debug(f"TTS: Received frame: {frame_type}")
        
        if isinstance(frame, StartFrame):
            logger.debug(f"TTS: Received StartFrame. Current _started={getattr(self, '_FrameProcessor__started', 'UNKNOWN')}")
        
        # CancelFrame handling:
        # - Hands-free mode: Pass through (needed for proper interruption)
        # - Push-to-talk mode: STOP here (transport breaks if it receives these)
        # GUARD: Same stale-frame logic as InterruptionFrame (from pre-send interrupt)
        if isinstance(frame, CancelFrame):
            now = time.monotonic()
            if self.is_stale_interrupt_frame():
                logger.info(
                    "TTS: Ignoring stale CancelFrame (%.0fms since LLMFullResponseStartFrame)",
                    (now - self._llm_response_started_at) * 1000,
                )
                return
            self.abort_pending_synthesis()
            logger.debug(f"TTS: CancelFrame received - cancelling TTS (hands_free={self._is_hands_free}, ptt_active={self._ptt_active})")
            
            # Only pass CancelFrame through in hands-free mode
            if self._is_hands_free:
                logger.debug("TTS: Passing CancelFrame through (hands-free mode)")
                await self.push_frame(frame, direction)
            return
        
        # Handle InterruptionFrame - KILLS AUDIO IMMEDIATELY
        # InterruptionFrame always interrupts, regardless of mode.
        # GUARD: Ignore stale InterruptionFrames from interrupt_tts that ran before process_text_input.
        # When user sends a message we do: interrupt_tts -> process_text_input. The InterruptionFrame
        # from interrupt_tts can arrive at TTS AFTER LLMFullResponseStartFrame (pipeline reordering).
        # That would wrongly cancel the new response. Ignore if we started a new response within 300ms.
        if isinstance(frame, InterruptionFrame):
            now = time.monotonic()
            if self.is_stale_interrupt_frame():
                logger.info(
                    "TTS: Ignoring stale InterruptionFrame (%.0fms since LLMFullResponseStartFrame; "
                    "grace=%.1fs — typical hot-swap / interrupt_tts race)",
                    (now - self._llm_response_started_at) * 1000,
                    STALE_INTERRUPT_GRACE_SEC,
                )
                return
            logger.debug("TTS: InterruptionFrame received - stopping playback")
            self.abort_pending_synthesis()
            
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
        
        # UserStartedSpeakingFrame / UserStoppedSpeakingFrame — pass through, no ducking
        if isinstance(frame, UserStartedSpeakingFrame):
            if self._is_hands_free:
                await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserStoppedSpeakingFrame):
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            logger.debug(f"TTS: Received TextFrame: '{frame.text[:100]}...' (buffer_len={len(self._text_buffer)}, cancelled={self._cancelled}, ptt_active={self._ptt_active})")
            
            # CRITICAL: If cancelled (e.g., by CancelFrame from PTT), DROP all TextFrames
            # Do NOT reset cancelled state - only LLMFullResponseStartFrame should do that
            if not self.maybe_clear_stale_cancelled_for_text():
                logger.debug("TTS: TextFrame dropped - TTS is cancelled, not processing")
                return
            
            # Accumulate cleaned text and process incrementally. Live deltas can
            # contain markdown/tool residue that is absent from rendered replay.
            cleaned_text = clean_text_for_tts(frame.text, strip_whitespace=False, spoken_prose=True)
            if not cleaned_text:
                logger.debug("TTS: TextFrame dropped after OpenAI live cleaning")
                return

            # Log buffer state before adding to help debug duplicates
            buffer_before = len(self._text_buffer)
            self._text_buffer += cleaned_text
            logger.debug(f"TTS: Added TextFrame text to buffer (buffer: {buffer_before} -> {len(self._text_buffer)} chars): '{frame.text[:100]}...'")
            
            # CRITICAL: Check cancellation before extracting sentences
            # This prevents processing new sentences if we're already cancelled
            if self._cancelled:
                logger.debug("TTS: Cancelled before extracting sentences - clearing buffer and dropping TextFrame")
                self._text_buffer = ""
                return
            
            # Extract complete sentences and process them immediately
            sentences, remaining = self._extract_complete_sentences(self._text_buffer)
            self._text_buffer = remaining
            
            if sentences:
                logger.debug(
                    "TTS: Processing %s sentence(s) from TextFrame (batch=%s)",
                    len(sentences),
                    self._get_sentence_batch_size(),
                )
                await self._enqueue_new_sentences(sentences, direction)
            else:
                logger.debug(f"TTS: No complete sentences yet, buffering")
            
            # CRITICAL: Do NOT push TextFrame through - TTS services consume TextFrames and generate audio
            # Pushing TextFrames through would cause them to be processed again downstream, leading to repetition
            # TextFrames are consumed here and converted to AudioRawFrames, which are what get pushed downstream
            return
        
        if isinstance(frame, LLMFullResponseStartFrame):
            self._llm_response_started_at = time.monotonic()
            logger.debug(f"TTS: LLMFullResponseStartFrame - resetting TTS state for new response (cancelled={self._cancelled} -> False)")
            self._text_buffer = ""
            self._cancelled = False
            self._cancelled_since = 0.0
            self._processed_sentences.clear()
            self._sentence_batch_hold = []
            
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
                
                # CRITICAL: Check for duplicate sentences in remaining text before processing
                # Extract sentences and check each one against processed set
                sentences, remaining = self._extract_complete_sentences(text)
                new_sentence_count = 0
                if sentences:
                    # Filter out already-processed sentences
                    new_sentences = []
                    for sentence in sentences:
                        normalized = sentence.strip().lower()
                        
                        # Check exact match
                        if normalized in self._processed_sentences:
                            logger.debug(f"TTS: Skipping duplicate sentence in remaining text: '{sentence[:50]}...' (already processed)")
                            continue
                        
                        # Only skip when current is subset of processed (redundant)
                        is_duplicate = False
                        for processed in self._processed_sentences:
                            if len(normalized) > 10 and len(processed) > 10 and normalized in processed:
                                logger.debug(f"TTS: Skipping duplicate in remaining (subset): '{sentence[:50]}...'")
                                is_duplicate = True
                                break
                        
                        if is_duplicate:
                            continue
                        
                        new_sentences.append(sentence)
                        self._processed_sentences.add(normalized)
                        new_sentence_count += 1
                    
                    # Reconstruct text from new sentences only
                    if new_sentences:
                        text = ' '.join(new_sentences) + (' ' + remaining if remaining else '')
                    elif remaining.strip():
                        text = remaining.strip()
                    else:
                        text = ""
                
                if not text.strip():
                    logger.debug(f"TTS: No new text to process after duplicate filtering")
                else:
                    logger.debug(
                        "TTS: Enqueueing remaining text after duplicate check (%s new sentences): %r",
                        new_sentence_count,
                        text[:80],
                    )
                    sentences, trailing = self._extract_complete_sentences(text)
                    if trailing.strip():
                        sentences = list(sentences) + [trailing.strip()]
                    await self._enqueue_new_sentences(sentences, direction)
            else:
                logger.debug("TTS: No remaining text to process (buffer empty or cancelled)")

            # ponytail: Pixazo batches 3 sentences — short replies can leave 1–2 in hold
            # after streaming drained the text buffer; always flush at response end.
            if not self._cancelled:
                await self._flush_sentence_batch_hold(direction)

            if not self._cancelled:
                await self._drain_speak_queue()
            
            # End TTS session - emit stop event with total accumulated duration
            if self._tts_session_active:
                total_duration = self._total_audio_duration
                self._tts_session_active = False
                self._total_audio_duration = 0.0
                
                # Ensure we emit a positive duration even if very small
                # This prevents the player from being treated as an interrupt case
                if total_duration <= 0.0 and self._tts_started_emitted:
                    # If we emitted tts_started but have no duration, use a small default
                    total_duration = 0.1  # Minimum duration to prevent premature closing
                    logger.debug(f"TTS: No duration tracked but tts_started was emitted - using minimum duration {total_duration}s")
                
                # Emit TTS stopped event to main process with total audio duration
                # The GUI will wait for this duration before hiding the player window
                # NOTE: The transport's playback_finished event is the primary mechanism for closing the player
                # This tts_stopped event is mainly used as a failsafe
                if self.event_queue:
                    try:
                        self.event_queue.put(('tts_stopped', {'duration': total_duration}), block=False)
                        logger.debug(f"TTS: Sent tts_stopped (duration: {total_duration:.1f}s)")
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
        """Return the output sample rate for OpenAI TTS (24kHz)"""
        return 24000







