"""
Action Playback Service

Handles action playback independently of the UI.
This service is always available and can trigger playback via signals.
"""

import logging
import json
import threading
import time
from distr.core.db import get_session, Action
from distr.core.paths import RECORDINGS_DIR
from .player_process import ActionPlayerProcess
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from pynput import keyboard

logger = logging.getLogger(__name__)


class ActionPlaybackService(QObject):
    """Service for playing actions independently of UI"""
    
    playback_finished = pyqtSignal()  # Emitted when playback completes
    playback_failed = pyqtSignal(str)  # Emitted when playback fails (with error message)
    
    def __init__(self):
        super().__init__()
        self.current_playback_process = None
        self.current_action_id = None
        self.current_action_title = None
        self.escape_listener = None
        self.escape_lock = threading.Lock()
        self._pause_overlay = None
        self._is_paused = False

    def play_recording_file(self, recording_filename: str):
        """Play a recording directly by filename (from RECORDINGS_DIR)."""
        try:
            recording_path = Path(RECORDINGS_DIR) / recording_filename
            recording_path = recording_path.resolve()
            if not recording_path.exists():
                error_msg = f"Recording file not found: {recording_filename}"
                logger.error(f"[ACTION PLAYBACK SERVICE] {error_msg}")
                self.playback_failed.emit(error_msg)
                return
            logger.info(f"[ACTION PLAYBACK SERVICE] Playing recording file: {recording_filename}")
            self._start_playback(str(recording_path), False, recording_filename)
        except Exception as e:
            logger.error(f"[ACTION PLAYBACK SERVICE] Error playing recording file: {e}", exc_info=True)
            self.playback_failed.emit(f"Error playing recording: {str(e)}")

    def play_action_by_name(self, action_name: str):
        """Play an action by name or trigger word"""
        try:
            logger.info(f"[ACTION PLAYBACK SERVICE] Received play action request for: '{action_name}'")
            
            # Find action by name or trigger word
            with get_session() as session:
                action = None
                action_name_lower = action_name.lower().strip()
                
                # First, try exact title match
                action = session.query(Action).filter(Action.title.ilike(action_name)).first()
                if action:
                    logger.info(f"[ACTION PLAYBACK SERVICE] Found action by exact title match: '{action.title}'")
                
                # If no exact match, try partial title match
                if not action:
                    action = session.query(Action).filter(Action.title.ilike(f"%{action_name}%")).first()
                    if action:
                        logger.info(f"[ACTION PLAYBACK SERVICE] Found action by partial title match: '{action.title}'")
                
                # If still no match, search in trigger words
                if not action:
                    logger.info(f"[ACTION PLAYBACK SERVICE] Searching trigger words for '{action_name}'...")
                    all_actions = session.query(Action).all()
                    for a in all_actions:
                        # Check title (which is also a trigger word)
                        if a.title and action_name_lower == a.title.lower():
                            action = a
                            logger.info(f"[ACTION PLAYBACK SERVICE] Found action by title trigger: '{action.title}'")
                            break
                        
                        # Check additional trigger words
                        if a.additional_trigger_words:
                            try:
                                trigger_words = json.loads(a.additional_trigger_words)
                                if isinstance(trigger_words, list):
                                    for trigger in trigger_words:
                                        if trigger and action_name_lower == str(trigger).lower():
                                            action = a
                                            logger.info(f"[ACTION PLAYBACK SERVICE] Found action by trigger word '{trigger}': '{action.title}'")
                                            break
                                    if action:
                                        break
                            except (json.JSONDecodeError, TypeError) as e:
                                logger.warning(f"[ACTION PLAYBACK SERVICE] Error parsing trigger words for action {a.id}: {e}")
                                continue
                
                if not action:
                    logger.warning(f"[ACTION PLAYBACK SERVICE] Action '{action_name}' not found by title or trigger words")
                    error_msg = f"Action '{action_name}' not found"
                    self.playback_failed.emit(error_msg)
                    # Speak error via TTS
                    from distr.core.signals import signal_manager
                    signal_manager.speak_text_directly.emit(error_msg)
                    return
                
                # Check if this is an instruction action (should not be handled here)
                if action.is_instruction:
                    logger.warning(f"[ACTION PLAYBACK SERVICE] Action '{action.title}' is an instruction action, not a recorded action")
                    error_msg = f"Action '{action.title}' is an instruction action, not a recorded action"
                    self.playback_failed.emit(error_msg)
                    # Speak error via TTS
                    from distr.core.signals import signal_manager
                    signal_manager.speak_text_directly.emit(error_msg)
                    return
                
                if not action.recording_filename:
                    logger.warning(f"[ACTION PLAYBACK SERVICE] Action '{action.title}' has no recording file")
                    error_msg = f"Action '{action.title}' has no recording file"
                    self.playback_failed.emit(error_msg)
                    # Speak error via TTS
                    from distr.core.signals import signal_manager
                    signal_manager.speak_text_directly.emit(error_msg)
                    return
                
                # Get the recording file path
                recording_path = Path(RECORDINGS_DIR) / action.recording_filename
                recording_path = recording_path.resolve()
                
                if not recording_path.exists():
                    logger.error(f"[ACTION PLAYBACK SERVICE] Recording file not found: {recording_path}")
                    error_msg = f"Recording file not found for action '{action.title}'"
                    self.playback_failed.emit(error_msg)
                    # Speak error via TTS
                    from distr.core.signals import signal_manager
                    signal_manager.speak_text_directly.emit(error_msg)
                    return
                
                # Start playback process
                logger.info(f"[ACTION PLAYBACK SERVICE] Starting playback for action '{action.title}' (ID: {action.id})")
                self.current_action_id = action.id
                self.current_action_title = action.title
                # Note: play_sticky is deprecated, always use False
                self._start_playback(str(recording_path), False, action.title)
                
        except Exception as e:
            logger.error(f"[ACTION PLAYBACK SERVICE] Error playing action: {e}", exc_info=True)
            error_msg = f"Error playing action: {str(e)}"
            self.playback_failed.emit(error_msg)
            # Speak error via TTS
            from distr.core.signals import signal_manager
            signal_manager.speak_text_directly.emit(error_msg)
    
    def _start_playback(self, file_path: str, play_sticky: bool, action_title: str):
        """Start the playback process"""
        try:
            logger.info(f"[ACTION PLAYBACK SERVICE] Creating ActionPlayerProcess for file: {file_path}, play_sticky={play_sticky}")
            
            # Clean up any existing playback
            if self.current_playback_process:
                try:
                    self.current_playback_process.stop()
                except Exception:
                    pass
                self.current_playback_process = None

            # Create and start playback process
            self.current_playback_process = ActionPlayerProcess(
                file_path=file_path,
                play_sticky=play_sticky
            )
            
            logger.info(f"[ACTION PLAYBACK SERVICE] Starting playback process...")
            success, error = self.current_playback_process.start()
            
            if not success:
                logger.error(f"[ACTION PLAYBACK SERVICE] Failed to start playback process: {error}")
                error_msg = error or "Failed to start playback"
                self.playback_failed.emit(error_msg)
                # Speak error via TTS
                from distr.core.signals import signal_manager
                signal_manager.speak_text_directly.emit(error_msg)
                self.current_playback_process = None
                return
            
            logger.info(f"[ACTION PLAYBACK SERVICE] Playback process started successfully, monitoring status...")
            
            # Speak "Running action X" AFTER playback has started
            from distr.core.signals import signal_manager
            signal_manager.speak_text_directly.emit(f"Running action {action_title}")
            logger.info(f"[ACTION PLAYBACK SERVICE] Spoke 'Running action {action_title}' via TTS after playback started")
            
            # Start Escape key listener to stop playback
            self._start_escape_listener()
            
            # Monitor playback completion in background
            def check_playback_status():
                if not self.current_playback_process:
                    return
                
                if not self.current_playback_process.is_alive():
                    # Process finished, check result
                    success, error = self.current_playback_process.wait_for_completion(timeout=0.1)
                    if success:
                        logger.info(f"[ACTION PLAYBACK SERVICE] Playback completed successfully")
                        self._on_playback_finished()
                    else:
                        logger.error(f"[ACTION PLAYBACK SERVICE] Playback failed: {error}")
                        self._on_playback_failed(error or "Playback failed")
                    # Clean up
                    self.current_playback_process = None
                else:
                    # Still playing, check again in 500ms
                    QTimer.singleShot(500, check_playback_status)
            
            # Start monitoring
            QTimer.singleShot(500, check_playback_status)
            
        except Exception as e:
            logger.error(f"[ACTION PLAYBACK SERVICE] Error starting playback: {e}", exc_info=True)
            self.playback_failed.emit(f"Error starting playback: {str(e)}")
    
    def _on_playback_finished(self):
        """Handle successful playback completion"""
        # Stop escape listener
        self._stop_escape_listener()
        
        # Clean up playback process
        if self.current_playback_process:
            try:
                self.current_playback_process.stop()
            except Exception:
                pass
            self.current_playback_process = None

        # Emit signal for UI updates (if window exists) and TTS
        self.playback_finished.emit()
        
        # Speak "Done" via TTS
        from distr.core.signals import signal_manager
        signal_manager.speak_text_directly.emit("Done")
        logger.info("[ACTION PLAYBACK SERVICE] Playback completed - spoke 'Done' via TTS")
    
    def _on_playback_failed(self, error_message: str):
        """Handle failed playback"""
        # Stop escape listener
        self._stop_escape_listener()
        
        # Clean up playback process
        if self.current_playback_process:
            try:
                self.current_playback_process.stop()
            except Exception:
                pass
            self.current_playback_process = None

        # Emit signal for UI updates (if window exists)
        self.playback_failed.emit(error_message)
        
        # Speak error via TTS
        from distr.core.signals import signal_manager
        error_text = f"Action playback failed: {error_message}"
        if len(error_text) > 100:
            error_text = "Action playback failed"
        signal_manager.speak_text_directly.emit(error_text)
        logger.warning(f"[ACTION PLAYBACK SERVICE] Playback failed: {error_message} - spoke error via TTS")
    
    def _start_escape_listener(self):
        """Start listening for Escape (stop) and Ctrl+Space (pause/resume) during playback.
        
        Uses macOS NSEvent global monitor on darwin, pynput on other platforms.
        """
        try:
            import sys
            if sys.platform == 'darwin':
                self._start_macos_escape_listener()
                return
            
            # Use pynput on other platforms
            self._ctrl_held = False

            def on_press(key):
                try:
                    if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                        self._ctrl_held = True
                    elif key == keyboard.Key.esc:
                        logger.info("[ACTION PLAYBACK SERVICE] Escape pressed - stopping playback")
                        self._stop_playback()
                        return False  # Stop listener
                    elif key == keyboard.Key.space and self._ctrl_held:
                        logger.info("[ACTION PLAYBACK SERVICE] Ctrl+Space pressed - toggling pause")
                        QTimer.singleShot(0, self._toggle_pause_playback)
                except Exception as e:
                    logger.error(f"[ACTION PLAYBACK SERVICE] Error in key listener: {e}")
            
            def on_release(key):
                if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
                    self._ctrl_held = False
            
            self.escape_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self.escape_listener.start()
            logger.info("[ACTION PLAYBACK SERVICE] Started Escape/Ctrl+Space key listener for playback")
        except Exception as e:
            logger.error(f"[ACTION PLAYBACK SERVICE] Error starting key listener: {e}", exc_info=True)
    
    def _start_macos_escape_listener(self):
        """Start macOS-specific key listener using NSEvent global monitor"""
        try:
            from AppKit import NSEvent, NSKeyDownMask
            try:
                from Quartz.CoreGraphics import kVK_Escape, kVK_Space
            except ImportError:
                kVK_Escape = 53
                kVK_Space = 49
            
            NSControlKeyMask = 1 << 18  # NSEventModifierFlagControl

            def handler(event):
                try:
                    key_code = event.keyCode()
                    flags = event.modifierFlags()
                    if key_code == kVK_Escape or key_code == 53:
                        logger.info("[ACTION PLAYBACK SERVICE] Escape pressed (macOS) - stopping playback")
                        QTimer.singleShot(0, self._stop_playback)
                    elif (key_code == kVK_Space or key_code == 49) and (flags & NSControlKeyMask):
                        logger.info("[ACTION PLAYBACK SERVICE] Ctrl+Space pressed (macOS) - toggling pause")
                        QTimer.singleShot(0, self._toggle_pause_playback)
                except Exception as e:
                    logger.error(f"[ACTION PLAYBACK SERVICE] Error in macOS key handler: {e}")
                return event
            
            self.escape_listener = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSKeyDownMask, handler
            )
            logger.info("[ACTION PLAYBACK SERVICE] Started macOS Escape/Ctrl+Space key listener using NSEvent")
        except ImportError as e:
            logger.warning(f"[ACTION PLAYBACK SERVICE] PyObjC not available for macOS key listener: {e}")
            self.escape_listener = None
        except Exception as e:
            logger.error(f"[ACTION PLAYBACK SERVICE] Error starting macOS key listener: {e}", exc_info=True)
            self.escape_listener = None
    
    def _stop_escape_listener(self):
        """Stop the Escape/Space key listener"""
        try:
            if self.escape_listener:
                import sys
                if sys.platform == 'darwin':
                    try:
                        from AppKit import NSEvent
                        NSEvent.removeMonitor_(self.escape_listener)
                        logger.info("[ACTION PLAYBACK SERVICE] Stopped macOS key listener")
                    except Exception as e:
                        logger.warning(f"[ACTION PLAYBACK SERVICE] Error removing macOS monitor: {e}")
                else:
                    self.escape_listener.stop()
                    logger.info("[ACTION PLAYBACK SERVICE] Stopped key listener")
                self.escape_listener = None
        except Exception as e:
            logger.error(f"[ACTION PLAYBACK SERVICE] Error stopping key listener: {e}", exc_info=True)
        # Clean up pause overlay
        if self._pause_overlay:
            try:
                self._pause_overlay.cleanup()
            except Exception:
                pass
            self._pause_overlay = None
        self._is_paused = False

    def _toggle_pause_playback(self):
        """Toggle pause/resume on the current playback."""
        if not self.current_playback_process or not self.current_playback_process.is_alive():
            return
        if self._is_paused:
            # Resume
            self._is_paused = False
            self.current_playback_process.resume()
            logger.info("[ACTION PLAYBACK SERVICE] Playback resumed")
            from distr.core.signals import signal_manager
            signal_manager.speak_text_directly.emit("Resumed")
            if self._pause_overlay:
                self._pause_overlay.dismiss()
        else:
            # Pause
            self._is_paused = True
            self.current_playback_process.pause()
            logger.info("[ACTION PLAYBACK SERVICE] Playback paused")
            from distr.core.signals import signal_manager
            signal_manager.speak_text_directly.emit("Paused")
            try:
                from distr.gui.countdown_overlay import PauseOverlay
                if not self._pause_overlay:
                    self._pause_overlay = PauseOverlay()
                self._pause_overlay.show_on_cursor_screen()
            except Exception as e:
                logger.error(f"[ACTION PLAYBACK SERVICE] Error showing pause overlay: {e}")
    
    def _stop_playback(self):
        """Stop the current playback"""
        try:
            logger.info("[ACTION PLAYBACK SERVICE] Stopping playback")
            
            # Stop the playback process
            if self.current_playback_process:
                self.current_playback_process.stop()
            
            # Stop escape listener
            self._stop_escape_listener()
            
            # Clean up
            self.current_playback_process = None
            
            # Emit signal for UI updates
            self.playback_failed.emit("Playback stopped by user")
            
            # Speak via TTS
            from distr.core.signals import signal_manager
            signal_manager.speak_text_directly.emit("Action Stopped")
            logger.info("[ACTION PLAYBACK SERVICE] Playback stopped - spoke 'Action Stopped' via TTS")
        except Exception as e:
            logger.error(f"[ACTION PLAYBACK SERVICE] Error stopping playback: {e}", exc_info=True)
    
    def stop_action(self):
        """Public method to stop the current action playback"""
        self._stop_playback()
    
    def stop(self):
        """Stop the service (alias for stop_action for shutdown compatibility)"""
        self._stop_playback()

