"""Base STT service with shared state, PTT, hands-free, and frame handling."""
import asyncio
import logging
import string
import time
import numpy as np
from collections import deque

from distr.core.agent.libs import (
    STTService,
    InterruptionFrame, UserStartedSpeakingFrame, TranscriptionFrame,
    SpeakingStartedFrames, SpeakingStoppedFrames,
)
from distr.core.agent.constants import (
    VAD_DEFAULT_THRESHOLD,
    VAD_BARGEIN_MULTIPLIER_MIN,
    VAD_BARGEIN_MULTIPLIER_MAX,
    VAD_BARGEIN_FLOOR_MIN,
    VAD_BARGEIN_FLOOR_MAX,
    VAD_BARGEIN_CONSECUTIVE_MIN,
    VAD_BARGEIN_CONSECUTIVE_MAX,
)

logger = logging.getLogger(__name__)

# Shared across all STT services - no need to duplicate per-service
_FILLER_WORDS = {
    "um", "uh", "er", "ah", "hmm", "hmmm", "mm", "mmhmm", "huh",
    "like", "you know", "well", "so", "actually", "basically",
}
_AUDIO_ARTIFACTS = {a.lower() for a in [
    "(clears throat)", "[blank audio]", "[no audio]",
    "[clapping]", "(clapping)", "[laughter]", "[laugh]",
    "(laughter)", "(laugh)", "[music]", "(music)",
    "[bleep]", "(bleep)", "[beep]", "(beep)",
    "[bell]", "(bell)", "(bell ringing)", "(bell dings)",
    "[static]", "[popping]", "(popping)",
    "[silence]", "(silence)", "[sigh]", "(sigh)",
    "(sighs)", "[sighing]", "(sighing)", "[applause]",
    "(applause)", "(clicking)", "(coughing)", "(knocking)",
    "[coughing]", "[tapping]", "(beatboxing)", "(tapping)",
    "[dog barks]", "(cough)", "(breathing heavily)",
    "[BLANK_AUDIO]", "[BLANK]", "blank_audio", "blankaudio", "blank",
    "thank you", "thanks", "(dramatic music)", "(soft music)",
    "dramatic music", "soft music",
]}


class BaseSTTService(STTService):
    """
    Common base for all STT services.

    Centralises state management, PTT activation/deactivation, hands-free
    interruption, filler/artifact filtering, and the SpeakingStarted/Stopped
    frame handling that was previously copy-pasted in every service.

    Subclasses must implement:
        run_stt(audio: bytes) -> AsyncGenerator[Frame]
        process_frame(frame, direction)  (call super helpers from within)

    Subclasses may override:
        _on_speaking_started(frame, direction)  - service-specific setup
        _on_speaking_stopped(frame, direction)   - service-specific teardown
    """

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(self, *, event_queue=None, is_hands_free: bool = False, aec_ref_buf=None, **kwargs):
        super().__init__(**kwargs)

        # Audio buffers
        self._audio_buffer: list = []
        self._ptt_buffer_accumulator: list = []
        self._pre_buffer: deque = deque(maxlen=15)  # ~300 ms rolling buffer

        # State flags
        self._user_speaking: bool = False
        self._ptt_active: bool = False
        self._is_hands_free: bool = is_hands_free
        self._is_dictating: bool = False
        self._stt_cancelled: bool = False
        self._pending_interruption: bool = False
        self._pending_ptt_process: bool = False
        self._ptt_flush_scheduled: bool = False

        # AEC reference buffer — used to gate VAD interruptions during TTS playback.
        # When TTS is playing (ref_buf.is_active), VAD may fire on residual echo
        # that the NLMS filter couldn't fully cancel. We suppress those false
        # interruptions unless the mic energy is high enough to indicate real speech.
        self._aec_ref_buf = aec_ref_buf

        # --- Adaptive echo floor tracking ---
        # Instead of a fixed RMS threshold, we track the actual echo residual
        # level during TTS playback (after the grace period) and set the
        # barge-in threshold dynamically above it. This adapts to different
        # speaker volumes, room acoustics, and AEC convergence quality.
        #
        # Echo floor = exponential moving average of mic RMS during TTS playback.
        # Barge-in threshold = echo_floor * multiplier (with a minimum floor).
        # This way if echo residual is 0.035, threshold becomes ~0.063 (1.8x),
        # and real speech (typically 0.08+) still passes comfortably.
        self._echo_floor_rms: float = 0.035  # initial estimate (conservative — real echo residual is often 0.02-0.04)
        self._echo_floor_alpha: float = 0.08  # EMA smoothing (faster adaptation to catch real residual level quickly)
        self._echo_floor_multiplier: float = 1.8  # threshold = floor * this (wider margin to avoid false barge-in)
        self._echo_floor_min: float = 0.04  # absolute minimum threshold (echo residual is rarely below this)
        self._echo_floor_samples: int = 0  # how many samples contributed

        # Consecutive-chunk debounce for barge-in detection.
        # Require N consecutive high-energy chunks before triggering barge-in.
        # At 20ms/chunk, 10 chunks = 200ms of sustained energy — echo residual
        # fluctuates and rarely sustains for that long, but real speech does.
        self._bargein_consecutive_required: int = 10
        self._bargein_consecutive_count: int = 0

        # When the echo gate suppresses a SpeakingStartedFrame, the user might
        # actually be speaking (energy just hadn't built up yet, or AEC hadn't
        # converged). We set this flag and keep checking energy on subsequent
        # audio frames. If energy rises above threshold, we trigger barge-in
        # retroactively. Without this, a single suppressed SpeakingStartedFrame
        # means no barge-in for the entire TTS session (VAD won't fire again
        # because the user never stopped speaking).
        self._pending_bargein_check: bool = False

        # Callback to cancel the welcome message task on barge-in.
        # Set by the session after pipeline creation.
        self._cancel_welcome_callback = None

        # Track whether we already fired an interruption for the current TTS
        # session. Reset when TTS stops (ref_buf deactivates). This prevents
        # firing repeated interruptions every audio frame while the user is
        # still speaking and TTS hasn't stopped yet.
        self._tts_interrupted: bool = False

        # Pipeline references (set at runtime)
        self._pipeline_direction = None
        self._event_loop = None
        self._current_stt_task = None

        # Event queue for GUI feedback
        self.event_queue = event_queue

        # Minimum audio duration before transcription (subclasses may override)
        self._min_audio_duration_ms: int = 1000

        # Filler / artifact filtering (shared module-level sets)
        self._filler_words = _FILLER_WORDS
        self._audio_artifacts = _AUDIO_ARTIFACTS

        self._vad_threshold: int = VAD_DEFAULT_THRESHOLD
        self.set_vad_threshold(self._vad_threshold)

    # ------------------------------------------------------------------
    # Text filtering
    # ------------------------------------------------------------------

    def _is_meaningful_text(self, text: str) -> bool:
        if not text:
            return False
        text_lower = text.strip().lower()
        if text_lower in self._audio_artifacts:
            return False
        # Also check after stripping trailing punctuation (e.g. "[BLANK_AUDIO].")
        text_stripped = text_lower.rstrip(string.punctuation).strip()
        if text_stripped in self._audio_artifacts:
            return False
        text_no_punct = text_lower.translate(
            str.maketrans("", "", string.punctuation)
        ).strip()
        if text_no_punct in self._filler_words:
            return False
        # Catch bracket-wrapped artifacts not in the list (e.g. "[inaudible]", "(noise)")
        if text_stripped.startswith(('[', '(')) and text_stripped.endswith((']', ')')):
            return False
        return True

    # ------------------------------------------------------------------
    # Mode setters
    # ------------------------------------------------------------------

    def set_hands_free(self, enabled: bool):
        """Set hands-free mode.  Override in subclasses that need to
        connect/disconnect a streaming API when the mode changes."""
        old = self._is_hands_free
        self._is_hands_free = enabled
        logger.info(f"STT hands-free mode: {old} -> {enabled}")

    def set_dictating(self, enabled: bool):
        old = self._is_dictating
        self._is_dictating = enabled
        logger.info(f"STT dictation mode: {old} -> {enabled}")

    def set_vad_threshold(self, threshold: int):
        """Tune hands-free echo-gate sensitivity from the shared VAD slider.

        Lower values make continuous-mode barge-in more sensitive.
        Higher values demand stronger, more sustained speech before we
        treat mic energy during TTS playback as real user speech.
        """
        threshold = max(0, min(100, int(threshold)))
        strictness = threshold / 100.0
        self._vad_threshold = threshold
        self._echo_floor_multiplier = (
            VAD_BARGEIN_MULTIPLIER_MIN
            + ((VAD_BARGEIN_MULTIPLIER_MAX - VAD_BARGEIN_MULTIPLIER_MIN) * strictness)
        )
        self._echo_floor_min = (
            VAD_BARGEIN_FLOOR_MIN
            + ((VAD_BARGEIN_FLOOR_MAX - VAD_BARGEIN_FLOOR_MIN) * strictness)
        )
        span = VAD_BARGEIN_CONSECUTIVE_MAX - VAD_BARGEIN_CONSECUTIVE_MIN
        self._bargein_consecutive_required = (
            VAD_BARGEIN_CONSECUTIVE_MIN + round(span * strictness)
        )
        logger.info(
            "STT VAD threshold set to %s -> barge-in multiplier=%.2f floor=%.3f chunks=%s",
            threshold,
            self._echo_floor_multiplier,
            self._echo_floor_min,
            self._bargein_consecutive_required,
        )

    # ------------------------------------------------------------------
    # PTT activation / deactivation
    # ------------------------------------------------------------------

    def set_ptt_active(self, active: bool, *, queue_interruption: bool = True):
        """Full PTT state machine — shared across all STT services."""
        was_active = self._ptt_active
        self._ptt_active = active
        if active == was_active:
            return

        if not active:
            # PTT released — clear interrupt flag and cancelled so next PTT cycle is clean
            self._pending_interruption = False
            self._stt_cancelled = False

        logger.debug(f"STT: PTT state changed: {was_active} -> {active} (pipeline_direction={self._pipeline_direction is not None}, event_loop={self._event_loop is not None})")

        if self.event_queue is None:
            logger.debug("STT: event_queue is None, skipping GUI events")

        if active:
            # --- PTT just activated ---
            logger.debug("STT: PTT ACTIVATED - capturing audio")
            self._ptt_buffer_accumulator = []
            self._ptt_flush_scheduled = False
            self._pending_ptt_process = False
            # Dictation must keep capturing; only agent PTT clears in-flight STT.
            self._stt_cancelled = bool(queue_interruption)
            self._audio_buffer = []
            self._pending_interruption = bool(queue_interruption)

            try:
                self.event_queue.put(("stt_capture_started", {}), block=False)
            except Exception as e:
                logger.debug(f"Could not emit stt_capture_started: {e}")

            if queue_interruption:
                # Fallback for STT restore paths that do not go through command_handler.
                if (self._pipeline_direction is not None
                        and self._event_loop is not None
                        and isinstance(self._event_loop, asyncio.AbstractEventLoop)
                        and self._event_loop.is_running() is True):
                    coro = None
                    try:
                        coro = self._send_interruption(self._pipeline_direction)
                        asyncio.run_coroutine_threadsafe(
                            coro,
                            self._event_loop,
                        )
                        logger.debug("STT: Scheduled immediate InterruptionFrame on PTT activation")
                    except Exception as e:
                        if coro is not None:
                            coro.close()
                        logger.warning(f"STT: Could not send immediate interruption: {e}")
                else:
                    if self._pipeline_direction is None:
                        logger.debug(
                            "STT: PTT activated but pipeline_direction not set yet - "
                            "InterruptionFrame will be sent when first frame arrives"
                        )
                    elif self._event_loop is None or not (getattr(self._event_loop, "is_running", lambda: False)()):
                        logger.debug("STT: event_loop not available for immediate InterruptionFrame")
        else:
            # --- PTT just released ---
            try:
                self.event_queue.put(("stt_capture_stopped", {}), block=False)
            except Exception as e:
                logger.debug(f"Could not emit stt_capture_stopped: {e}")

            self._merge_pre_buffer_into_ptt_on_release()

            if self._ptt_buffer_accumulator:
                self._pending_ptt_process = True
                n = len(self._ptt_buffer_accumulator)
                total = sum(len(c) for c in self._ptt_buffer_accumulator)
                dur = (total / (16000 * 2)) * 1000
                logger.info(
                    "STT: PTT released, buffer has %s chunks (~%.0fms) — flushing immediately",
                    n,
                    dur,
                )
                self._schedule_immediate_ptt_buffer_flush()
            else:
                logger.warning(
                    "STT: PTT released but buffer empty — no audio captured "
                    "(dictating=%s, hands_free=%s)",
                    self._is_dictating,
                    self._is_hands_free,
                )
                if self._is_dictating:
                    self._schedule_dictation_empty_transcription_unblock()

    def _merge_pre_buffer_into_ptt_on_release(self):
        """Fold rolling pre-buffer frames collected during dictation/PTT hand-off."""
        if not self._pre_buffer:
            return
        merged = len(self._pre_buffer)
        self._ptt_buffer_accumulator.extend(self._pre_buffer)
        self._pre_buffer.clear()
        logger.info("STT: Merged %s pre-buffer chunk(s) into PTT buffer on release", merged)

    def _schedule_immediate_ptt_buffer_flush(self):
        """Run PTT transcription on the pipeline loop (do not wait for another mic frame)."""
        direction = self._pipeline_direction
        loop = self._event_loop
        if direction is None:
            logger.warning(
                "STT: PTT buffer flush deferred — pipeline_direction not set yet "
                "(will retry on next frame)"
            )
            return
        if loop is None or not getattr(loop, "is_running", lambda: False)():
            logger.warning(
                "STT: PTT buffer flush deferred — event loop not running "
                "(will retry on next frame)"
            )
            return
        if not hasattr(self, "_process_ptt_buffer_immediate"):
            return
        if self._ptt_flush_scheduled:
            return

        self._ptt_flush_scheduled = True

        async def _flush_ptt_buffer():
            self._ptt_flush_scheduled = False
            if not self._ptt_buffer_accumulator:
                self._pending_ptt_process = False
                return
            self._pending_ptt_process = False
            try:
                await self._process_ptt_buffer_immediate(direction)
            except Exception as exc:
                logger.error("STT: PTT buffer flush failed: %s", exc, exc_info=True)

        try:
            asyncio.run_coroutine_threadsafe(_flush_ptt_buffer(), loop)
        except Exception as exc:
            self._ptt_flush_scheduled = False
            logger.warning("STT: Could not schedule PTT buffer flush: %s", exc)

    def _schedule_dictation_empty_transcription_unblock(self):
        """End one-shot dictation when capture produced no audio (avoids 60s stuck state)."""
        if self.event_queue:
            try:
                self.event_queue.put(("stop_dictation", {}), block=False)
                logger.info(
                    "STT: Queued stop_dictation after empty dictation capture "
                    "(no mic audio captured)"
                )
                return
            except Exception as exc:
                logger.warning("STT: Could not queue stop_dictation for empty capture: %s", exc)

        direction = self._pipeline_direction
        loop = self._event_loop
        if direction is None or loop is None or not getattr(loop, "is_running", lambda: False)():
            logger.warning(
                "STT: Dictation stuck-unblock skipped — pipeline not ready "
                "(dictation may time out after 60s)"
            )
            return

        async def _emit_empty():
            try:
                frame = TranscriptionFrame(text="", user_id="", timestamp=time.time())
                await self.push_frame(frame, direction)
                logger.info(
                    "STT: Sent empty TranscriptionFrame to end one-shot dictation "
                    "(no mic audio captured)"
                )
            except Exception as exc:
                logger.warning("STT: Could not unblock dictation with empty transcript: %s", exc)

        try:
            asyncio.run_coroutine_threadsafe(_emit_empty(), loop)
        except Exception as exc:
            logger.warning("STT: Could not schedule dictation unblock: %s", exc)

    # ------------------------------------------------------------------
    # Interruption helper
    # ------------------------------------------------------------------

    async def _send_interruption(self, direction):
        """Push an InterruptionFrame downstream to kill current TTS/LLM."""
        try:
            await self.push_frame(InterruptionFrame(), direction)
            logger.debug("STT: InterruptionFrame sent")
        except Exception as e:
            logger.error(f"Error sending InterruptionFrame: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Common frame-processing helpers  (called from subclass process_frame)
    # ------------------------------------------------------------------

    def _store_pipeline_context(self, direction):
        """Store direction and event loop for use in set_ptt_active (thread-safe)."""
        self._pipeline_direction = direction
        if self._event_loop is None:
            try:
                self._event_loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    self._event_loop = asyncio.get_event_loop()
                except RuntimeError:
                    self._event_loop = None

    async def _handle_pending_interruption(self, direction):
        """Send pending InterruptionFrame if PTT was just activated."""
        if not self._pending_interruption:
            return
        logger.debug("STT: Sending pending InterruptionFrame (from PTT activation)")
        self._pending_interruption = False
        try:
            await self._send_interruption(direction)
        except Exception as e:
            logger.error(f"STT: pending interruption failed: {e}")
            self._pending_interruption = True  # retry next frame

    def _should_filter_interruption(self, frame) -> bool:
        """Return True if an incoming InterruptionFrame should be swallowed.
        In PTT mode, VAD-generated InterruptionFrames are not expected (we
        suppress _allow_interruptions during TTS), but filter as safety net."""
        if not isinstance(frame, InterruptionFrame):
            return False
        if self._is_hands_free or self._is_dictating:
            return False
        if self._ptt_active:
            # PTT sends its own InterruptionFrame — let it through
            return False
        logger.debug("STT: Filtering unexpected InterruptionFrame (PTT mode, no active PTT)")
        return True

    # ------------------------------------------------------------------
    # Barge-in gate (echo suppression during TTS playback)
    # ------------------------------------------------------------------

    def _is_tts_playing(self) -> bool:
        """Return True if TTS audio is currently being played through the speaker."""
        if self._aec_ref_buf is not None:
            return self._aec_ref_buf.is_active
        return False

    def _get_adaptive_threshold(self) -> float:
        """Return the current barge-in RMS threshold based on the echo floor.

        The threshold adapts to the actual echo residual level measured during
        TTS playback. This handles varying speaker volumes, room acoustics,
        and AEC convergence quality without manual tuning.

        Returns echo_floor * multiplier, clamped to a minimum.
        """
        threshold = max(
            self._echo_floor_rms * self._echo_floor_multiplier,
            self._echo_floor_min,
        )
        return threshold

    def _update_echo_floor(self, chunk_rms: float):
        """Update the echo floor estimate with a new mic RMS sample.

        Called on every audio frame during TTS playback (after the grace
        period). Uses an exponential moving average so the floor adapts
        slowly — transient spikes (user coughing, brief noise) don't
        inflate it, but sustained echo level changes are tracked.

        Only updates when the chunk looks like echo (below a reasonable
        ceiling). If the user is actively speaking over TTS, those high-RMS
        frames shouldn't pull the echo floor up.
        """
        # Don't let speech-level RMS contaminate the echo floor estimate.
        # Echo residual after AEC is typically 0.01-0.05. Anything above
        # 0.06 is almost certainly speech or a loud transient.
        if chunk_rms > 0.06:
            return

        self._echo_floor_samples += 1
        if self._echo_floor_samples <= 5:
            # Bootstrap: use a faster alpha for the first few samples
            alpha = 0.3
        else:
            alpha = self._echo_floor_alpha
        self._echo_floor_rms = (1 - alpha) * self._echo_floor_rms + alpha * chunk_rms

    def _check_bargein_energy(self) -> bool:
        """Check if the pre-buffer audio has enough energy to be real speech.

        Uses an adaptive threshold based on the measured echo floor:
        1. Grace period (~500ms) after TTS starts — AEC weights are zero and
           the first echo spike can exceed the threshold before convergence.
        2. Consecutive-chunk check — require N consecutive chunks above the
           adaptive threshold. Real speech sustains energy; echo doesn't.

        Returns True if energy is high enough to allow barge-in.
        """
        # Grace period: suppress all barge-in for the first 800ms after TTS
        # starts, regardless of energy. AEC weights are zero at the start of
        # each TTS session and need time to converge. 500ms was too short —
        # echo residual spikes during convergence were passing the threshold.
        if self._aec_ref_buf is not None:
            elapsed = self._aec_ref_buf.seconds_since_activation()
            if elapsed < 0.8:
                logger.debug(f"STT: Barge-in suppressed (grace period, {elapsed:.3f}s since TTS start)")
                return False

        if not self._pre_buffer:
            return False

        threshold = self._get_adaptive_threshold()

        try:
            # Scan the pre-buffer from newest to oldest, counting consecutive
            # chunks above threshold. If we find N consecutive, it's real speech.
            consecutive = 0
            rms_values = []
            for chunk_bytes in reversed(self._pre_buffer):
                chunk_f32 = np.frombuffer(chunk_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                chunk_rms = float(np.sqrt(np.mean(chunk_f32 ** 2)))
                rms_values.append(chunk_rms)
                if chunk_rms >= threshold:
                    consecutive += 1
                    if consecutive >= self._bargein_consecutive_required:
                        logger.debug(f"STT: Barge-in allowed ({consecutive} consecutive chunks above {threshold:.4f} [echo_floor={self._echo_floor_rms:.4f}], rms={chunk_rms:.4f})")
                        return True
                else:
                    break  # streak broken — not sustained speech

            # Log RMS values so we can diagnose threshold issues
            rms_str = ", ".join(f"{r:.4f}" for r in rms_values[:6])
            logger.debug(f"STT: Barge-in suppressed ({consecutive}/{self._bargein_consecutive_required} consecutive, threshold={threshold:.4f} [echo_floor={self._echo_floor_rms:.4f}], recent_rms=[{rms_str}])")
            return False
        except Exception as e:
            logger.debug(f"STT: Barge-in energy check failed: {e}, allowing through")
            return True  # fail-open: allow interruption if check fails

    # ------------------------------------------------------------------
    # Deferred barge-in check (runs per audio frame after suppression)
    # ------------------------------------------------------------------

    async def _check_pending_bargein(self, audio_bytes, direction):
        """Called on every audio frame when a SpeakingStartedFrame was suppressed.

        The echo gate may suppress a SpeakingStartedFrame because energy was
        too low at that instant (AEC convergence, mic ramp-up). But the user
        might actually be speaking. We keep checking each audio frame's energy
        using the same consecutive-chunk debounce. If energy builds up, we
        trigger the barge-in retroactively.

        Also feeds the adaptive echo floor tracker on every frame during TTS
        playback so the threshold stays calibrated.
        """
        # Always update echo floor during TTS playback (even if no pending check)
        if self._is_tts_playing() and self._aec_ref_buf is not None:
            elapsed = self._aec_ref_buf.seconds_since_activation()
            if elapsed >= 0.8:  # only after grace period
                try:
                    ef32 = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                    erms = float(np.sqrt(np.mean(ef32 ** 2)))
                    self._update_echo_floor(erms)
                except Exception:
                    pass

        if not self._pending_bargein_check:
            return

        if not self._is_tts_playing():
            # TTS stopped — no need to barge in anymore
            self._pending_bargein_check = False
            self._bargein_consecutive_count = 0
            return

        if self._ptt_active:
            return

        if not (self._is_hands_free or self._is_dictating):
            return

        # Grace period
        if self._aec_ref_buf is not None:
            elapsed = self._aec_ref_buf.seconds_since_activation()
            if elapsed < 0.8:
                self._bargein_consecutive_count = 0
                logger.debug(f"STT: Deferred barge-in suppressed (grace period, {elapsed:.3f}s)")
                return

        threshold = self._get_adaptive_threshold()

        # Check this chunk's energy
        try:
            chunk_f32 = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            chunk_rms = float(np.sqrt(np.mean(chunk_f32 ** 2)))

            if chunk_rms >= threshold:
                self._bargein_consecutive_count += 1
            else:
                self._bargein_consecutive_count = 0

            if self._bargein_consecutive_count < self._bargein_consecutive_required:
                return
        except Exception:
            return

        # Energy confirmed — trigger barge-in retroactively
        confirmed_count = self._bargein_consecutive_count
        self._pending_bargein_check = False
        self._bargein_consecutive_count = 0

        mode = "dictation" if self._is_dictating else "hands-free"
        logger.info(f"STT: Deferred barge-in confirmed ({mode}) — {confirmed_count}/{self._bargein_consecutive_required} consecutive chunks above {threshold:.4f} [echo_floor={self._echo_floor_rms:.4f}], last rms={chunk_rms:.4f}")

        self._user_speaking = True
        # Do NOT seed from _pre_buffer — that audio was captured during TTS
        # playback and is echo-contaminated (AEC weights still converging).
        # Start with a fresh buffer. Post-interrupt audio will be clean because
        # the transport sets ref_buf.set_active(False) on InterruptionFrame,
        # and the AEC filter passes audio through unprocessed when inactive.
        self._audio_buffer = []
        self._pre_buffer.clear()

        # Call the subclass hook so service-specific setup runs (e.g. streaming
        # API reset). Create a synthetic frame since we don't have the original.
        synthetic_frame = UserStartedSpeakingFrame()
        await self._on_speaking_started(synthetic_frame, direction)

        logger.debug("STT: Deferred barge-in — triggering pipeline interruption")
        await self.push_interruption_task_frame_and_wait()

        if self._cancel_welcome_callback:
            self._cancel_welcome_callback()

        if self._is_hands_free and self.event_queue:
            try:
                self.event_queue.put(("stt_hands_free_glow_on", {}), block=False)
            except Exception:
                pass

        # Push the speaking-started frame downstream so the rest of the
        # pipeline knows the user is speaking (matches _handle_speaking_started).
        await self.push_frame(synthetic_frame, direction)

    # ------------------------------------------------------------------
    # Continuous-speech re-interruption
    # ------------------------------------------------------------------

    async def _check_continuous_speech_interruption(self, audio_bytes, direction):
        """Re-interrupt if the user is still speaking when a new TTS response starts.

        After a barge-in, the user's speech is transcribed and a new LLM response
        begins. If the user keeps talking without pausing, VAD never fires a new
        SpeakingStartedFrame (the user never stopped). The new TTS plays
        uninterrupted because there's no second barge-in trigger.

        This method is called on every audio frame from the subclass. It checks:
        1. Is the user currently speaking? (_user_speaking == True)
        2. Is TTS playing? (_is_tts_playing() == True)
        3. Have we already interrupted this TTS session? (_tts_interrupted)
        4. Does the audio have enough consecutive energy? (debounce)

        Uses the same consecutive-chunk debounce as _check_bargein_energy and
        test_aec_live.py: require N consecutive high-energy chunks before firing.
        """
        if not self._user_speaking:
            self._tts_interrupted = False
            self._bargein_consecutive_count = 0
            return

        if not self._is_tts_playing():
            self._tts_interrupted = False
            self._bargein_consecutive_count = 0
            return

        if self._tts_interrupted:
            return

        if self._ptt_active:
            return

        if not (self._is_hands_free or self._is_dictating):
            return

        # Grace period for new TTS session
        if self._aec_ref_buf is not None:
            elapsed = self._aec_ref_buf.seconds_since_activation()
            if elapsed < 0.8:
                self._bargein_consecutive_count = 0
                return

        # Check this audio frame's energy using consecutive-chunk debounce
        threshold = self._get_adaptive_threshold()
        try:
            chunk_f32 = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            chunk_rms = float(np.sqrt(np.mean(chunk_f32 ** 2)))

            if chunk_rms >= threshold:
                self._bargein_consecutive_count += 1
            else:
                self._bargein_consecutive_count = 0

            if self._bargein_consecutive_count < self._bargein_consecutive_required:
                return
        except Exception:
            return

        # User is still speaking with sustained energy while new TTS is playing.
        logger.info("STT: User still speaking during new TTS — re-interrupting pipeline")
        self._tts_interrupted = True
        self._bargein_consecutive_count = 0
        await self.push_interruption_task_frame_and_wait()

    # ------------------------------------------------------------------
    # SpeakingStarted / Stopped  (template-method pattern)
    # ------------------------------------------------------------------

    async def _handle_speaking_started(self, frame, direction):
        """Common handling when VAD detects speech onset.

        Returns True if the frame was handled (caller should return).
        Returns False if not applicable (e.g. wrong frame type).
        """
        if not isinstance(frame, SpeakingStartedFrames):
            return False

        # Ignore VAD frames during PTT
        if self._ptt_active:
            return True

        if not (self._is_hands_free or self._is_dictating):
            return True  # neither mode active — swallow

        # --- Echo gate: suppress false VAD triggers during TTS playback ---
        # During TTS playback, _allow_interruptions is False on the input transport,
        # so Pipecat won't auto-fire InterruptionFrame from VAD. The VAD still pushes
        # SpeakingStartedFrame which reaches us here. We decide:
        #   - Low mic energy (echo) → swallow the frame, no interruption
        #   - High mic energy (real speech) → re-enable interruptions and trigger one
        if self._is_tts_playing() and not self._check_bargein_energy():
            logger.debug("STT: Suppressing VAD speaking-started (TTS playing, low mic energy — echo)")
            # Don't fully discard — the user might actually be speaking but
            # energy hasn't built up yet (AEC convergence, mic ramp-up).
            # Set a flag so we keep checking energy on subsequent audio frames.
            self._pending_bargein_check = True
            self._bargein_consecutive_count = 0
            return True  # swallow the frame for now

        mode = "dictation" if self._is_dictating else "hands-free"
        pre_buf_ms = len(self._pre_buffer) * 20
        logger.debug(f"STT: User started speaking ({mode}), seeding {pre_buf_ms}ms pre-buffer")

        self._user_speaking = True
        self._audio_buffer = list(self._pre_buffer)

        # Hook: let subclass do service-specific work (e.g. stream pre-buffer
        # to a WebSocket, reset a recognizer, etc.)
        await self._on_speaking_started(frame, direction)

        self._pre_buffer.clear()

        # Only interrupt if TTS is actually playing. When TTS isn't playing
        # (e.g. between responses, or LLM is still generating text), there's
        # nothing to interrupt — firing one just kills the LLM response before
        # any audio is produced. The _check_continuous_speech_interruption
        # method handles the case where TTS starts playing while the user is
        # still talking.
        tts_playing = self._is_tts_playing()
        logger.debug(f"STT: TTS playing check: {tts_playing} (ref_buf.is_active={self._aec_ref_buf.is_active if self._aec_ref_buf else 'N/A'})")
        if tts_playing:
            logger.info("STT: Barge-in confirmed — triggering pipeline interruption")
            await self.push_interruption_task_frame_and_wait()

            # Cancel the welcome message task if it's still running — the pipeline
            # interruption sets _cancelled on the LLM (stops new TextFrames), but
            # the welcome task's asyncio.sleep keeps it alive. Cancel it so
            # LLMFullResponseEndFrame is sent immediately via the finally block.
            if self._cancel_welcome_callback:
                self._cancel_welcome_callback()
                logger.info("STT: Cancelled welcome task via callback (barge-in)")

            # Glow signal for hands-free UI
            if self._is_hands_free and self.event_queue:
                try:
                    self.event_queue.put(("stt_hands_free_glow_on", {}), block=False)
                except Exception:
                    pass

            # Notify the rest of the pipeline that the user is speaking.
            # Only push when interrupting — the LLM cancels its generation
            # on UserStartedSpeakingFrame, which we don't want when TTS
            # isn't playing (user is just talking between responses).
            await self.push_frame(frame, direction)
        else:
            logger.debug("STT: User speaking but TTS not playing — accumulating audio (no interrupt needed)")

        return True

    async def _handle_speaking_stopped(self, frame, direction):
        """Common handling when VAD detects speech offset.

        Returns True if the frame was handled.
        """
        if not isinstance(frame, SpeakingStoppedFrames):
            return False

        # User stopped speaking — clear any pending barge-in check
        self._pending_bargein_check = False
        self._bargein_consecutive_count = 0

        if not ((self._is_hands_free or self._is_dictating)
                and self._user_speaking and not self._ptt_active):
            # Not in an active hands-free/dictation speaking session
            await self.push_frame(frame, direction)
            return True

        self._user_speaking = False

        # Glow off
        if self._is_hands_free and not self._ptt_active and self.event_queue:
            try:
                self.event_queue.put(("stt_hands_free_glow_off", {}), block=False)
            except Exception:
                pass

        # Hook: subclass processes the accumulated audio
        await self._on_speaking_stopped(frame, direction)

        await self.push_frame(frame, direction)
        return True

    # ------------------------------------------------------------------
    # Hooks for subclasses (override as needed)
    # ------------------------------------------------------------------

    async def _on_speaking_started(self, frame, direction):
        """Called after pre-buffer is seeded, before it's cleared.
        Override to stream pre-buffer to an API, reset recognizers, etc."""
        pass

    async def _on_speaking_stopped(self, frame, direction):
        """Called after _user_speaking is cleared.
        Override to process accumulated audio buffer, get final results, etc."""
        pass
