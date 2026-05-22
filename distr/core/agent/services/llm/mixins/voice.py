"""
Voice & Dictation Mixin

Handles voice commands (start/stop listening, stop speaking) and dictation
mode (start/stop dictating, typing text via keyboard simulation).

Extracted from LLMSharedMixin to keep shared.py focused on core LLM logic.
"""

import logging
import re
import string
import time
from typing import Any

logger = logging.getLogger(__name__)

# R27: seconds user has to confirm after a draft readout or reminder summary.
_VOICE_CONFIRM_WINDOW_S = 35.0
_GENERIC_ASSISTANT_MESSAGES = {
    "done",
    "ok",
    "okay",
    "sure",
    "finished",
    "complete",
    "completed",
}


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
        if self._is_dictating and getattr(self, '_dictation_one_shot', False):
            return False

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
        """Process dictation text. Returns the text to type."""
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

        if self._is_contextual_typeout_command(text):
            resolved = self._resolve_contextual_typeout_text()
            if resolved:
                logger.info(
                    "Dictation: Resolved contextual type-out command to prior assistant text (%d characters)",
                    len(resolved),
                )
                return resolved
            logger.info(
                "Dictation: Contextual type-out command detected, but no prior assistant text was available"
            )
            return ""

        if self._should_rewrite_dictation_as_ticket():
            try:
                from distr.core.audio.ticket_dictation import rewrite_dictation_as_ticket

                settings = self._load_dictation_settings()
                rewritten = rewrite_dictation_as_ticket(text, settings)
                if rewritten:
                    logger.info(
                        "Dictation: Rewrote transcript as ticket (%d -> %d characters)",
                        len(text),
                        len(rewritten),
                    )
                    return rewritten
            except Exception as e:
                logger.debug("Dictation: Ticket rewrite failed; typing raw transcript: %s", e)

        return text

    def _should_rewrite_dictation_as_ticket(self) -> bool:
        return (
            getattr(self, "_dictation_one_shot", False)
            and getattr(self, "_dictation_output_mode", "plain") == "ticket"
        )

    def _load_dictation_settings(self) -> dict:
        try:
            from distr.core.settings import load_settings_from_db

            return load_settings_from_db() or {}
        except Exception as e:
            logger.debug("Dictation: Could not load settings: %s", e)
            return {}

    def _is_contextual_typeout_command(self, text: str) -> bool:
        """Return True when dictation text asks to type the prior assistant response."""
        normalized = re.sub(r"\s+", " ", text.lower().translate(
            str.maketrans(string.punctuation, " " * len(string.punctuation))
        )).strip()
        if not normalized:
            return False

        return bool(re.fullmatch(
            r"(?:(?:can|could|would)\s+you\s+)?"
            r"(?:(?:please|actually|just|now)\s+)*"
            r"(?:type|write|paste|put)\s+"
            r"(?:that|this|it)"
            r"(?:\s+(?:out|down|in|for\s+me))?"
            r"(?:\s+please)?",
            normalized,
        ))

    def _resolve_contextual_typeout_text(self) -> str:
        """Find the latest useful assistant message from active memory or chat history."""
        for message in reversed(getattr(self, "_messages", []) or []):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = self._dictation_message_content_to_text(message.get("content"))
            if self._is_useful_contextual_typeout_text(content):
                return content

        chat_manager = getattr(self, "chat_manager", None)
        chat_id = chat_manager.get_current_chat() if chat_manager else None
        if chat_manager and chat_id:
            try:
                for message in reversed(chat_manager.get_chat_history(chat_id) or []):
                    if not isinstance(message, dict) or message.get("role") != "assistant":
                        continue
                    content = self._dictation_message_content_to_text(message.get("content"))
                    if self._is_useful_contextual_typeout_text(content):
                        return content
            except Exception as e:
                logger.debug("Dictation: Could not inspect chat history for contextual type-out: %s", e)

        return ""

    def _dictation_message_content_to_text(self, content: Any) -> str:
        """Normalize provider message content into visible text for keyboard typing."""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    value = item.get("text") or item.get("content")
                    if isinstance(value, str):
                        parts.append(value)
            return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
        if isinstance(content, dict):
            value = content.get("text") or content.get("content")
            if isinstance(value, str):
                return value.strip()
        return ""

    def _is_useful_contextual_typeout_text(self, text: str) -> bool:
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        normalized = re.sub(r"\s+", " ", cleaned.lower()).strip(" .!?:;")
        return normalized not in _GENERIC_ASSISTANT_MESSAGES

    async def _type_dictation_text(self, text: str):
        """Type dictation text as keyboard input."""
        if not text or not text.strip():
            return
        try:
            from distr.core.audio.dictation import insert_text
            logger.info("Dictation: Typing text (%d characters): '%s...'", len(text), text[:50])
            newline_mode = (
                "shift_enter"
                if getattr(self, "_dictation_output_mode", "plain") == "ticket"
                else "literal"
            )
            success = insert_text(text, newline_mode=newline_mode)
            if success:
                logger.info("Dictation: Successfully typed text")
            else:
                logger.error("Dictation: Failed to type text")
        except Exception as e:
            logger.error("Dictation: Error typing text: %s", e, exc_info=True)

    def _start_dictation(self, one_shot: bool = False, output_mode: str = "plain"):
        """Start dictation mode.

        Args:
            one_shot: When True (hold-to-dictate hotkey), exit dictation after the next
                transcript is typed — release runs push_to_talk_stop before LLM sees text.
        """
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
        self._dictation_one_shot = bool(one_shot)
        self._dictation_output_mode = "ticket" if output_mode == "ticket" else "plain"
        logger.info(
            "Dictation: Dictation mode started (one_shot=%s, output_mode=%s)",
            self._dictation_one_shot,
            self._dictation_output_mode,
        )

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
        self._dictation_one_shot = False
        self._dictation_output_mode = "plain"
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

        # --- R27 proactive / planner / draft voice ---
        if await self._handle_proactive_voice_commands(text_lower, direction):
            return True

        return False

    def _r27_expire_voice_gates(self) -> None:
        """Clear timed voice gates after their deadline (monotonic clock)."""
        now = time.monotonic()
        gate_id = getattr(self, "_r27_draft_gate_id", None)
        until = getattr(self, "_r27_draft_gate_until", 0.0) or 0.0
        if gate_id is not None and until and now >= until:
            self._r27_draft_gate_id = None
            self._r27_draft_gate_until = 0.0
        rg = getattr(self, "_r27_reminder_gate", None)
        if isinstance(rg, dict) and rg.get("until") and now >= rg["until"]:
            self._r27_reminder_gate = None

    async def _r27_readout_draft_entry(self, target, direction) -> None:
        """TTS body + open timed approve/reject gate for one queued draft."""
        from distr.core.agent.services.llm.text_utils import clean_text_for_tts

        body_raw = (target.draft or "").strip()
        body = (
            clean_text_for_tts(body_raw[:6000], spoken_prose=True) if body_raw else ""
        )
        if len(body) > 2400:
            body = body[:2397] + "…"
        intro = (
            f"Pending draft: {target.description[:180]}. "
            "Here is the draft text."
        )
        await self._speak_text(intro, direction)
        if body:
            await self._speak_text(body, direction)
        self._r27_draft_gate_id = target.id
        self._r27_draft_gate_until = time.monotonic() + _VOICE_CONFIRM_WINDOW_S
        await self._speak_text(
            "Say approve that, reject that, or never mind within about thirty five seconds.",
            direction,
        )
        cid = self.chat_manager.get_current_chat() if getattr(self, "chat_manager", None) else None
        if cid and self.chat_manager:
            try:
                self.chat_manager.add_assistant_message(
                    cid,
                    f"[Voice — pending draft readout]\n\n`{target.id}`\n\n{body_raw[:8000]}",
                )
            except Exception as e:
                logger.debug("Proactive voice: draft readout chat log failed: %s", e)
        logger.info(
            "R27 voice: pending draft readout id=%s gate_until=%s",
            target.id,
            self._r27_draft_gate_until,
        )

    async def _handle_proactive_voice_commands(self, text_lower: str, direction) -> bool:
        """Planner readout, draft readout + timed confirm, reminder + DB confirm (R27)."""
        from distr.core.initiative.planners import tts_excerpt_from_markdown
        from distr.core.initiative.voice_commands import (
            load_latest_planner_markdown,
            match_draft_decision,
            match_draft_decision_for_id,
            match_read_draft_by_id_request,
            match_reminder_request,
            match_schedule_confirm,
            match_voice_wait_cancel,
            resolve_draft_entry_by_voice_id,
            save_voice_reminder_proactive_task,
            wants_agenda_readout,
            wants_pending_draft_readout,
        )
        from distr.core.initiative.draft_execute import approve_draft_in_queue
        from distr.core.initiative.draft_queue import DraftQueue

        self._r27_expire_voice_gates()

        # --- Reminder confirmation window (DB insert) ---
        rg = getattr(self, "_r27_reminder_gate", None)
        if isinstance(rg, dict) and rg.get("until", 0) > time.monotonic():
            if match_voice_wait_cancel(text_lower):
                self._r27_reminder_gate = None
                await self._speak_text("Okay — I did not add that reminder.", direction)
                logger.info("R27 voice: reminder gate cancelled")
                return True
            if match_schedule_confirm(text_lower):
                ok, detail = save_voice_reminder_proactive_task(
                    instruction=rg["instruction"],
                    frequency=rg["frequency"],
                )
                self._r27_reminder_gate = None
                if ok:
                    await self._speak_text(
                        f"Saved your proactive task: {detail}. It runs at nine A M local time.",
                        direction,
                    )
                    cid = self.chat_manager.get_current_chat() if getattr(self, "chat_manager", None) else None
                    if cid and self.chat_manager:
                        try:
                            self.chat_manager.add_assistant_message(
                                cid,
                                f"[Voice — proactive task saved]\n\n**{detail}**\n"
                                f"- Frequency: {rg['frequency']}\n"
                                f"- Instruction: {rg['instruction'][:2000]}",
                            )
                        except Exception as e:
                            logger.debug("Proactive voice: reminder chat log failed: %s", e)
                    logger.info("R27 voice: reminder saved to DB name=%r", detail)
                else:
                    await self._speak_text(
                        f"I could not save that reminder. {detail[:200]}",
                        direction,
                    )
                    logger.warning("R27 voice: reminder DB save failed: %s", detail)
                return True
            # Gate is open but phrase was not a confirmation — avoid FIFO draft approve, etc.
            return False

        # --- Draft readout confirmation window ---
        gate_draft_id = getattr(self, "_r27_draft_gate_id", None)
        gate_until = getattr(self, "_r27_draft_gate_until", 0.0) or 0.0
        if gate_draft_id is not None and gate_until > time.monotonic():
            if match_voice_wait_cancel(text_lower):
                self._r27_draft_gate_id = None
                self._r27_draft_gate_until = 0.0
                await self._speak_text("Okay — I left the draft in your queue unchanged.", direction)
                logger.info("R27 voice: draft gate cancelled")
                return True
            decision = match_draft_decision(text_lower)
            if decision:
                dq = DraftQueue()
                dq.expire_old()
                target_meta = None
                for e in dq.get_all():
                    if e.id == gate_draft_id:
                        target_meta = e
                        break
                self._r27_draft_gate_id = None
                self._r27_draft_gate_until = 0.0
                verb = "approved" if decision == "approve" else "rejected"
                if not target_meta:
                    await self._speak_text(
                        "That draft is no longer in the queue — it may have been removed already.",
                        direction,
                    )
                    logger.info("R27 voice: gated draft %s missing id=%s", decision, gate_draft_id)
                    return True
                if decision == "approve":
                    ok = approve_draft_in_queue(dq, gate_draft_id)
                else:
                    ok = dq.remove(gate_draft_id)
                if ok:
                    snippet = (target_meta.description or "")[:200]
                    await self._speak_text(
                        f"I {verb} the draft: {snippet}" if snippet else f"I {verb} that draft.",
                        direction,
                    )
                    cid = self.chat_manager.get_current_chat() if getattr(self, "chat_manager", None) else None
                    if cid and self.chat_manager:
                        try:
                            self.chat_manager.add_assistant_message(
                                cid,
                                f"[Voice — draft {verb}] `{gate_draft_id}`\n\n{target_meta.draft[:4000]}",
                            )
                        except Exception as e:
                            logger.debug("Proactive voice: draft chat log failed: %s", e)
                else:
                    await self._speak_text(
                        "I could not update that draft — it may have already been removed.",
                        direction,
                    )
                logger.info("R27 voice: gated draft %s id=%s ok=%s", decision, gate_draft_id, ok)
                return True
            return False

        # --- Read a specific draft by id (before generic "read pending draft") ---
        read_token = match_read_draft_by_id_request(text_lower)
        if read_token:
            self._r27_reminder_gate = None
            dq = DraftQueue()
            dq.expire_old()
            entries = dq.get_all()
            entry, rid_status = resolve_draft_entry_by_voice_id(read_token, entries)
            if rid_status == "ambiguous":
                await self._speak_text(
                    "Multiple pending drafts match that short id. "
                    "Use the full U U I D from chat, or say read pending draft for the oldest one.",
                    direction,
                )
                logger.info("R27 voice: read draft by id ambiguous token=%s", read_token)
                return True
            if rid_status == "none" or entry is None:
                await self._speak_text(
                    "I could not find a pending draft with that id.",
                    direction,
                )
                logger.info("R27 voice: read draft by id not found token=%s", read_token)
                return True
            await self._r27_readout_draft_entry(entry, direction)
            return True

        # --- Start pending-draft readout (opens draft gate after TTS) ---
        if wants_pending_draft_readout(text_lower):
            self._r27_reminder_gate = None
            dq = DraftQueue()
            dq.expire_old()
            entries = dq.get_all()
            if not entries:
                await self._speak_text(
                    "There are no pending initiative drafts in your queue.",
                    direction,
                )
                logger.info("R27 voice: pending draft readout requested but queue empty")
                return True
            await self._r27_readout_draft_entry(entries[0], direction)
            return True

        # --- Parsed reminder: open confirmation gate (no DB row until confirm) ---
        remind = match_reminder_request(text_lower)
        if remind:
            self._r27_draft_gate_id = None
            self._r27_draft_gate_until = 0.0
            instr = remind["instruction"]
            freq = remind["frequency"]
            self._r27_reminder_gate = {
                "instruction": instr,
                "frequency": freq,
                "until": time.monotonic() + _VOICE_CONFIRM_WINDOW_S,
            }
            await self._speak_text(
                f"I will add a {freq} proactive task: remind you to {instr}. "
                "Say confirm schedule to save it, or never mind to cancel.",
                direction,
            )
            logger.info("R27 voice: reminder gate opened freq=%s", freq)
            return True

        if wants_agenda_readout(text_lower):
            md = load_latest_planner_markdown()
            if not md:
                msg = "I do not have a saved day, week, or month planner yet. Run your scheduled planners first."
            else:
                excerpt = tts_excerpt_from_markdown(md, max_len=1100)
                msg = f"Here is your latest planner. {excerpt}"
            await self._speak_text(msg, direction)
            cid = self.chat_manager.get_current_chat() if getattr(self, "chat_manager", None) else None
            if cid and self.chat_manager:
                chat_body = (
                    "[Voice — my agenda]\n\n"
                    + (md if md else "_No planner rows in the database yet._")
                )[:12000]
                try:
                    self.chat_manager.add_assistant_message(cid, chat_body)
                except Exception as e:
                    logger.debug("Proactive voice: add_assistant_message failed: %s", e)
            logger.info("R27 voice: agenda readout handled")
            return True

        targeted = match_draft_decision_for_id(text_lower)
        if targeted:
            ddec, raw_tok = targeted
            dq = DraftQueue()
            dq.expire_old()
            entries = dq.get_all()
            entry, tid_status = resolve_draft_entry_by_voice_id(raw_tok, entries)
            if tid_status == "ambiguous":
                await self._speak_text(
                    "Several drafts match that id fragment. Use the full U U I D from chat.",
                    direction,
                )
                logger.info("R27 voice: targeted draft %s ambiguous token=%s", ddec, raw_tok)
                return True
            if tid_status == "none" or entry is None:
                await self._speak_text(
                    "No pending draft matches that id.",
                    direction,
                )
                logger.info("R27 voice: targeted draft %s not found token=%s", ddec, raw_tok)
                return True
            if ddec == "approve":
                ok = approve_draft_in_queue(dq, entry.id)
            else:
                ok = dq.remove(entry.id)
            verb = "approved" if ddec == "approve" else "rejected"
            if ok:
                await self._speak_text(
                    f"I {verb} draft {entry.id[:8]}: {(entry.description or '')[:200]}",
                    direction,
                )
                cid = self.chat_manager.get_current_chat() if getattr(self, "chat_manager", None) else None
                if cid and self.chat_manager:
                    try:
                        self.chat_manager.add_assistant_message(
                            cid,
                            f"[Voice — draft {verb}] `{entry.id}`\n\n{entry.draft[:4000]}",
                        )
                    except Exception as e:
                        logger.debug("Proactive voice: draft chat log failed: %s", e)
            else:
                await self._speak_text(
                    "I could not update that draft — it may have already been removed.",
                    direction,
                )
            logger.info("R27 voice: targeted draft %s id=%s ok=%s", ddec, entry.id, ok)
            return True

        decision = match_draft_decision(text_lower)
        if decision:
            dq = DraftQueue()
            dq.expire_old()
            entries = dq.get_all()
            if not entries:
                await self._speak_text("There are no pending initiative drafts to approve or reject.", direction)
                logger.info("R27 voice: draft %s but queue empty", decision)
                return True
            target = entries[0]
            if decision == "approve":
                ok = approve_draft_in_queue(dq, target.id)
            else:
                ok = dq.remove(target.id)
            verb = "approved" if decision == "approve" else "rejected"
            if ok:
                await self._speak_text(
                    f"I {verb} the pending draft: {target.description[:200]}",
                    direction,
                )
                cid = self.chat_manager.get_current_chat() if getattr(self, "chat_manager", None) else None
                if cid and self.chat_manager:
                    try:
                        self.chat_manager.add_assistant_message(
                            cid,
                            f"[Voice — draft {verb}] `{target.id}`\n\n{target.draft[:4000]}",
                        )
                    except Exception as e:
                        logger.debug("Proactive voice: draft chat log failed: %s", e)
            else:
                await self._speak_text(
                    "I could not update that draft — it may have already been removed.",
                    direction,
                )
            logger.info("R27 voice: draft %s id=%s ok=%s", decision, target.id, ok)
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
