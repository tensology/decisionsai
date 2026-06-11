"""
Headless action recorder host. Handles start/stop recording signals (voice, agent, web)
without the ActionWindow GUI. Uses ActionRecorderProcess and updates DB; emits
action_recording_started/action_recording_stopped for tray icon and set_action_name for naming.
"""

import json
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

import re
import string

from PyQt6.QtCore import QObject, QTimer

from distr.core.db import get_session, Action
from distr.core.signals import signal_manager, speak_text_directly_event_queue
from distr.core.paths import RECORDINGS_DIR

logger = logging.getLogger(__name__)

# Whisper/STT artifacts and filler words that should never be accepted as action names
_STT_ARTIFACTS = {a.lower() for a in [
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
    "um", "uh", "er", "ah", "hmm", "hmmm", "mm", "mmhmm", "huh",
    "like", "you know", "well", "so", "actually", "basically",
]}
# Pattern to detect bracketed/parenthesized annotations like [anything] or (anything)
_ANNOTATION_RE = re.compile(r'^\s*[\[\(].*[\]\)]\s*$')


def _is_stt_artifact(text: str) -> bool:
    """Return True if text is a whisper/STT artifact or filler that should be rejected."""
    if not text:
        return True
    stripped = text.strip().lower()
    if not stripped:
        return True
    # Remove punctuation for comparison
    no_punct = stripped.translate(str.maketrans("", "", string.punctuation)).strip()
    if stripped in _STT_ARTIFACTS or no_punct in _STT_ARTIFACTS:
        return True
    # Reject any [bracketed] or (parenthesized) annotation
    if _ANNOTATION_RE.match(stripped):
        return True
    return False


def _word_to_number(w):
    """Convert word to number for 1-20."""
    mapping = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
        'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
        'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
        'nineteen': 19, 'twenty': 20
    }
    return mapping.get(w.lower()) if w else None


def _is_trigger_word_taken(trigger_word: str, exclude_action_id: int = None) -> bool:
    """Check if trigger word is already used by another action."""
    try:
        trigger_lower = (trigger_word or "").strip().lower()
        if not trigger_lower:
            return False
        with get_session() as session:
            for action in session.query(Action).all():
                if exclude_action_id and action.id == exclude_action_id:
                    continue
                if action.title and action.title.lower() == trigger_lower:
                    return True
                if action.additional_trigger_words:
                    try:
                        words = json.loads(action.additional_trigger_words)
                        if isinstance(words, list):
                            for word in words:
                                if word and str(word).lower() == trigger_lower:
                                    return True
                    except (json.JSONDecodeError, TypeError):
                        pass
        return False
    except Exception as e:
        logger.error(f"Error checking trigger word: {e}", exc_info=True)
        return False


class ActionRecorderHost(QObject):
    """
    Headless host for action recording. Connects to start/stop signals and
    runs ActionRecorderProcess; updates DB and emits tray/name signals.
    Also handles step recording via shared infrastructure.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recorder_process = None
        self.current_action_id = None
        self.current_recording_filename = None
        self._stopping_recording = False
        self.pending_action_id_for_recording = None
        self.waiting_for_action_name_id = None
        self._countdown_overlay = None
        self._recording_for_step_id = None  # When recording for a step instead of an action
        self._key_listener = None  # Escape/Space listener during recording
        self._pause_overlay = None  # Pause overlay widget
        self._is_paused = False

        signal_manager.start_action_recording.connect(self._on_start_recording)
        signal_manager.start_action_recording_with_id.connect(self._on_start_recording_with_id)
        signal_manager.stop_action_recording.connect(self._on_stop_recording)
        signal_manager.set_action_name.connect(self._on_set_action_name)
        signal_manager.start_step_recording.connect(self._on_start_step_recording)
        signal_manager.stop_step_recording.connect(self._on_stop_step_recording)

    def _on_start_recording(self):
        """Handle start recording.

        Reuse an action only when an explicit action_id was provided
        (via start_action_recording_with_id from the UI). Plain
        "start recording" should always create a brand new action row.
        """
        action_id = self.pending_action_id_for_recording
        self.pending_action_id_for_recording = None
        if action_id:
            try:
                with get_session() as session:
                    action = session.query(Action).get(action_id)
                    if action:
                        self.current_action_id = action_id
                        self._start_recording_for_action(action_id, action.title)
                        return
            except Exception as e:
                logger.error(f"Error loading action {action_id}: {e}", exc_info=True)
        self._start_recording_silently()

    def _on_start_recording_with_id(self, action_id: int):
        """Handle start recording with specific action_id."""
        self.pending_action_id_for_recording = action_id
        self._on_start_recording()

    def _on_stop_recording(self):
        """Handle stop recording signal."""
        self._stop_recording()

    def _start_recording_silently(self):
        """Create a new action with auto-incremented name and start recording."""
        if self.recorder_process and self.recorder_process.is_alive():
            logger.warning("Cannot start recording: already recording")
            speak_text_directly_event_queue("Already recording. Stop the current recording first.")
            return
        try:
            from distr.core.agent.tools.actions.create_action import get_next_title, generate_trigger_words
            title = None
            with get_session() as session:
                recent = session.query(Action).order_by(Action.id.desc()).limit(50).all()
                for action in recent:
                    next_title = get_next_title(action.title)
                    if next_title:
                        title = next_title
                        break
                if not title:
                    title = "one"
            with get_session() as session:
                trigger_words_json = generate_trigger_words(title)
                new_action = Action(
                    title=title,
                    description="",
                    additional_trigger_words=trigger_words_json,
                    is_instruction=False,
                    instruction_text=None,
                    action="{}",
                    recording_filename=None,
                    created_date=datetime.utcnow(),
                    modified_date=datetime.utcnow()
                )
                session.add(new_action)
                session.commit()
                action_id = new_action.id
            self.current_action_id = action_id
            self._start_recording_for_action(action_id, title)
        except Exception as e:
            logger.error(f"Error starting recording silently: {e}", exc_info=True)
            speak_text_directly_event_queue(f"Failed to start recording: {str(e)}")

    def _start_recording_for_action(self, action_id: int, action_title: str):
        """Start recording for a specific action — shows countdown first."""
        if self.recorder_process and self.recorder_process.is_alive():
            logger.warning("Already recording, ignoring start")
            return
        # Clear any stale "waiting for name" state from a previous recording
        self.waiting_for_action_name_id = None
        self._recording_for_step_id = None

        def _after_countdown():
            self._do_start_recording(action_id, action_title)

        self._show_countdown(_after_countdown)

    def _do_start_recording(self, action_id: int, action_title: str):
        """Actually start the recorder process (called after countdown)."""
        try:
            from distr.core.actions.recorder_process import ActionRecorderProcess
            logger.info(f"[RECORDER_HOST] Starting recording for action_id={action_id}, title='{action_title}'")
            self.recorder_process = ActionRecorderProcess(
                action_id=action_id,
                action_title=action_title,
                recordings_dir=RECORDINGS_DIR
            )
            success, result = self.recorder_process.start()
            if success:
                self.current_recording_filename = result
                self.current_action_id = action_id
                logger.info(f"[RECORDER_HOST] Recording started, filename={result}")
                signal_manager.action_recording_started.emit(action_id)
                speak_text_directly_event_queue("Recording started.")
                self._start_recording_key_listener()
            else:
                logger.error(f"[RECORDER_HOST] Failed to start recording: {result}")
                speak_text_directly_event_queue(f"Failed to start recording: {result}")
        except Exception as e:
            logger.error(f"Error starting recording: {e}", exc_info=True)
            speak_text_directly_event_queue(f"Failed to start recording: {str(e)}")

    def _show_countdown(self, on_complete):
        """Show the 3-2-1-GO countdown overlay, then call on_complete."""
        try:
            from distr.gui.countdown_overlay import CountdownOverlay
            self._countdown_overlay = CountdownOverlay(on_complete=on_complete)
            self._countdown_overlay.start()
        except Exception as e:
            logger.error(f"Countdown overlay failed, starting immediately: {e}", exc_info=True)
            on_complete()

    # ── Step recording (shared infrastructure) ──

    def _on_start_step_recording(self, step_id: int):
        """Start recording for a workflow step — uses same recorder, saves to step."""
        if self.recorder_process and self.recorder_process.is_alive():
            logger.warning("Cannot start step recording: already recording")
            speak_text_directly_event_queue("Already recording. Stop the current recording first.")
            return
        self._recording_for_step_id = step_id
        self.waiting_for_action_name_id = None

        def _after_countdown():
            self._do_start_step_recording(step_id)

        self._show_countdown(_after_countdown)

    def _do_start_step_recording(self, step_id: int):
        """Actually start recording for a step."""
        try:
            from distr.core.actions.recorder_process import ActionRecorderProcess
            logger.info(f"[RECORDER_HOST] Starting recording for step_id={step_id}")
            self.recorder_process = ActionRecorderProcess(
                action_id=step_id,
                action_title=f"step-{step_id}",
                recordings_dir=RECORDINGS_DIR
            )
            success, result = self.recorder_process.start()
            if success:
                self.current_recording_filename = result
                logger.info(f"[RECORDER_HOST] Step recording started, filename={result}")
                signal_manager.step_recording_started.emit(step_id)
                self._start_recording_key_listener()
            else:
                logger.error(f"[RECORDER_HOST] Failed to start step recording: {result}")
                speak_text_directly_event_queue(f"Failed to start recording: {result}")
                self.recorder_process = None
                self._recording_for_step_id = None
        except Exception as e:
            logger.error(f"Error starting step recording: {e}", exc_info=True)
            speak_text_directly_event_queue(f"Failed to start recording: {str(e)}")
            self.recorder_process = None
            self._recording_for_step_id = None

    def _on_stop_step_recording(self):
        """Stop recording for a step, create a linked Action entity, and save to step DB."""
        self._stop_recording_key_listener()
        step_id = self._recording_for_step_id
        if not step_id:
            logger.warning("No step recording in progress")
            speak_text_directly_event_queue("No recording in progress for this step.")
            return
        if not self.recorder_process:
            logger.warning("Cannot stop step recording: no recorder process")
            speak_text_directly_event_queue("Recording was not active. Try recording again.")
            self._recording_for_step_id = None
            return
        try:
            saved_filename, save_error = self.recorder_process.stop()
            if save_error:
                logger.error(f"Step recorder save error: {save_error}")
            recording_filename = saved_filename or self.current_recording_filename or f"step-{step_id}.json"

            # Build action title from workflow name + step name
            action_title = f"Step {step_id}"
            try:
                from distr.core.db.workflow import AutoWorkflowStep, AutoWorkflow
                with get_session() as session:
                    step = session.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
                    if step:
                        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == step.workflow_id).first()
                        wf_name = wf.name if wf else "Workflow"
                        step_name = step.name or f"Step {step.position}"
                        action_title = f"{wf_name} - {step_name}"
            except Exception as e:
                logger.warning(f"Could not resolve workflow/step name: {e}")

            # Create a real Action entity linked to this step
            from distr.core.agent.tools.actions.create_action import generate_trigger_words
            from distr.core.workflow.service import update_step
            with get_session() as session:
                trigger_words_json = generate_trigger_words(action_title)
                new_action = Action(
                    title=action_title,
                    description=f"Recorded from workflow step {step_id}",
                    additional_trigger_words=trigger_words_json,
                    is_instruction=False,
                    instruction_text=None,
                    action="{}",
                    recording_filename=recording_filename,
                    created_date=datetime.utcnow(),
                    modified_date=datetime.utcnow(),
                )
                session.add(new_action)
                session.commit()
                new_action_id = new_action.id

            # Link the action to the step and save the recording filename
            update_step(step_id, recording_filename=recording_filename, action_id=new_action_id)
            logger.info(f"[RECORDER_HOST] Step recording saved: {recording_filename} for step {step_id}, action_id={new_action_id}")
            signal_manager.step_recording_stopped.emit(step_id)
            speak_text_directly_event_queue("Step recording saved.")
        except Exception as e:
            logger.error(f"Error stopping step recording: {e}", exc_info=True)
            speak_text_directly_event_queue(f"Error saving recording: {str(e)}")
        finally:
            self.recorder_process = None
            self.current_recording_filename = None
            self._recording_for_step_id = None

    def _stop_recording(self):
        """Stop recording and save to DB (no UI)."""
        self._stop_recording_key_listener()
        if self._stopping_recording:
            logger.debug("Stop recording already in progress, ignoring duplicate call")
            return
        if not self.recorder_process:
            logger.warning("Cannot stop recording: no recorder process")
            return
        self._stopping_recording = True
        recording_filename = self.current_recording_filename
        action_id = self.current_action_id
        try:
            from distr.core.actions.recorder import slugify
            if not recording_filename and action_id:
                with get_session() as session:
                    action = session.query(Action).get(action_id)
                    if action and action.title:
                        recording_filename = f"{slugify(action.title)}-{action_id}.json"
                    else:
                        recording_filename = f"action-{action_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
            if not recording_filename:
                recording_filename = f"recording-{action_id}.json"
            saved_filename, save_error = self.recorder_process.stop()
            if saved_filename:
                recording_filename = saved_filename
            if save_error:
                logger.error(f"Recorder save error: {save_error}")
            recording_path = Path(RECORDINGS_DIR) / recording_filename
            if not recording_path.exists():
                try:
                    if Path(RECORDINGS_DIR).exists():
                        files = list(Path(RECORDINGS_DIR).glob("*.json"))
                        action_id_str = str(action_id)
                        matching = [f for f in files if action_id_str in f.name]
                        if matching:
                            recording_path = max(matching, key=lambda p: p.stat().st_mtime)
                            recording_filename = recording_path.name
                except Exception as e:
                    logger.error(f"Error resolving recording file: {e}")
            if action_id and recording_filename and str(action_id) in recording_filename:
                with get_session() as session:
                    action = session.query(Action).get(action_id)
                    if action:
                        action.recording_filename = recording_filename
                        action.modified_date = datetime.utcnow()
                        session.commit()
                        logger.info(f"Saved recording '{recording_filename}' to action {action_id}")
            signal_manager.action_recording_stopped.emit(action_id)
            try:
                with get_session() as session:
                    action = session.query(Action).get(action_id)
                    if action:
                        title = (action.title or "").strip()
                        # Always prompt for confirmation/name on stop recording so the
                        # user can keep or update the action name immediately.
                        self.waiting_for_action_name_id = action_id
                        signal_manager.waiting_for_action_name.emit(action_id)
                        if title:
                            speak_text_directly_event_queue(
                                f"Recording saved. Current name is {title}. Confirm or provide a new name."
                            )
                        else:
                            speak_text_directly_event_queue(
                                "Recording saved. What would you like to name this action?"
                            )
            except Exception as e:
                logger.error(f"Error after stop recording: {e}", exc_info=True)
                self.waiting_for_action_name_id = None
        finally:
            self.recorder_process = None
            self.current_recording_filename = None
            self._stopping_recording = False
            logger.info("Stopped recording")

    def _start_recording_key_listener(self):
        """Start listening for Escape (stop) and Space (pause/resume) during recording."""
        self._stop_recording_key_listener()
        self._is_paused = False
        try:
            if sys.platform == 'darwin':
                self._start_macos_recording_key_listener()
            else:
                self._start_pynput_recording_key_listener()
        except Exception as e:
            logger.error(f"[RECORDER_HOST] Error starting key listener: {e}", exc_info=True)

    def _start_pynput_recording_key_listener(self):
        """pynput-based key listener for non-macOS."""
        try:
            from pynput import keyboard
            self._ctrl_held = False

            def on_press(key):
                try:
                    if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                        self._ctrl_held = True
                    elif key == keyboard.Key.esc:
                        logger.info("[RECORDER_HOST] Escape pressed during recording")
                        QTimer.singleShot(0, self._escape_stop_recording)
                        return False
                    elif key == keyboard.Key.space and self._ctrl_held:
                        logger.info("[RECORDER_HOST] Ctrl+Space pressed during recording")
                        QTimer.singleShot(0, self._toggle_pause_recording)
                except Exception as e:
                    logger.error(f"[RECORDER_HOST] Key listener error: {e}")

            def on_release(key):
                if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                    self._ctrl_held = False

            self._key_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._key_listener.start()
            logger.info("[RECORDER_HOST] Started Escape/Ctrl+Space key listener (pynput)")
        except Exception as e:
            logger.error(f"[RECORDER_HOST] pynput key listener failed: {e}", exc_info=True)

    def _start_macos_recording_key_listener(self):
        """macOS NSEvent-based key listener — global + local so Escape works when focused."""
        try:
            from distr.core.actions.key_monitor import MacEscapeMonitor

            self._key_listener = MacEscapeMonitor(
                on_escape=lambda: QTimer.singleShot(0, self._escape_stop_recording),
                on_ctrl_space=lambda: QTimer.singleShot(0, self._toggle_pause_recording),
            )
            if self._key_listener.start():
                logger.info("[RECORDER_HOST] Started Escape/Ctrl+Space key listener (macOS)")
            else:
                self._key_listener = None
        except Exception as e:
            logger.error(f"[RECORDER_HOST] macOS key listener failed: {e}", exc_info=True)
            self._key_listener = None

    def _stop_recording_key_listener(self):
        """Stop the Escape/Space key listener."""
        try:
            if self._key_listener:
                if sys.platform == 'darwin':
                    try:
                        self._key_listener.stop()
                    except Exception as e:
                        logger.warning(f"[RECORDER_HOST] Error removing macOS monitor: {e}")
                else:
                    try:
                        self._key_listener.stop()
                    except Exception:
                        pass
                self._key_listener = None
        except Exception as e:
            logger.error(f"[RECORDER_HOST] Error stopping key listener: {e}", exc_info=True)
        # Clean up pause overlay
        if self._pause_overlay:
            try:
                self._pause_overlay.cleanup()
            except Exception:
                pass
            self._pause_overlay = None
        self._is_paused = False

    def _escape_stop_recording(self):
        """Handle Escape press during recording — stop and route to correct handler."""
        if self._recording_for_step_id:
            self._on_stop_step_recording()
        elif self.recorder_process:
            self._stop_recording()

    def _toggle_pause_recording(self):
        """Handle Space press during recording — toggle pause/resume."""
        if not self.recorder_process or not self.recorder_process.is_alive():
            return
        paused = self.recorder_process.pause()
        if paused is None:
            return
        self._is_paused = paused
        if paused:
            logger.info("[RECORDER_HOST] Recording paused")
            speak_text_directly_event_queue("Paused")
            # Show pause overlay
            try:
                from distr.gui.countdown_overlay import PauseOverlay
                if not self._pause_overlay:
                    self._pause_overlay = PauseOverlay()
                self._pause_overlay.show_on_cursor_screen()
            except Exception as e:
                logger.error(f"[RECORDER_HOST] Error showing pause overlay: {e}")
        else:
            logger.info("[RECORDER_HOST] Recording resumed")
            speak_text_directly_event_queue("Resumed")
            if self._pause_overlay:
                self._pause_overlay.dismiss()

    def cancel_recorded_action(self, action_id: int) -> bool:
        """Delete a freshly recorded action and discard its recording file."""
        recording_filename = None
        try:
            with get_session() as session:
                action = session.query(Action).get(action_id)
                if not action:
                    self.waiting_for_action_name_id = None
                    return False
                recording_filename = (action.recording_filename or "").strip() or None
                session.delete(action)
                session.commit()

            if recording_filename:
                recording_path = Path(RECORDINGS_DIR) / recording_filename
                if recording_path.exists():
                    recording_path.unlink()
            else:
                action_id_str = str(action_id)
                try:
                    for candidate in Path(RECORDINGS_DIR).glob("*.json"):
                        if action_id_str in candidate.name:
                            candidate.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning(
                        "Failed to clean up recording files for cancelled action %s: %s",
                        action_id,
                        exc,
                    )

            self.waiting_for_action_name_id = None
            signal_manager.action_recording_cancelled.emit(action_id)
            try:
                from distr.gui.web.workflow_events import increment_workflow_updated

                increment_workflow_updated()
            except Exception:
                pass
            speak_text_directly_event_queue("Action cancelled.")
            logger.info("Cancelled recorded action %s", action_id)
            return True
        except Exception as exc:
            logger.error("Failed to cancel recorded action %s: %s", action_id, exc, exc_info=True)
            self.waiting_for_action_name_id = None
            return False

    def _on_set_action_name(self, action_id: int, name: str):
        """Update action title and trigger words from voice/web."""
        name_stripped = (name or "").strip()
        if not name_stripped:
            logger.warning("Empty name in set_action_name")
            speak_text_directly_event_queue("Please provide a name for the action.")
            return
        # Reject STT artifacts like [BLANK_AUDIO], fillers, etc.
        if _is_stt_artifact(name_stripped):
            logger.debug(f"Ignoring STT artifact as action name: '{name_stripped}'")
            return
        if _is_trigger_word_taken(name_stripped, exclude_action_id=action_id):
            speak_text_directly_event_queue(f"The trigger word '{name_stripped}' is already used. Please choose a different name.")
            return
        try:
            with get_session() as session:
                action = session.query(Action).get(action_id)
                if not action:
                    return
                action.title = name_stripped
                existing = []
                if action.additional_trigger_words:
                    try:
                        existing = json.loads(action.additional_trigger_words)
                        if not isinstance(existing, list):
                            existing = []
                    except (json.JSONDecodeError, TypeError):
                        pass
                if name_stripped not in existing:
                    existing.append(name_stripped)
                action.additional_trigger_words = json.dumps(existing)
                action.modified_date = datetime.utcnow()
                session.commit()
            self.waiting_for_action_name_id = None
            speak_text_directly_event_queue(f"Recording saved. You can run this action by saying 'run action {name_stripped}'")
        except Exception as e:
            logger.error(f"Error setting action name: {e}", exc_info=True)
