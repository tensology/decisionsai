"""
Voice & Dictation Mixin

Handles voice commands (start/stop listening, stop speaking) and dictation
mode (start/stop dictating, typing text via keyboard simulation).

Extracted from LLMSharedMixin to keep shared.py focused on core LLM logic.
"""

import logging
import re
import string

logger = logging.getLogger(__name__)


class VoiceDictationMixin:
    """Mixin providing voice command and dictation methods for LLM services.

    Expects on self:
    - _is_dictating, _is_hands_free, _is_listening
    - _hands_free_before_dictation
    - set_hands_free(enabled)
    - event_queue
    - push_frame(frame, direction)
    """

    # ------------------------------------------------------------------ #
    #  Dictation                                                          #
    # ------------------------------------------------------------------ #

    def _check_dictation_commands(self, text_lower: str, original_text: str) -> bool:
        """Check for dictation commands. Returns True if a dictation command was processed."""
        if not self._is_dictating:
            words = text_lower.split()
            if "dictate" in text_lower:
                if len(words) <= 3 or text_lower.startswith("dictate"):
                    logger.info("Dictation: 'dictate' command detected - starting dictation mode")
                    self._start_dictation()
                    return True
            elif "start dictating" in text_lower:
                if len(words) <= 3 or text_lower.startswith("start dictating"):
                    logger.info("Dictation: 'start dictating' command detected - starting dictation mode")
                    self._start_dictation()
                    return True

        if self._is_dictating:
            text_no_punct = text_lower.translate(str.maketrans('', '', string.punctuation)).strip()
            if "stop dictating" in text_no_punct or "stopped dictating" in text_no_punct or "enter this" in text_no_punct:
                logger.info("Dictation: Stop command detected - stopping dictation mode")
                self._stop_dictation()
                return True

        return False

    def _process_dictation_text(self, text: str) -> str:
        """Process dictation text - remove stop commands. Returns the text to type."""
        text_lower = text.lower()
        text_no_punct = text_lower.translate(str.maketrans('', '', string.punctuation))

        if "stop dictating" in text_no_punct or "stopped dictating" in text_no_punct:
            pattern = r'\b(?:stop|stopped)\s+dictating\b'
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                text = text[:match.start()].strip()
        elif "enter this" in text_no_punct:
            pattern = r'\benter\s+this\b'
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                text = text[:match.start()].strip()

        return text

    async def _type_dictation_text(self, text: str):
        """Type dictation text as keyboard input."""
        if not text or not text.strip():
            return
        try:
            from distr.core.audio.dictation import type_text
            logger.info("Dictation: Typing text (%d characters): '%s...'", len(text), text[:50])
            success = type_text(text)
            if success:
                logger.info("Dictation: Successfully typed text")
            else:
                logger.error("Dictation: Failed to type text")
        except Exception as e:
            logger.error("Dictation: Error typing text: %s", e, exc_info=True)

    def _start_dictation(self):
        """Start dictation mode."""
        if self._is_dictating:
            return

        self._hands_free_before_dictation = self._is_hands_free

        if self._is_hands_free:
            logger.info("Dictation: Disabling hands-free mode for dictation")
            self.set_hands_free(False)
            if self.event_queue:
                try:
                    self.event_queue.put(('hands_free_mode_changed', {'enabled': False}), block=False)
                except Exception as e:
                    logger.debug("Error emitting hands_free_mode_changed: %s", e)

        self._is_dictating = True
        logger.info("Dictation: Dictation mode started")

        if self.event_queue:
            try:
                self.event_queue.put(('set_dictating', {'enabled': True}), block=False)
            except Exception as e:
                logger.debug("Error emitting set_dictating: %s", e)
            try:
                self.event_queue.put(('dictation_started', {}), block=False)
            except Exception as e:
                logger.debug("Error emitting dictation_started: %s", e)

    def _stop_dictation(self):
        """Stop dictation mode."""
        if not self._is_dictating:
            return

        self._is_dictating = False
        logger.info("Dictation: Dictation mode stopped")

        if self.event_queue:
            try:
                self.event_queue.put(('set_dictating', {'enabled': False}), block=False)
            except Exception as e:
                logger.debug("Error emitting set_dictating: %s", e)

        if self._hands_free_before_dictation and self._is_listening:
            logger.info("Dictation: Restoring hands-free mode after dictation")
            self.set_hands_free(True)
            if self.event_queue:
                try:
                    self.event_queue.put(('hands_free_mode_changed', {'enabled': True}), block=False)
                except Exception as e:
                    logger.debug("Error emitting hands_free_mode_changed: %s", e)

        self._hands_free_before_dictation = False

        if self.event_queue:
            try:
                self.event_queue.put(('dictation_stopped', {}), block=False)
            except Exception as e:
                logger.debug("Error emitting dictation_stopped: %s", e)

    # ------------------------------------------------------------------ #
    #  Voice commands                                                     #
    # ------------------------------------------------------------------ #

    def _check_start_listening_command(self, text: str) -> bool:
        """Check if text is a 'start listening' wake word. Returns True if detected and executed."""
        text_lower = text.lower().strip()
        words = text_lower.split()

        start_patterns = [
            "start listening", "begin listening", "listen now", "enable listening",
            "turn on listening", "start responding", "begin responding",
            "start recording", "wake up", "hey computer", "listen to me",
        ]
        for pattern in start_patterns:
            if pattern in text_lower:
                if len(words) <= 4 or text_lower.startswith(pattern):
                    logger.debug("Wake word detected: start_listening")
                    self._execute_start_listening()
                    return True
        return False

    async def _check_and_execute_voice_command(self, text: str, direction) -> bool:
        """Check if text is a voice command and execute it. Returns True if handled."""
        text_lower = text.lower().strip()
        words = text_lower.split()
        word_count = len(words)

        # --- start listening ---
        start_patterns = [
            "start listening", "begin listening", "listen now", "enable listening",
            "turn on listening", "start responding", "begin responding",
        ]
        for pattern in start_patterns:
            if pattern in text_lower:
                if len(words) <= 4 or text_lower.startswith(pattern):
                    logger.debug("Voice command detected: start_listening")
                    self._execute_start_listening()
                    return True

        # --- stop speaking ---
        if word_count > 6 and "stop" in text_lower:
            if not text_lower.startswith(("stop speaking", "stop talking", "stop responding", "shut up")):
                logger.debug("Voice command: Skipping 'stop' in long sentence (%d words): '%s'", word_count, text)
                return False

        stop_speaking_patterns = [
            "stop speaking", "stop talking", "stop responding",
            "shut up", "be quiet", "quiet", "enough",
        ]
        for pattern in stop_speaking_patterns:
            if pattern in text_lower:
                if word_count <= 3 or text_lower.startswith(pattern):
                    logger.debug("Voice command detected: stop_speaking")
                    await self._execute_stop_speaking(direction)
                    return True

        if text_lower.strip() == "stop" or (word_count == 1 and words[0] == "stop"):
            logger.debug("Voice command detected: stop_speaking (standalone 'stop')")
            await self._execute_stop_speaking(direction)
            return True

        # --- stop listening ---
        stop_listening_patterns = [
            "stop listening", "don't listen", "disable listening",
            "turn off listening",
        ]
        for pattern in stop_listening_patterns:
            if pattern in text_lower:
                if len(words) <= 4 or text_lower.startswith(pattern):
                    logger.debug("Voice command detected: stop_listening")
                    self._execute_stop_listening()
                    return True

        return False

    def _execute_start_listening(self):
        """Execute start_listening voice command."""
        if not self._is_listening:
            self._is_listening = True
            logger.debug("Voice command: Listening enabled")
            if self.event_queue:
                try:
                    self.event_queue.put(('voice_set_is_listening', {'enabled': True}), block=False)
                except Exception as e:
                    logger.debug("Could not emit voice_set_is_listening: %s", e)

    async def _execute_stop_speaking(self, direction):
        """Execute stop_speaking — interrupts TTS but keeps listening."""
        logger.debug("Voice command: Stop speaking (interrupting TTS, keeping listening enabled)")
        try:
            from distr.core.agent.libs import InterruptionFrame
            interruption_frame = InterruptionFrame()
            await self.push_frame(interruption_frame, direction)
            logger.debug("Voice command: InterruptionFrame sent to interrupt TTS")
        except Exception as e:
            logger.error("Error sending InterruptionFrame for stop_speaking: %s", e)

    def _execute_stop_listening(self):
        """Execute stop_listening — disables listening entirely."""
        if self._is_listening:
            self._is_listening = False
            logger.debug("Voice command: Listening disabled")
            if self.event_queue:
                try:
                    self.event_queue.put(('voice_set_is_listening', {'enabled': False}), block=False)
                except Exception as e:
                    logger.debug("Could not emit voice_set_is_listening: %s", e)

    async def _speak_text(self, text: str, direction):
        """Push text through the TTS pipeline."""
        from distr.core.agent.libs import LLMFullResponseStartFrame, LLMFullResponseEndFrame, TextFrame
        await self.push_frame(LLMFullResponseStartFrame(), direction)
        await self.push_frame(TextFrame(text=text), direction)
        await self.push_frame(LLMFullResponseEndFrame(), direction)
