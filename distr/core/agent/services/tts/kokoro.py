import asyncio
import logging
import os
import re
import time
import numpy as np

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE, TTSService,
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame, TTSStartedFrame, TTSStoppedFrame, ErrorFrame, StartFrame, EndFrame, CancelFrame, InterruptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame, AudioRawFrame, OutputAudioRawFrame,
    Kokoro, KOKORO_AVAILABLE
)

# Import text cleaning utility to remove markdown formatting
from distr.core.agent.services.llm.utils import clean_text_for_tts
from distr.core.agent.services.tts.sentence_split import extract_complete_sentences

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text normalization for espeak-ng phonemizer
# ---------------------------------------------------------------------------
# The root issue: LLMs output smart/curly quotes (U+2019) for contractions
# like "I'm".  The clean_text_for_tts() sanitizer in utils.py now
# normalizes these to straight apostrophes before they reach TTS, so
# espeak-ng phonemizes "I'm" correctly as "aɪm" instead of "ɪm".
#
# This function is kept as a lightweight safety net for any call sites that
# bypass clean_text_for_tts (settings previews, Telegram, save_audio, etc.).

_SMART_APOSTROPHE = re.compile(r"[\u2018\u2019\u0060\u00B4]")


def _normalize_text_for_tts(text: str) -> str:
    """Normalize smart quotes to straight apostrophes for correct phonemization."""
    if not text:
        return text
    return _SMART_APOSTROPHE.sub("'", text)


class KokoroTTSService(TTSService):
    """Kokoro-based TTS service using Pipecat"""
    
    def __init__(self, model_path: str, voices_path: str, voice_name: str = "af_heart", stt_service=None, playback_speed: float = 1.0, event_queue=None, speech_volume: int = 100, reference_voice_path: str = None, **kwargs):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for KokoroTTSService")
        if not KOKORO_AVAILABLE:
            raise ImportError("kokoro-onnx is required for KokoroTTSService")
        
        # Initialize TTSService (includes Pipecat 0.0.95+ __process_queue fix from libs.py)
        super().__init__(**kwargs)
            
        self.kokoro = Kokoro(model_path, voices_path)
        self.voice_name = voice_name
        self.playback_speed = playback_speed
        self._text_buffer = ""
        self._frame_id_counter = 10000
        self._stt_service = stt_service
        self._cancelled = False
        self._cancelled_since = 0.0  # monotonic timestamp when cancellation was last set
        self._is_hands_free = False  # Track hands-free mode state
        self._ptt_active = False  # Track PTT state
        self.event_queue = event_queue  # Queue to send events back to main process
        self._tts_session_active = False  # Track if we're in an active TTS session
        self._total_audio_duration = 0.0  # Accumulate total audio duration
        self._tts_started_emitted = False  # Track if we've emitted tts_started for this session
        self._in_response_after_start = False  # True between LLMFullResponseStartFrame and LLMFullResponseEndFrame; used to ignore stale CancelFrames
        self._llm_response_started_at = 0  # Timestamp of last LLMFullResponseStartFrame; used to ignore stale InterruptionFrames
        self._processed_sentences = set()  # Track processed sentences (normalized text) to prevent duplicates
        self._sentence_emit_counts = {}  # Track sentence emission count per response for duplicate diagnostics
        self._response_debug_id = "boot"
        self._session_text = ""  # Track full text spoken during current TTS session
        self._last_telegram_send_hash = None  # Track last sent message hash to prevent duplicates
        self._last_telegram_send_time = 0  # Track last send time for rate limiting
        # Convert speech_volume (0-100) to multiplier (0.0-1.0)
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        
        # Voice cloning via Kanade — if a reference clip is provided, all TTS
        # output is voice-converted to match the reference speaker.
        self._reference_voice_path = reference_voice_path
        self._voice_cloning_enabled = False
        if reference_voice_path and os.path.isfile(reference_voice_path):
            self._voice_cloning_enabled = True
            logger.info("KokoroTTSService: voice cloning ENABLED (ref: %s)", os.path.basename(reference_voice_path))
        elif reference_voice_path:
            logger.warning("KokoroTTSService: reference voice path not found: %s", reference_voice_path)
        
        try:
            available_voices = self.kokoro.get_voices()
            if voice_name in available_voices:
                self.voice = voice_name
                logger.info(f"KokoroTTSService: using voice '{self.voice}' (requested: '{voice_name}')")
            else:
                self.voice = available_voices[0] if available_voices else "af_heart"
                logger.warning(f"KokoroTTSService: voice '{voice_name}' not in {list(available_voices)[:10] if available_voices else []}, using '{self.voice}'")
        except Exception as e:
            logger.warning(f"KokoroTTSService: could not load voice, using default: {e}")
            self.voice = "af_heart"

        logger.info(f"KokoroTTSService initialized with voice: {self.voice}, volume: {speech_volume}%")
    
    def set_voice(self, voice_name: str):
        """Update voice in place - used for hot-swap without replacing the processor."""
        try:
            available_voices = self.kokoro.get_voices()
            if voice_name in available_voices:
                self.voice = voice_name
                self.voice_name = voice_name
                logger.info(f"KokoroTTSService: voice switched to '{self.voice}'")
            else:
                fallback = available_voices[0] if available_voices else "af_heart"
                self.voice = fallback
                self.voice_name = fallback
                logger.warning(f"KokoroTTSService: voice '{voice_name}' not in available, using '{fallback}'")
        except Exception as e:
            logger.warning(f"KokoroTTSService: set_voice failed: {e}")
            self.voice = "af_heart"

    def set_playback_speed(self, speed: float):
        """Update playback speed in real-time"""
        self.playback_speed = speed
        logger.debug(f"Kokoro TTS playback speed updated to {speed:.1f}x")
    
    def set_speech_volume(self, volume: int):
        """Update speech volume in real-time (0-100)"""
        # Convert volume (0-100) to multiplier (0.0-1.0)
        self._speech_volume = max(0.0, min(1.0, volume / 100.0))
        logger.debug(f"Kokoro TTS speech volume updated to {volume}% (multiplier: {self._speech_volume:.2f})")

    def set_reference_voice(self, path: str | None):
        """Enable or disable voice cloning. Pass None to disable."""
        if path and os.path.isfile(path):
            self._reference_voice_path = path
            self._voice_cloning_enabled = True
            logger.info("Voice cloning enabled: %s", os.path.basename(path))
        else:
            self._reference_voice_path = None
            self._voice_cloning_enabled = False
            # Unload Kanade model from memory when voice cloning is disabled
            try:
                from distr.core.audio.voice_cloner import unload_model
                unload_model()
            except Exception:
                pass
            if path:
                logger.warning("Reference voice not found: %s", path)
            else:
                logger.info("Voice cloning disabled")
    
    def set_hands_free(self, enabled: bool):
        """Set hands-free mode state - affects how CancelFrame/InterruptionFrame are handled"""
        self._is_hands_free = enabled
        logger.debug(f"TTS hands-free mode: {enabled}")
    
    def set_ptt_active(self, active: bool):
        """Set PTT state."""
        self._ptt_active = active
        logger.debug(f"TTS: set_ptt_active(active={active})")

    def _extract_complete_sentences(self, text: str):
        """Extract complete sentences from text buffer."""
        return extract_complete_sentences(text)

    async def run_tts(self, text: str):
        """Process text and yield audio frames"""
        # Check cancellation at the very start
        if self._cancelled:
            logger.debug("TTS: run_tts() called but _cancelled=True - returning immediately")
            return

        # Normalize text for better phonemization (smart quotes → straight apostrophes)
        text = _normalize_text_for_tts(text)

        yield TTSStartedFrame()

        # Check again after yielding start frame
        if self._cancelled:
            return

        audio_duration_seconds = 0.0

        try:
            # WORKAROUND: kokoro-onnx has an off-by-one bug when truncating to 510 phonemes
            # It tries to access index 510 in an array of size 510 (valid indices: 0-509)
            # Split long sentences into chunks to stay under 509 phonemes (~390 chars per chunk)
            MAX_CHARS = 390
            text_chunks = []

            if len(text) > MAX_CHARS:
                logger.warning(f"TTS: Sentence too long ({len(text)} chars), splitting into chunks")
                # Split at commas, semicolons, or word boundaries
                remaining = text
                while remaining:
                    if len(remaining) <= MAX_CHARS:
                        text_chunks.append(remaining)
                        break

                    # Try to split at comma or semicolon within MAX_CHARS
                    chunk = remaining[:MAX_CHARS]
                    split_pos = max(chunk.rfind(','), chunk.rfind(';'), chunk.rfind(' '))

                    if split_pos > 0:
                        text_chunks.append(remaining[:split_pos + 1].strip())
                        remaining = remaining[split_pos + 1:].strip()
                    else:
                        # No good split point, force split at word boundary
                        text_chunks.append(chunk.rsplit(' ', 1)[0])
                        remaining = remaining[len(text_chunks[-1]):].strip()
            else:
                text_chunks = [text]

            loop = asyncio.get_running_loop()
            # Run in executor to avoid blocking
            # Clamp speed to Kokoro's supported range (0.5 to 2.0)
            # NOTE: We force speed to 1.0 here because time stretching is handled by the Transport layer now.
            clamped_speed = 1.0

            frames_yielded_total = 0

            # Process each chunk
            for text_chunk in text_chunks:
                if self._cancelled:
                    break

                try:
                    audio, sample_rate = await loop.run_in_executor(
                        None,
                        lambda c=text_chunk: self.kokoro.create(c, voice=self.voice, speed=clamped_speed)
                    )
                except ValueError as e:
                    if "need at least one array to concatenate" in str(e):
                        logger.warning("TTS: No audio frames generated for sentence: %r", text_chunk[:50] if len(text_chunk) > 50 else text_chunk)
                        continue
                    raise

                # Check cancellation after audio generation
                if self._cancelled:
                    return

                if audio is not None and len(audio) > 0:
                    # Voice cloning: convert to reference voice via Kanade
                    if self._voice_cloning_enabled:
                        try:
                            from distr.core.audio.voice_cloner import convert_voice, get_output_sample_rate
                            _vc_audio, _vc_sr, _vc_ref = audio, sample_rate, self._reference_voice_path
                            audio = await loop.run_in_executor(
                                None,
                                lambda a=_vc_audio, sr=_vc_sr, r=_vc_ref: convert_voice(a, sr, r),
                            )
                            sample_rate = get_output_sample_rate()
                        except Exception as vc_err:
                            logger.error("Voice cloning failed, using original audio: %s", vc_err)

                    if self._cancelled:
                        return

                    # Custom Kokoro voices (voice cloning) take longer to process.
                    # Add a small delay before yielding the FIRST audio frame so the
                    # transport buffer has time to fill, preventing choppy playback.
                    if self._voice_cloning_enabled and frames_yielded_total == 0:
                        import asyncio as _aio
                        logger.debug("TTS: Custom voice — waiting 2s before first playback")
                        await _aio.sleep(2.0)
                        if self._cancelled:
                            return

                    audio_int16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
                    audio_bytes = audio_int16.tobytes()

                    chunk_size = int(sample_rate * 0.02 * 2)
                    chunk_size = max(chunk_size, 320)

                    FrameClass = OutputAudioRawFrame if OutputAudioRawFrame else AudioRawFrame

                    for i in range(0, len(audio_bytes), chunk_size):
                        if self._cancelled:
                            break

                        audio_chunk = audio_bytes[i:i + chunk_size]
                        if len(audio_chunk) > 0:
                            # Emit tts_started when we yield the FIRST audio frame
                            # BUT ONLY for desktop requests - Telegram requests should NOT show the player
                            if frames_yielded_total == 0 and self._tts_session_active and not self._tts_started_emitted:
                                # Check if this is a Telegram request - if so, don't emit tts_started
                                import threading
                                has_telegram_request = hasattr(threading.current_thread(), 'telegram_request') and threading.current_thread().telegram_request

                                # Also check instance variable
                                if hasattr(self, '_current_telegram_request') and self._current_telegram_request:
                                    has_telegram_request = True

                                # If not found on current thread, check all threads
                                if not has_telegram_request:
                                    import threading as threading_module
                                    for thread in threading_module.enumerate():
                                        if hasattr(thread, 'telegram_request') and thread.telegram_request:
                                            has_telegram_request = True
                                            break

                                # Only emit tts_started for desktop requests
                                if not has_telegram_request:
                                    self._tts_started_emitted = True
                                    if self.event_queue:
                                        try:
                                            self.event_queue.put(('tts_started', {}), block=False)
                                            logger.debug("TTS: tts_started emitted")
                                        except Exception:
                                            pass
                                else:
                                    logger.debug("TTS: Skipping tts_started event (Telegram request - player should not show)")
                                    # Still mark as emitted to prevent duplicate checks
                                    self._tts_started_emitted = True

                            # Apply speech volume
                            audio_array = np.frombuffer(audio_chunk, dtype=np.int16)
                            audio_array = (audio_array * self._speech_volume).astype(np.int16)

                            audio_chunk = audio_array.tobytes()

                            frame = FrameClass(
                                audio=audio_chunk,
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
                            frames_yielded_total += 1

                    # Calculate audio duration for this chunk
                    total_audio_bytes = len(audio_bytes)
                    bytes_per_second = sample_rate * 2 * 1
                    audio_duration_seconds += total_audio_bytes / bytes_per_second if bytes_per_second > 0 else 0
                else:
                    logger.warning(f"TTS: No audio generated from text chunk: '{text_chunk[:50]}...'")
        except Exception as e:
            logger.error(f"TTS Error: {e}", exc_info=True)
            yield ErrorFrame(error=str(e))
            audio_duration_seconds = 0
        finally:
            yield TTSStoppedFrame()
            self._total_audio_duration += audio_duration_seconds

    async def process_frame(self, frame, direction):
        """Process TextFrame frames and generate audio"""

        frame_type = type(frame).__name__

        if frame_type == 'TextFrame':
            logger.debug(f"TTS: TextFrame: '{frame.text[:60]}' (buf={len(self._text_buffer)}, cancelled={self._cancelled})")
        elif frame_type == 'LLMFullResponseStartFrame':
            logger.debug("TTS: Response start")
        elif frame_type == 'LLMFullResponseEndFrame':
            logger.debug("TTS: Response end")
        elif frame_type == 'InterruptionFrame':
            logger.debug(f"TTS: InterruptionFrame (hands_free={self._is_hands_free})")
        elif frame_type not in ['AudioRawFrame', 'InputAudioRawFrame', 'UserSpeakingFrame']:
            logger.debug(f"TTS: Frame: {frame_type}")
        
        if isinstance(frame, StartFrame):
            logger.debug("TTS: StartFrame received")
        
        # Only cancel on explicit cancellation frames, not on UserStartedSpeakingFrame
        # UserStartedSpeakingFrame is just a notification, not a cancellation signal
        # CRITICAL: CancelFrame and InterruptionFrame handling depends on mode:
        # - Hands-free mode: Pass through (needed for proper interruption)
        # - Push-to-talk mode: STOP here (transport breaks if it receives these)
        if isinstance(frame, CancelFrame):
            # Ignore stale CancelFrames: interrupt_tts runs before process_text_input, but the frame can
            # arrive at TTS after the new response has started (pipeline ordering). Don't kill the new response.
            if getattr(self, '_in_response_after_start', False):
                logger.debug("TTS: CancelFrame ignored (stale - already in new response)")
                return
            # CancelFrame is still handled for backwards compatibility, but PTT now uses InterruptionFrame
            self._cancelled = True
            self._cancelled_since = time.monotonic()
            self._text_buffer = ""  # Clear any buffered text
            logger.warning(f"TTS: CancelFrame received - FORCE cancelling TTS (hands_free={self._is_hands_free}, ptt_active={self._ptt_active})")
            logger.debug("TTS: _cancelled=True, clearing text buffer. All subsequent TextFrames will be DROPPED")
            
            # Only pass CancelFrame through in hands-free mode
            # PTT now uses InterruptionFrame instead
            if self._is_hands_free:
                logger.debug("TTS: Passing CancelFrame through (hands-free mode)")
                await self.push_frame(frame, direction)
            else:
                logger.debug("TTS: NOT passing CancelFrame to transport (push-to-talk mode uses InterruptionFrame instead)")
            return
        
        # Handle InterruptionFrame - stop playback immediately (user spoke or stop/PTT)
        # CRITICAL: If an InterruptionFrame reaches TTS, it should ALWAYS interrupt (no filtering here)
        # - VAD-generated InterruptionFrames are filtered in Whisper/STT based on hands-free mode
        # - PTT-generated InterruptionFrames always come through and should always interrupt
        # - Don't filter based on hands-free mode here - that's STT's job
        if isinstance(frame, InterruptionFrame):
            # Guard: ignore stale InterruptionFrames that arrive after the current response
            # has already started (e.g. from a pre-send interrupt_tts command that raced).
            # Without this, creating a new chat sends interrupt_tts → InterruptionFrame,
            # then the LLM response starts → LLMFullResponseStartFrame resets _cancelled,
            # then the stale InterruptionFrame arrives and re-cancels, muting TTS output.
            now = time.monotonic()
            # Create-chat / load-in-agent send interrupt_tts before the first LLM token; on slow
            # pipelines InterruptionFrame can arrive well after LLMFullResponseStartFrame. A short
            # window caused _cancelled=True + all TextFrames dropped (audible silence, UI still streams).
            _STALE_INTERRUPT_GRACE_SEC = 2.0
            if (
                self._llm_response_started_at > 0
                and (now - self._llm_response_started_at) < _STALE_INTERRUPT_GRACE_SEC
            ):
                logger.info(
                    "TTS: Ignoring stale InterruptionFrame (%.0fms since LLMFullResponseStartFrame; "
                    "grace=%.1fs — typical create-chat interrupt_tts race)",
                    (now - self._llm_response_started_at) * 1000,
                    _STALE_INTERRUPT_GRACE_SEC,
                )
                return
            logger.debug("TTS: InterruptionFrame received - stopping playback")
            # CRITICAL: KILL audio immediately - set cancelled flag FIRST
            self._cancelled = True
            self._cancelled_since = time.monotonic()
            self._text_buffer = ""  # Clear any buffered text
            logger.debug("TTS: _cancelled=True, clearing text buffer. All subsequent TextFrames will be DROPPED")
            
            # Always emit tts_stopped on interrupt to close player (even if session wasn't active)
            # This ensures the player closes if it's already open
            # BUT ONLY for desktop requests - Telegram requests should NOT trigger player events
            if self._tts_session_active:
                self._tts_session_active = False
                self._total_audio_duration = 0.0
                self._tts_started_emitted = False
            
            # Check if this is a Telegram request - if so, don't emit tts_stopped
            import threading
            has_telegram_request = hasattr(threading.current_thread(), 'telegram_request') and threading.current_thread().telegram_request
            
            # Also check instance variable
            if hasattr(self, '_current_telegram_request') and self._current_telegram_request:
                has_telegram_request = True
            
            # If not found on current thread, check all threads
            if not has_telegram_request:
                import threading as threading_module
                for thread in threading_module.enumerate():
                    if hasattr(thread, 'telegram_request') and thread.telegram_request:
                        has_telegram_request = True
                        break
            
            # CRITICAL: Only emit tts_stopped on interrupt for desktop requests
            # This ensures the player window closes if it's open (but not for Telegram)
            if not has_telegram_request and self.event_queue:
                try:
                    self.event_queue.put(('tts_stopped', {'duration': 0.0}), block=False)
                    logger.debug("TTS: tts_stopped emitted (interrupted)")
                except Exception as e:
                    logger.debug(f"Could not emit tts_stopped event (non-blocking): {e}")
            elif has_telegram_request:
                logger.debug("TTS: Skipping tts_stopped event on interrupt (Telegram request - player should not be affected)")
            
            # CRITICAL: Pass InterruptionFrame through to transport to KILL audio playback
            # This frame tells the transport to stop/clear audio buffers immediately
            logger.debug(f"TTS: Passing InterruptionFrame to transport to KILL audio")
            await self.push_frame(frame, direction)
            return
        
        # UserStartedSpeakingFrame / UserStoppedSpeakingFrame — pass through
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
            if self._cancelled:
                # Recovery guard: stale cancellation can race ahead of a new
                # response and leave TTS muted while chat tokens still stream.
                # If no active PTT interruption is happening, auto-clear stale
                # cancelled state after a short grace window.
                _cancel_age = time.monotonic() - float(getattr(self, "_cancelled_since", 0.0) or 0.0)
                if not self._ptt_active and _cancel_age > 1.5:
                    logger.warning(
                        "TTS: Auto-clearing stale _cancelled state before TextFrame "
                        "(age=%.2fs, ptt_active=%s)",
                        _cancel_age,
                        self._ptt_active,
                    )
                    self._cancelled = False
                    self._cancelled_since = 0.0
                else:
                    logger.warning(
                        "TTS: TextFrame DROPPED while _cancelled=True (no audio for this chunk). "
                        "preview=%r — if UI still streamed text, check interrupt_tts race or welcome cancel.",
                        (frame.text or "")[:120],
                    )
                    # Don't process, don't accumulate, just drop it
                    return
            
            # CRITICAL: Check and store telegram_request flag EARLY when TextFrame arrives
            # This ensures it's available even if no sentences are processed (e.g., "Done" messages)
            import threading
            has_telegram_request = hasattr(threading.current_thread(), 'telegram_request') and threading.current_thread().telegram_request
            
            # If not found on current thread, check all threads (for thread pool scenarios)
            if not has_telegram_request:
                import threading as threading_module
                for thread in threading_module.enumerate():
                    if hasattr(thread, 'telegram_request') and thread.telegram_request:
                        has_telegram_request = True
                        # Store it on current thread and in instance variable for persistence
                        threading.current_thread().telegram_request = True
                        self._current_telegram_request = True
                        logger.debug(f"TTS: Found telegram_request=True on thread '{thread.name}' (TextFrame) - stored for session")
                        logger.debug(f"[Telegram TTS] 🔍 Found telegram_request on thread '{thread.name}' (TextFrame), storing for session")
                        break
            
            # Use instance variable if available (persists across the TTS session)
            if hasattr(self, '_current_telegram_request') and self._current_telegram_request:
                has_telegram_request = True
            
            # Store for future use in this TTS session
            if has_telegram_request:
                self._current_telegram_request = True
            
            # CRITICAL: Check if a file was already sent BEFORE accumulating text
            # If so, don't accumulate text - the file is the response
            import threading
            file_already_sent = False
            
            # Check thread-local first
            if hasattr(threading.current_thread(), 'telegram_file_sent'):
                file_already_sent = threading.current_thread().telegram_file_sent
            
            # Also check instance variable (set by LLM service for cross-thread access)
            if not file_already_sent and hasattr(self, '_telegram_file_sent'):
                file_already_sent = self._telegram_file_sent
            
            # Note: Even if file was already sent, we still need to accumulate text and generate TTS audio
            # The file_already_sent flag will be checked later when sending to Telegram
            # This ensures the user still hears "Done" via TTS even when a file was sent
            if file_already_sent:
                logger.debug(f"TTS: File already sent - will still generate TTS audio but skip Telegram text message")
                logger.debug(f"[Telegram TTS] ✅ Will generate TTS audio (file already sent, will skip Telegram text)")
            
            # Accumulate text and process incrementally
            self._text_buffer += frame.text
            # Also track in session text for Telegram sending
            # CRITICAL: Prevent duplicate "Done" accumulation - if text is "Done" and session_text already contains "Done", don't add it again
            text_lower = frame.text.strip().lower()
            session_text_lower = self._session_text.strip().lower()
            is_done_text = text_lower in ['done', 'done.', 'complete', 'completed', 'finished', 'finished.']
            already_has_done = any(done_word in session_text_lower for done_word in ['done', 'complete', 'finished'])
            
            if is_done_text and already_has_done:
                logger.debug(f"TTS: Skipping duplicate 'Done' text accumulation (session_text already contains 'Done')")
            else:
                # Add a space separator between accumulated sentences so Telegram
                # messages don't run together (e.g. "Hello!Welcome back!" → "Hello! Welcome back!")
                if self._session_text and not self._session_text.endswith((' ', '\n')):
                    self._session_text += ' '
                self._session_text += frame.text
            
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
                    emit_count = self._sentence_emit_counts.get(normalized_sentence, 0) + 1
                    self._sentence_emit_counts[normalized_sentence] = emit_count
                    if emit_count > 1:
                        logger.warning(
                            "TTS DUPLICATE DETECT: response_id=%s sentence_count=%d sentence=%r",
                            self._response_debug_id,
                            emit_count,
                            sentence[:120],
                        )
                    else:
                        logger.info(
                            "TTS SENTENCE EMIT: response_id=%s sentence=%r",
                            self._response_debug_id,
                            sentence[:120],
                        )
                    if normalized_sentence in self._processed_sentences:
                        logger.warning(
                            "TTS: Skipping duplicate sentence (response_id=%s): '%s...' (already processed)",
                            self._response_debug_id,
                            sentence[:50],
                        )
                        continue
                    
                    # Only skip when current is SUBSET of processed (redundant). Do NOT skip when
                    # processed is subset of current - that drops longer sentences (streaming sends
                    # "I'll help you." first, then "I'll help you with that.").
                    is_duplicate = False
                    if len(normalized_sentence) > 20:
                        for processed in self._processed_sentences:
                            if len(processed) > 20:
                                if normalized_sentence in processed:
                                    logger.warning(f"TTS: Skipping duplicate (subset): '{sentence[:50]}...'")
                                    is_duplicate = True
                                    break
                    
                    if is_duplicate:
                        continue
                    
                    # Mark sentence as processed and limit set size to prevent memory growth
                    self._processed_sentences.add(normalized_sentence)
                    if len(self._processed_sentences) > 100:
                        self._processed_sentences = set(list(self._processed_sentences)[-50:])
                    
                    # CRITICAL: Check if a file was already sent BEFORE generating audio
                    # If so, skip generating audio for this sentence - the file is the response
                    import threading
                    file_already_sent = False
                    
                    # Check thread-local first
                    if hasattr(threading.current_thread(), 'telegram_file_sent'):
                        file_already_sent = threading.current_thread().telegram_file_sent
                    
                    # Also check instance variable (set by LLM service for cross-thread access)
                    if not file_already_sent and hasattr(self, '_telegram_file_sent'):
                        file_already_sent = self._telegram_file_sent
                    
                    if file_already_sent:
                        logger.debug(f"TTS: File already sent - skipping audio generation for sentence: '{sentence[:50]}...'")
                        logger.debug(f"[Telegram TTS] ⏭️ Skipping audio generation - file already sent")
                        # Don't accumulate in session_text either - we're not sending this
                        continue

                    # Clean text to remove markdown formatting (**, *, _, `, etc.) before TTS
                    # This prevents the TTS from saying "asterisk asterisk" when it sees **bold**
                    cleaned_sentence = clean_text_for_tts(sentence)

                    logger.debug(f"TTS: Generating audio for sentence: '{cleaned_sentence[:50]}...'")
                    
                    # CRITICAL: Check if this is a Telegram request - if so, don't push audio frames to desktop
                    # Check on current thread AND check all threads (in case we're on a different thread)
                    # Also store it in instance variable so it persists for the entire TTS session
                    import threading
                    force_desktop_tts = bool(
                        getattr(threading.current_thread(), 'force_desktop_tts', False)
                        or getattr(self, '_force_desktop_tts', False)
                    )
                    has_telegram_request = (
                        hasattr(threading.current_thread(), 'telegram_request')
                        and threading.current_thread().telegram_request
                    )
                    
                    # If not found on current thread, check all threads (for thread pool scenarios)
                    if (not force_desktop_tts) and (not has_telegram_request):
                        import threading as threading_module
                        for thread in threading_module.enumerate():
                            if hasattr(thread, 'telegram_request') and thread.telegram_request:
                                has_telegram_request = True
                                # Store it on current thread and in instance variable for persistence
                                threading.current_thread().telegram_request = True
                                if not hasattr(self, '_current_telegram_request'):
                                    self._current_telegram_request = True
                                logger.debug(f"TTS: Found telegram_request=True on thread '{thread.name}' (not current thread) - stored for session")
                                logger.debug(f"[Telegram TTS] 🔍 Found telegram_request on thread '{thread.name}', storing for session")
                                break
                    
                    # Use instance variable if available (persists across the TTS session)
                    if (not force_desktop_tts) and hasattr(self, '_current_telegram_request') and self._current_telegram_request:
                        has_telegram_request = True
                    
                    # Store for future use in this TTS session
                    if has_telegram_request:
                        self._current_telegram_request = True
                    if force_desktop_tts:
                        has_telegram_request = False
                        self._current_telegram_request = False
                        logger.debug("TTS: force_desktop_tts=True, bypassing telegram-only mode")
                    
                    if has_telegram_request:
                        logger.debug("TTS: Telegram request - generating audio for Telegram only")
                        logger.debug(f"[Telegram TTS] 🔊 Generating audio for Telegram (not desktop)")
                        # Still generate audio to get duration, but don't push frames downstream
                        # CRITICAL: Consume all frames (including TTSStartedFrame/TTSStoppedFrame) but don't push any to desktop
                        frame_count = 0
                        audio_frame_count = 0
                        async for audio_frame in self.run_tts(cleaned_sentence):
                            # Filter out control frames - only count audio frames for duration
                            if isinstance(audio_frame, (TTSStartedFrame, TTSStoppedFrame)):
                                # Consume but don't push control frames for Telegram requests
                                continue
                            # Count audio frames for duration calculation, but don't push to desktop
                            if isinstance(audio_frame, AudioRawFrame) or (OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)):
                                audio_frame_count += 1
                                frame_count += 1
                            # CRITICAL: Do NOT push any frames to desktop for Telegram requests
                        logger.debug(f"TTS: Generated {audio_frame_count} audio frames for Telegram")
                    else:
                        # Desktop request - generate and push audio frames normally
                        logger.debug("TTS: Generating audio for desktop")
                        frame_count = 0
                        audio_frame_count = 0
                        async for audio_frame in self.run_tts(cleaned_sentence):
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
                        logger.debug(f"TTS: Pushed {audio_frame_count} audio frames for sentence")
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
            self._in_response_after_start = True  # So we ignore stale CancelFrames until EndFrame
            self._llm_response_started_at = time.monotonic()  # Timestamp to ignore stale InterruptionFrames
            self._response_debug_id = f"{int(self._llm_response_started_at * 1000)}"
            self._sentence_emit_counts.clear()
            logger.info("TTS RESPONSE START: response_id=%s", self._response_debug_id)
            # Reset session text tracking for new response
            self._session_text = ""
            self._text_buffer = ""
            self._cancelled = False  # Reset cancelled state to allow new audio generation
            self._cancelled_since = 0.0
            self._processed_sentences.clear()  # Clear processed sentences for new response
            
            # Start new TTS session - reset duration accumulator and mark session as active
            self._tts_session_active = True
            self._total_audio_duration = 0.0
            self._tts_started_emitted = False  # Reset flag - will emit when first audio frame is yielded
            
            # DON'T emit tts_started here - wait until we actually start playing audio
            # This prevents opening the player before audio actually plays
            
            # SAFEGUARD: Force clear all Telegram state flags at start of NEW response
            # This prevents state leakage from previous turns if they weren't cleared properly
            import threading
            if hasattr(threading.current_thread(), 'telegram_request'):
                threading.current_thread().telegram_request = False
            if hasattr(self, '_current_telegram_request'):
                self._current_telegram_request = False
            if hasattr(self, '_telegram_file_sent'):
                self._telegram_file_sent = False
            if hasattr(threading.current_thread(), 'telegram_file_sent'):
                threading.current_thread().telegram_file_sent = False
            if hasattr(threading.current_thread(), 'telegram_analyzed_image'):
                delattr(threading.current_thread(), 'telegram_analyzed_image')
            if hasattr(threading.current_thread(), 'telegram_send_raw_screenshot'):
                delattr(threading.current_thread(), 'telegram_send_raw_screenshot')
            
            await self.push_frame(frame, direction)
            return
            
        elif isinstance(frame, LLMFullResponseEndFrame):
            # NOTE: Do NOT clear _in_response_after_start / _llm_response_started_at here.
            # Clearing immediately allowed late InterruptionFrames (interrupt_tts races, VAD glitches)
            # to cancel TTS while we still synthesize "remaining buffer" audio — audible cut-offs.
            # Reset both only at the end of this branch (after remaining text + tts_stopped).
            duplicate_sentences = sum(1 for count in self._sentence_emit_counts.values() if count > 1)
            logger.info(
                "TTS RESPONSE END: response_id=%s unique_sentences=%d duplicate_sentences=%d",
                self._response_debug_id,
                len(self._sentence_emit_counts),
                duplicate_sentences,
            )
            logger.debug(f"TTS: LLMFullResponseEndFrame received - buffer: '{self._text_buffer[:50] if self._text_buffer else 'empty'}...', session_text: '{self._session_text[:50] if self._session_text else 'empty'}...' (len={len(self._session_text) if self._session_text else 0})")
            # Process any remaining text
            if self._text_buffer.strip() and not self._cancelled:
                text = self._text_buffer.strip()
                # Add remaining text to session text for Telegram (avoid duplicates)
                if text not in self._session_text:
                    if self._session_text and not self._session_text.endswith((' ', '\n')):
                        self._session_text += ' '
                    self._session_text += text
                    logger.debug(f"TTS: Added remaining buffer to session_text (len={len(self._session_text)})")
                self._text_buffer = ""
                
                logger.debug(f"TTS: Processing remaining text: '{text[:50]}...'")

                # CRITICAL: Check if this is a Telegram request - if so, don't push audio frames to desktop
                import threading
                force_desktop_tts = bool(
                    getattr(threading.current_thread(), 'force_desktop_tts', False)
                    or getattr(self, '_force_desktop_tts', False)
                )
                has_telegram_request = hasattr(threading.current_thread(), 'telegram_request') and threading.current_thread().telegram_request

                # Use instance variable if available (persists across the TTS session)
                if (not force_desktop_tts) and hasattr(self, '_current_telegram_request') and self._current_telegram_request:
                    has_telegram_request = True

                # If not found on current thread or instance, check all threads (for thread pool scenarios)
                if (not force_desktop_tts) and (not has_telegram_request):
                    import threading as threading_module
                    for thread in threading_module.enumerate():
                        if hasattr(thread, 'telegram_request') and thread.telegram_request:
                            has_telegram_request = True
                            # Store it for future use
                            threading.current_thread().telegram_request = True
                            self._current_telegram_request = True
                            logger.debug(f"TTS: Found telegram_request=True on thread '{thread.name}' (remaining text) - stored")
                            break
                if force_desktop_tts:
                    has_telegram_request = False
                    self._current_telegram_request = False
                    logger.debug("TTS: force_desktop_tts=True for remaining text, bypassing telegram-only mode")

                audio_frame_count = 0
                frame_count = 0
                async for audio_frame in self.run_tts(text):
                    if self._cancelled:
                        logger.debug("TTS: Generation cancelled during remaining text processing")
                        break
                    frame_count += 1

                    # Filter out control frames first
                    if isinstance(audio_frame, (TTSStartedFrame, TTSStoppedFrame)):
                        # For Telegram requests, consume but don't push control frames
                        if has_telegram_request:
                            continue
                        # For desktop requests, control frames will be handled by the pipeline

                    # Debug: Log frame details before pushing (same as regular TextFrame processing)
                    if isinstance(audio_frame, AudioRawFrame) or (OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)):
                        if audio_frame_count == 0:
                            logger.debug(f"TTS: About to push first audio frame from remaining text: {len(audio_frame.audio)} bytes, sample_rate={audio_frame.sample_rate}, direction={direction}")

                    try:
                        if has_telegram_request:
                            # Telegram request - generate audio for duration calculation but don't push to desktop
                            if isinstance(audio_frame, AudioRawFrame) or (OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)):
                                audio_frame_count += 1
                            # CRITICAL: Don't push ANY frames to desktop for Telegram requests
                            continue

                        # Desktop request - process and push frames normally
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

                if has_telegram_request:
                    logger.debug(f"TTS: Generated {audio_frame_count} audio frames from remaining text for Telegram (not pushed to desktop)")
                elif audio_frame_count > 0:
                    logger.debug(f"TTS: Processed {audio_frame_count} audio frames from remaining text")

                if audio_frame_count > 0:
                    logger.debug(f"TTS: Processed {audio_frame_count} audio frames from remaining text")
                else:
                    logger.warning(f"TTS: No audio frames generated from remaining text")
            else:
                logger.debug("TTS: No remaining text to process (buffer empty or cancelled)")
            
            # End TTS session - emit stop event with total accumulated duration
            # BUT ONLY for desktop requests - Telegram requests should NOT trigger player events
            if self._tts_session_active:
                # Check if this is a Telegram request - if so, don't emit tts_stopped
                import threading
                force_desktop_tts = bool(
                    getattr(threading.current_thread(), 'force_desktop_tts', False)
                    or getattr(self, '_force_desktop_tts', False)
                )
                has_telegram_request = hasattr(threading.current_thread(), 'telegram_request') and threading.current_thread().telegram_request
                
                # Also check instance variable
                if (not force_desktop_tts) and hasattr(self, '_current_telegram_request') and self._current_telegram_request:
                    has_telegram_request = True
                
                # If not found on current thread, check all threads
                if (not force_desktop_tts) and (not has_telegram_request):
                    import threading as threading_module
                    for thread in threading_module.enumerate():
                        if hasattr(thread, 'telegram_request') and thread.telegram_request:
                            has_telegram_request = True
                            break
                if force_desktop_tts:
                    has_telegram_request = False
                
                total_duration = self._total_audio_duration
                self._tts_session_active = False
                self._total_audio_duration = 0.0

                # Only emit tts_stopped for desktop requests
                if not has_telegram_request:
                    # Ensure we emit a positive duration even if very small
                    # This prevents the player from being treated as an interrupt case
                    if total_duration <= 0.0 and self._tts_started_emitted:
                        # If we emitted tts_started but have no duration, use a small default
                        total_duration = 0.1  # Minimum duration to prevent premature closing

                    # Emit TTS stopped event to main process with total audio duration
                    # The GUI will wait for this duration before hiding the player window
                    if self.event_queue:
                        try:
                            self.event_queue.put(('tts_stopped', {'duration': total_duration}), block=False)
                            logger.debug(f"TTS: Sent tts_stopped event to main process (total duration: {total_duration:.2f}s)")
                        except Exception as e:
                            logger.debug(f"Could not emit tts_stopped event (non-blocking): {e}")
                else:
                    logger.debug("TTS: Skipping tts_stopped event (Telegram request - player should not be affected)")
            
            # CRITICAL: Send to Telegram if we have session text, REGARDLESS of _tts_session_active
            # The session text accumulates all text from TextFrames, even if TTS was interrupted or didn't generate audio
            # This ensures we always send the LLM response to Telegram, even if audio generation failed
            logger.debug(f"TTS: LLMFullResponseEndFrame - session_text len={len(self._session_text) if self._session_text else 0}")
            
            # CRITICAL: Clear session text IMMEDIATELY to prevent duplicate sends if this frame is processed multiple times
            session_text_to_send = self._session_text
            self._session_text = ""  # Clear immediately to prevent loops
            
            if session_text_to_send and self.event_queue:
                try:
                    # Check if a file was already sent (via tool chaining)
                    # If so, still generate TTS audio but skip sending Telegram text message
                    # Check both thread-local and instance variable (for cross-thread access)
                    import threading
                    file_already_sent = False
                    
                    # Check thread-local first
                    if hasattr(threading.current_thread(), 'telegram_file_sent'):
                        file_already_sent = threading.current_thread().telegram_file_sent
                    
                    # Also check instance variable (set by LLM service for cross-thread access)
                    if not file_already_sent and hasattr(self, '_telegram_file_sent'):
                        file_already_sent = self._telegram_file_sent
                    
                    # Store the flag for later use (we'll check it before sending to Telegram)
                    # But continue processing to generate TTS audio
                    if file_already_sent:
                        logger.debug(f"TTS: File already sent - will generate TTS audio but skip Telegram text message")
                        logger.debug(f"[Telegram TTS] ✅ Will generate TTS audio for 'Done' (file already sent, will skip Telegram text)")
                        # Clear the instance flag after we've noted it
                        if hasattr(self, '_telegram_file_sent'):
                            self._telegram_file_sent = False
                    
                    # Normalize text and check if it's "Done" or similar completion message
                    session_text_normalized = session_text_to_send.strip()
                    text_lower = session_text_normalized.lower()
                    is_done = text_lower in ['done', 'done.', 'complete', 'completed', 'finished', 'finished.']
                    
                    # Deduplication: Check if we've already sent this exact message
                    import hashlib
                    import time as _time_mod
                    message_content = f"{session_text_normalized}|{is_done}"
                    message_hash = hashlib.md5(message_content.encode()).hexdigest()
                    current_time = _time_mod.time()
                    
                    # Prevent duplicate sends within 2 seconds (do not return early — finish EndFrame downstream)
                    _telegram_duplicate_recent = (
                        message_hash == self._last_telegram_send_hash
                        and (current_time - self._last_telegram_send_time) < 2.0
                    )
                    if _telegram_duplicate_recent:
                        logger.warning(
                            f"⚠️ TTS: Duplicate send_to_telegram event detected and dropped "
                            f"(same message sent {current_time - self._last_telegram_send_time:.2f}s ago): "
                            f"'{session_text_normalized[:50]}...'"
                        )
                        logger.debug("[Telegram TTS] ⚠️ Duplicate event dropped (already sent recently)")
                    else:
                        self._last_telegram_send_hash = message_hash
                        self._last_telegram_send_time = current_time
                    
                    # Check if user requested to send raw screenshot directly
                    import threading
                    should_send_raw_screenshot = hasattr(threading.current_thread(), 'telegram_send_raw_screenshot')
                    raw_screenshot_path = None
                    if should_send_raw_screenshot:
                        raw_screenshot_path = threading.current_thread().telegram_send_raw_screenshot
                        logger.debug(f"TTS: User requested raw screenshot send - using raw screenshot: {raw_screenshot_path}")
                        logger.debug(f"[Telegram TTS] 📸 Using raw screenshot for direct send: {raw_screenshot_path}")
                    
                    # For "Done" messages, normalize to just "Done" to avoid duplicates
                    if is_done:
                        session_text_normalized = "Done"
                    
                    logger.debug(f"TTS: Preparing send_to_telegram event: text='{session_text_normalized[:100]}...' (len={len(session_text_normalized)}, is_done={is_done}, _tts_session_active={self._tts_session_active})")
                    
                    # Check if this request came from Telegram and if there's an analyzed image
                    # ALWAYS include screenshot for Telegram requests (whether it's analysis or direct send)
                    analyzed_image_path = None
                    has_telegram_request = hasattr(threading.current_thread(), 'telegram_request') and threading.current_thread().telegram_request
                    
                    # Use instance variable if available (persists across the TTS session)
                    if hasattr(self, '_current_telegram_request') and self._current_telegram_request:
                        has_telegram_request = True
                        logger.debug(f"TTS: Using stored _current_telegram_request=True for Telegram sending check")
                    
                    # If not found on current thread or instance, check all threads (for thread pool scenarios)
                    if not has_telegram_request:
                        import threading as threading_module
                        for thread in threading_module.enumerate():
                            if hasattr(thread, 'telegram_request') and thread.telegram_request:
                                has_telegram_request = True
                                # Store it for future use
                                threading.current_thread().telegram_request = True
                                self._current_telegram_request = True
                                logger.debug(f"TTS: Found telegram_request=True on thread '{thread.name}' (not current thread) - for Telegram sending check, stored")
                                logger.debug(f"[Telegram TTS] 🔍 Found telegram_request on thread '{thread.name}' for sending check")
                                break
                    
                    has_telegram_image = hasattr(threading.current_thread(), 'telegram_analyzed_image')
                    has_raw_screenshot = hasattr(threading.current_thread(), 'telegram_send_raw_screenshot')
                    
                    logger.debug(f"TTS: Telegram check - has_telegram_request={has_telegram_request}, has_telegram_image={has_telegram_image}, has_raw_screenshot={has_raw_screenshot}")
                    logger.debug(f"[Telegram TTS] 🔍 Telegram check: has_telegram_request={has_telegram_request}, text='{session_text_normalized[:50]}...'")
                    
                    # Check if this looks like a welcome message (system-initiated greeting)
                    # Welcome messages should be sent to Telegram if connected, even without telegram_request flag
                    text_lower_for_welcome = session_text_normalized.lower()
                    is_welcome_message = (
                        'hello' in text_lower_for_welcome and 
                        ('welcome' in text_lower_for_welcome or 
                         'welcome back' in text_lower_for_welcome or
                         "i'm your ai assistant" in text_lower_for_welcome or
                         "i'm ready to help" in text_lower_for_welcome or
                         "here to help" in text_lower_for_welcome or
                         "what would you like" in text_lower_for_welcome)
                    ) or (
                        not has_telegram_request and  # Not a user request
                        ('welcome back' in text_lower_for_welcome or
                         ('hello' in text_lower_for_welcome and 'welcome' in text_lower_for_welcome))
                    )
                    
                    # CRITICAL: Send to Telegram if:
                    # 1. This is a Telegram request (user sent a message from Telegram), OR
                    # 2. This is a welcome message (system-initiated greeting when Telegram is connected)
                    # Desktop-initiated user responses should NOT be sent to Telegram
                    # BUT: Skip Telegram send if file was already sent (file is the response, but we still want TTS audio)
                    should_send_to_telegram = (has_telegram_request or is_welcome_message) and not file_already_sent
                    
                    logger.info(f"[Telegram TTS] Decision: send={should_send_to_telegram} (telegram_req={has_telegram_request}, welcome={is_welcome_message}, file_sent={file_already_sent}, text='{session_text_normalized[:80]}...')")
                    
                    if should_send_to_telegram and not _telegram_duplicate_recent:
                        if is_welcome_message and not has_telegram_request:
                            logger.debug(f"TTS: Welcome message detected - will send to Telegram even though not a Telegram request")
                            logger.debug(f"[Telegram TTS] 👋 Welcome message detected - sending to Telegram")
                        # Priority 1: If user requested raw screenshot send, use that
                        if should_send_raw_screenshot and raw_screenshot_path:
                            analyzed_image_path = raw_screenshot_path
                            logger.debug(f"TTS: Using raw screenshot for direct send: {analyzed_image_path}")
                            logger.debug(f"[Telegram TTS] 📸 Using raw screenshot: {analyzed_image_path}")
                        
                        # Priority 2: ALWAYS check for stored screenshot from vision tools (for analysis requests)
                        # This ensures screenshots are included even when user asks "tell me what you see"
                        # Only use this if we don't already have a raw screenshot path
                        if not analyzed_image_path and has_telegram_image:
                            analyzed_image_path = threading.current_thread().telegram_analyzed_image
                            logger.debug(f"TTS: Found analyzed screenshot for Telegram response: {analyzed_image_path}")
                            logger.debug(f"[Telegram TTS] 📸 Including screenshot with analysis: {analyzed_image_path}")
                        elif not analyzed_image_path:
                            # Welcome messages never have a screenshot context — that's expected, not a warning
                            if not is_welcome_message:
                                logger.warning("TTS: Telegram request but no screenshot found in thread - screenshot should have been stored!")
                                logger.debug("[Telegram TTS] \u26a0\ufe0f No screenshot found for Telegram response")
                            # Try to get it from raw screenshot if available
                            if has_raw_screenshot:
                                analyzed_image_path = threading.current_thread().telegram_send_raw_screenshot
                                logger.debug(f"TTS: Found raw screenshot path: {analyzed_image_path}")
                                logger.debug(f"[Telegram TTS] 📸 Using raw screenshot path: {analyzed_image_path}")
                        # CRITICAL: If we have a Telegram request but no screenshot yet, check ALL threads for the screenshot
                        # This handles cases where the tool ran in a different thread
                        if not analyzed_image_path:
                            # Try to find screenshot in any thread's local storage
                            import threading as threading_module
                            for thread in threading_module.enumerate():
                                if hasattr(thread, 'telegram_analyzed_image'):
                                    analyzed_image_path = thread.telegram_analyzed_image
                                    logger.debug(f"TTS: Found screenshot in thread '{thread.name}': {analyzed_image_path}")
                                    logger.debug(f"[Telegram TTS] 📸 Found screenshot in thread '{thread.name}': {analyzed_image_path}")
                                    break
                                if hasattr(thread, 'telegram_send_raw_screenshot'):
                                    analyzed_image_path = thread.telegram_send_raw_screenshot
                                    logger.debug(f"TTS: Found raw screenshot in thread '{thread.name}': {analyzed_image_path}")
                                    logger.debug(f"[Telegram TTS] 📸 Found raw screenshot in thread '{thread.name}': {analyzed_image_path}")
                                    break
                        
                            # ALSO check shared storage from screenshot_analyzer and vision_analyzer (cross-thread fallback)
                            if not analyzed_image_path:
                                try:
                                    from distr.core.agent.tools.vision.screenshot_analyzer import ScreenshotAnalyzerTool
                                    from distr.core.agent.tools.vision.vision_analyzer import VisionAnalyzerTool
                                    if ScreenshotAnalyzerTool._last_telegram_screenshot and os.path.exists(ScreenshotAnalyzerTool._last_telegram_screenshot):
                                        analyzed_image_path = ScreenshotAnalyzerTool._last_telegram_screenshot
                                        ScreenshotAnalyzerTool._last_telegram_screenshot = None  # Clear after use
                                        logger.debug(f"TTS: Found screenshot from shared storage (cross-thread): {analyzed_image_path}")
                                        logger.debug(f"[Telegram TTS] 📸 Using screenshot from ScreenshotAnalyzerTool._last_telegram_screenshot")
                                    elif VisionAnalyzerTool._last_telegram_image and os.path.exists(VisionAnalyzerTool._last_telegram_image):
                                        analyzed_image_path = VisionAnalyzerTool._last_telegram_image
                                        VisionAnalyzerTool._last_telegram_image = None  # Clear after use
                                        logger.debug(f"TTS: Found image from vision_analyzer shared storage: {analyzed_image_path}")
                                        logger.debug(f"[Telegram TTS] 📸 Using image from VisionAnalyzerTool._last_telegram_image")
                                except Exception as e:
                                    logger.debug(f"TTS: Could not check for shared screenshot/image: {e}")
                            # ALSO check for generated images from image_generator tool (class variable)
                            if not analyzed_image_path:
                                try:
                                    from distr.core.agent.tools.vision.image_generator import ImageGeneratorTool
                                    if ImageGeneratorTool._last_generated_image and os.path.exists(ImageGeneratorTool._last_generated_image):
                                        analyzed_image_path = ImageGeneratorTool._last_generated_image
                                        # Clear it after use to prevent duplicate sends
                                        ImageGeneratorTool._last_generated_image = None
                                        logger.debug(f"TTS: Found generated image from image_generator: {analyzed_image_path}")
                                        logger.debug(f"[Telegram TTS] 🎨 Including generated image: {analyzed_image_path}")
                                except Exception as e:
                                    logger.debug(f"TTS: Could not check for generated image: {e}")
                        
                        # Log what we're sending
                        logger.debug(f"TTS: Sending to Telegram - text='{session_text_normalized[:100]}...', is_done={is_done}, analyzed_image_path={analyzed_image_path}")
                        logger.debug(f"[Telegram TTS] 📤 Sending: text='{session_text_normalized[:50]}...', is_done={is_done}, screenshot={analyzed_image_path is not None}")
                        
                        self.event_queue.put(('send_to_telegram', {
                            'text': session_text_normalized,
                            'is_done': is_done,
                            'provider': 'kokoro',  # Only send for Kokoro TTS
                            'analyzed_image_path': analyzed_image_path,  # Image from vision analysis (if from Telegram)
                            'explicit_artifact_intent': bool(should_send_raw_screenshot),
                        }), block=False)
                        logger.debug(f"✅ TTS: Sent send_to_telegram event with text='{session_text_normalized[:100]}...' (is_done={is_done}, provider=kokoro, screenshot={analyzed_image_path is not None})")
                        logger.debug(f"📤 Telegram: '{session_text_normalized[:100]}' {f'+ screenshot' if analyzed_image_path else ''}")
                    else:
                        # Desktop request - do NOT send to Telegram
                        logger.debug(f"TTS: Desktop request - NOT sending to Telegram (text='{session_text_normalized[:100]}...')")
                        logger.debug(f"[Desktop TTS] 🔊 Desktop request - audio played on desktop, NOT sent to Telegram")
                except Exception as e:
                    logger.error(f"❌ Could not emit send_to_telegram event: {e}", exc_info=True)
                    logger.error(f"[Telegram TTS] ❌ Failed to send event: {e}")
            elif session_text_to_send and not self.event_queue:
                logger.warning(f"⚠️ TTS: Has session text but no event_queue! Text: '{session_text_to_send[:100]}...'")
            elif not session_text_to_send:
                logger.warning(f"⚠️ TTS: No session text to send to Telegram! (_tts_session_active={self._tts_session_active})")
            
            # Session text already cleared above to prevent loops
            
            # Clear Telegram request flag and analyzed image after sending
            # This is LLMFullResponseEndFrame, so we're at the end of the response - always clear
            import threading
            for _tg_attr in ('telegram_send_raw_screenshot', 'telegram_analyzed_image', 'telegram_request', 'telegram_file_sent'):
                if hasattr(threading.current_thread(), _tg_attr):
                    try:
                        delattr(threading.current_thread(), _tg_attr)
                    except Exception:
                        pass
            # Also clear class-level screenshot references
            try:
                from distr.core.agent.tools.vision.screenshot_analyzer import ScreenshotAnalyzerTool
                ScreenshotAnalyzerTool._last_telegram_screenshot = None
            except Exception:
                pass
            try:
                from distr.core.agent.tools.vision.vision_analyzer import VisionAnalyzerTool
                VisionAnalyzerTool._last_telegram_image = None
            except Exception:
                pass
            logger.debug(f"[Telegram TTS] 🧹 Cleared ALL telegram flags (response complete)")
            
            # End of LLM response: safe to accept CancelFrame / non-stale InterruptionFrame logic again
            self._in_response_after_start = False
            self._llm_response_started_at = 0

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
        """Return the output sample rate for Kokoro (24kHz)"""
        return 24000
