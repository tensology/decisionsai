"""Agent event-queue dispatcher mixin for the Application class.

Handles all events coming from the agent subprocess via ``agent_event_queue``.
The giant ``check_agent_events`` method is split into per-domain helpers so each
handler stays readable.
"""

import logging
import os
import base64
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6 import QtWidgets

from distr.core.signals import signal_manager
from distr.core.settings import load_settings_from_db, save_settings_to_db
from distr.core.integrations.telegram.response_format import (
    determine_response_format,
    load_response_format_settings,
)
from distr.core.human_engagement import (
    EngagementAttachment,
    EngagementIntent,
    HumanEngagementService,
    is_low_value_status_text,
)

logger = logging.getLogger(__name__)


class EventHandlerMixin:
    """Dispatches agent-process events to the appropriate handler."""

    # ------------------------------------------------------------------
    # Public entry point (called by QTimer in Application.__init__)
    # ------------------------------------------------------------------

    def check_agent_events(self):
        """Check for events from the agent process.

        Uses a bounded drain loop with ``get_nowait()`` directly (no
        ``empty()`` pre-check) to avoid the TOCTOU race where another
        thread drains the queue between the ``empty()`` call and the
        ``get_nowait()`` call.  The loop is capped at 50 events per
        tick to prevent starvation of the Qt event loop.
        """
        import queue as _queue

        # Lazy-init event dedup cache
        if not hasattr(self, '_event_dedup_cache'):
            self._event_dedup_cache = {}
        else:
            # Periodic cleanup: remove entries older than 10s to prevent unbounded growth
            now = time.time()
            self._event_dedup_cache = {
                k: v for k, v in self._event_dedup_cache.items() if now - v < 10.0
            }

        for _ in range(50):
            try:
                event, data = self.agent_event_queue.get_nowait()
                logger.info(f"[EVENT QUEUE] Received event: {event} with data: {data}")
                self._dispatch_agent_event(event, data)
            except _queue.Empty:
                break
            except Exception as e:
                logger.error(f"Error processing agent event: {e}")

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    def _dispatch_agent_event(self, event: str, data: dict):
        """Route *event* to the correct handler method."""

        # --- Telegram transcription ---
        if event == 'telegram_transcription_result':
            self._evt_telegram_transcription(data)

        # --- STT / dictation / hands-free ---
        elif event in ('stt_ready', 'stt_capture_started', 'stt_capture_stopped',
                        'stt_hands_free_glow_on', 'stt_hands_free_glow_off',
                        'dictation_started', 'dictation_stopped', 'set_dictating',
                        'hands_free_mode_changed'):
            self._evt_stt_dictation(event, data)

        # --- TTS / player ---
        elif event in ('tts_started', 'playback_finished', 'tts_stopped', 'tts_error'):
            self._evt_tts_player(event, data)

        # --- Listening state ---
        elif event == 'voice_set_is_listening':
            self._evt_voice_listening(data)

        # --- Oracle ---
        elif event in ('oracle_change', 'change_oracle', 'hide_oracle', 'show_oracle'):
            self._evt_oracle(event, data)

        # --- Chat lifecycle ---
        elif event in ('chat_created', 'chat_updated', 'chat_deleted',
                        'current_chat_changed', 'model_hot_reload', 'load_chat'):
            self._evt_chat_lifecycle(event, data)

        # --- Chat stream ---
        elif event in ('chat_stream_started', 'chat_stream_token',
                        'chat_stream_finished', 'chat_stream_error',
                        'typing_indicator_changed', 'chat_message_added',
                        'transcription_progress'):
            self._evt_chat_stream(event, data)

        # --- Actions ---
        elif event in ('action_created', 'start_action_recording',
                        'stop_action_recording', 'play_action_by_name',
                        'stop_action', 'send_text_input', 'speak_text_directly',
                        'speak_on_desktop',
                        'transcription_for_action_name', 'set_action_name'):
            self._evt_actions(event, data)

        # --- Workflows (websocket refresh) ---
        elif event == 'step_runner_updated':
            try:
                from distr.gui.web.workflow_events import increment_workflow_updated
                increment_workflow_updated()
            except ImportError:
                pass

        # --- Workflow feedback ---
        elif event == 'step_waiting_for_feedback':
            self._evt_step_waiting_for_feedback(data)

        # --- Telegram send ---
        elif event == 'send_to_telegram':
            self._evt_send_to_telegram(data)
        elif event == 'send_file_to_telegram':
            self._evt_send_file_to_telegram(data)

        # --- Misc ---
        elif event == 'exit_app':
            logger.info("[EVENT QUEUE] Received exit_app event from agent process - quitting application")
            self.quit()
        elif event == 'restart_app':
            logger.info("[EVENT QUEUE] Received restart_app event from agent process - restarting application")
            if hasattr(self, 'oracle_window') and self.oracle_window:
                self.oracle_window.restart_app()
            else:
                logger.warning("[EVENT QUEUE] No oracle_window available for restart")
        elif event == 'get_current_mouse_screen':
            self._evt_get_mouse_screen()
        elif event == 'file_operation_confirmation_request':
            self._evt_file_operation_confirmation(data)
        elif event == 'rename_preview_request':
            self._evt_rename_preview(data)

    def _force_oracle_idle_if_ptt_stale(self, reason: str) -> None:
        """Recover Oracle hook when ptt_active is stale.

        If hook is still `ptt_active` but the Oracle window no longer has an
        active hold-to-talk request, force the hook back to idle.
        """
        try:
            if not hasattr(self, 'oracle_window') or not self.oracle_window:
                return
            ow = self.oracle_window
            dispatcher = getattr(ow, '_event_dispatcher', None)
            if not dispatcher:
                return
            if dispatcher.get_current_hook() != "ptt_active":
                return

            hold_active = bool(getattr(ow, 'hold_to_talk_active', False))
            ptt_requested = bool(getattr(ow, 'ptt_requested', False))
            if hold_active or ptt_requested:
                return

            logger.warning("[EVENT QUEUE] Oracle hook stale on ptt_active; forcing idle (%s)", reason)
            dispatcher.force_idle(reason=reason)
        except Exception:
            logger.debug("[EVENT QUEUE] Failed stale-ptt idle recovery", exc_info=True)

    # ------------------------------------------------------------------
    # STT / dictation
    # ------------------------------------------------------------------

    def _evt_stt_dictation(self, event, data):
        if event == 'stt_ready':
            logger.info("[EVENT QUEUE] STT ready - PTT is now safe to use")
            signal_manager.stt_ready.emit()
        elif event == 'stt_capture_started':
            signal_manager.stt_capture_started.emit()
        elif event == 'stt_capture_stopped':
            signal_manager.stt_capture_stopped.emit()
        elif event == 'stt_hands_free_glow_on':
            signal_manager.stt_hands_free_glow_on.emit()
        elif event == 'stt_hands_free_glow_off':
            signal_manager.stt_hands_free_glow_off.emit()
        elif event == 'dictation_started':
            signal_manager.dictation_started.emit()
        elif event == 'dictation_stopped':
            signal_manager.dictation_stopped.emit()
        elif event == 'set_dictating':
            self._send_command_to_agent('set_dictating', {'enabled': data.get('enabled', False)})
        elif event == 'hands_free_mode_changed':
            signal_manager.hands_free_mode_changed.emit(data.get('enabled', False))

    # ------------------------------------------------------------------
    # TTS / player
    # ------------------------------------------------------------------

    def _evt_tts_player(self, event, data):
        # Lazy-init TTS UI lifecycle state
        if not hasattr(self, '_tts_active_sessions'):
            self._tts_active_sessions = 0
        if not hasattr(self, '_tts_pending_non_interrupt_closes'):
            self._tts_pending_non_interrupt_closes = 0
        if not hasattr(self, '_tts_non_interrupt_fallback_timer'):
            self._tts_non_interrupt_fallback_timer = QTimer(self)
            self._tts_non_interrupt_fallback_timer.setSingleShot(True)
            self._tts_non_interrupt_fallback_timer.timeout.connect(self._on_tts_non_interrupt_fallback_timeout)
        if not hasattr(self, '_tts_player_generation'):
            self._tts_player_generation = 0
        if event == 'tts_started':
            source = data.get("source") if isinstance(data, dict) else None
            if source != "transport":
                logger.info(
                    "[EVENT QUEUE] Provider tts_started received before confirmed audio output; "
                    "deferring player open until transport audio starts (source=%s)",
                    source or "provider",
                )
                return

            # Dedup bursty duplicate starts (e.g., direct speak + provider start).
            # Some paths emit two near-identical tts_started events within ~1s,
            # which creates double session accounting and choppy/non-fluid playback.
            dedup_key = ('tts_started',)
            now = time.time()
            last_time = self._event_dedup_cache.get(dedup_key, 0)
            player_visible = bool(
                hasattr(self, 'player_window') and self.player_window and self.player_window.isVisible()
            )
            if now - last_time < 1.2 and self._tts_active_sessions > 0:
                logger.debug(
                    "[EVENT QUEUE] Dedup: skipping duplicate tts_started (dt=%.3fs active_sessions=%d visible=%s)",
                    now - last_time,
                    self._tts_active_sessions,
                    player_visible,
                )
                return
            self._event_dedup_cache[dedup_key] = now
            self._tts_active_sessions += 1
            self.last_tts_start_time = time.time()
            logger.info(
                "[EVENT QUEUE] tts_started: active_sessions=%d player_visible=%s",
                self._tts_active_sessions,
                player_visible,
            )
            if not hasattr(self, '_player_safety_timer'):
                self._player_safety_timer = QTimer(self)
                self._player_safety_timer.setSingleShot(True)
                self._player_safety_timer.timeout.connect(self._on_player_safety_timeout)
            self._player_safety_timer.start(300000)
            self._tts_player_generation += 1
            player_generation = self._tts_player_generation
            if hasattr(self, 'oracle_window') and self.oracle_window:
                if hasattr(self.oracle_window, 'position_player_window'):
                    QtWidgets.QApplication.processEvents()
                    self.oracle_window.position_player_window()
                    QtWidgets.QApplication.processEvents()
            QTimer.singleShot(20, lambda gen=player_generation: self._emit_player_signal_if_tts_active(gen, "show"))
            QTimer.singleShot(350, lambda gen=player_generation: self._emit_player_signal_if_tts_active(gen, "play"))

        elif event == 'playback_finished':
            logger.info("[EVENT QUEUE] Playback finished event received - closing player immediately")
            # playback_finished is authoritative end-of-utterance for normal paths
            self._tts_active_sessions = max(0, self._tts_active_sessions - 1)
            if self._tts_pending_non_interrupt_closes > 0:
                self._tts_pending_non_interrupt_closes = max(0, self._tts_pending_non_interrupt_closes - 1)
            logger.info(
                "[EVENT QUEUE] playback_finished: active_sessions=%d pending_non_interrupt=%d",
                self._tts_active_sessions,
                self._tts_pending_non_interrupt_closes,
            )
            if hasattr(self, '_player_safety_timer') and self._player_safety_timer.isActive():
                self._player_safety_timer.stop()
            if self._tts_active_sessions <= 0:
                self._tts_player_generation += 1
            # Important: do NOT cancel fallback if there are still pending non-interrupt
            # closes (multi-utterance/subagent bursts may emit fewer playback_finished events).
            if self._tts_pending_non_interrupt_closes <= 0:
                if hasattr(self, '_tts_non_interrupt_fallback_timer') and self._tts_non_interrupt_fallback_timer.isActive():
                    self._tts_non_interrupt_fallback_timer.stop()
            else:
                if hasattr(self, '_tts_non_interrupt_fallback_timer'):
                    self._tts_non_interrupt_fallback_timer.start(2500)
            self._close_player_if_tts_complete("playback_finished")

        elif event == 'tts_stopped':
            duration = data.get('duration', 0.0)
            logger.info(f"[EVENT QUEUE] TTS stopped event received, duration: {duration}")
            interrupted = bool(data.get('interrupted', False))
            logger.info(
                "[EVENT QUEUE] tts_stopped details: interrupted=%s active_sessions=%d",
                interrupted,
                self._tts_active_sessions,
            )
            # Dedup all bursty tts_stopped events (interrupt and non-interrupt).
            # We round duration to reduce float jitter while still distinguishing
            # genuinely different utterances.
            dedup_key = ('tts_stopped', interrupted, round(float(duration or 0.0), 1))
            now = time.time()
            last_time = self._event_dedup_cache.get(dedup_key, 0)
            if now - last_time < 0.6:
                logger.debug(
                    "[EVENT QUEUE] Dedup: skipping duplicate tts_stopped interrupted=%s duration=%.3f",
                    interrupted,
                    float(duration or 0.0),
                )
                return
            self._event_dedup_cache[dedup_key] = now
            if duration <= 0.0 and not interrupted and self._tts_active_sessions > 0:
                logger.warning(
                    "[EVENT QUEUE] Ignoring zero-duration tts_stopped while playback is active "
                    "(active_sessions=%d). Waiting for playback_finished or safety fallback.",
                    self._tts_active_sessions,
                )
                return

            # Interrupt stops are authoritative. Zero-duration non-interrupt
            # stops only close the player when no active playback session exists;
            # otherwise they can race ahead of audio and make TTS look silent.
            if interrupted or duration <= 0.0:
                logger.info("[EVENT QUEUE] TTS interrupted (duration <= 0), closing player immediately")
                self._tts_active_sessions = 0
                self._tts_pending_non_interrupt_closes = 0
                self._tts_player_generation += 1
                if hasattr(self, '_player_safety_timer') and self._player_safety_timer.isActive():
                    self._player_safety_timer.stop()
                if hasattr(self, '_tts_non_interrupt_fallback_timer') and self._tts_non_interrupt_fallback_timer.isActive():
                    self._tts_non_interrupt_fallback_timer.stop()
                QtWidgets.QApplication.processEvents()
                self._close_player_if_tts_complete("tts_stopped interrupt")
            else:
                # Normal non-interrupt stop: playback may still be draining.
                if self._tts_active_sessions <= 0:
                    logger.warning(
                        "[EVENT QUEUE] Non-interrupt tts_stopped with no confirmed transport playback "
                        "(duration=%.3f). Player was not opened; ignoring close.",
                        float(duration or 0.0),
                    )
                    return
                # Track this utterance as awaiting playback_finished, but keep a
                # duration-aware fallback for transports that never emit it.
                self._tts_pending_non_interrupt_closes += 1
                fallback_ms = int(max(3000, min(90000, (float(duration) * 1000.0) + 1500.0)))
                if hasattr(self, '_tts_non_interrupt_fallback_timer'):
                    self._tts_non_interrupt_fallback_timer.start(fallback_ms)
                logger.info(
                    "[EVENT QUEUE] Non-interrupt tts_stopped received; waiting for playback_finished (fallback=%dms active_sessions=%d pending=%d)",
                    fallback_ms,
                    self._tts_active_sessions,
                    self._tts_pending_non_interrupt_closes,
                )

        elif event == 'tts_error':
            provider = data.get('provider', 'TTS')
            error_type = data.get('error_type', 'unknown')
            message = data.get('message', 'An error occurred with the TTS service')
            now = time.time()
            last_key = getattr(self, '_last_tts_error_key', None)
            last_at = getattr(self, '_last_tts_error_at', 0.0)
            error_key = (provider, error_type, message)
            is_duplicate = error_key == last_key and now - last_at < 60.0
            self._last_tts_error_key = error_key
            self._last_tts_error_at = now
            if is_duplicate:
                logger.debug(f"[EVENT QUEUE] Suppressed duplicate {provider} TTS error ({error_type})")
                return
            logger.warning(f"[EVENT QUEUE] {provider} TTS unavailable ({error_type}): {message}")
            msg_box = QtWidgets.QMessageBox()
            msg_box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            msg_box.setWindowTitle(f"{provider} TTS Error")
            msg_box.setText(message)
            msg_box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
            msg_box.setStyleSheet("""
                QMessageBox { background-color: #343541; color: #ececf1; }
                QMessageBox QLabel { color: #ececf1; font-size: 14px; }
                QMessageBox QPushButton {
                    background-color: #007bff; color: white; border: none;
                    padding: 10px 20px; border-radius: 4px; font-weight: bold;
                    font-size: 14px; min-width: 100px;
                }
                QMessageBox QPushButton:hover { background-color: #0069d9; }
            """)
            msg_box.show()
            if error_type == 'quota_exceeded':
                logger.info(f"[EVENT QUEUE] Quota exceeded for {provider} - switching to Kokoro automatically")
                self._handle_quota_exceeded_fallback()

    def _on_player_safety_timeout(self):
        """Fallback: hide player if playback_finished was never received (e.g. agent crash)."""
        self._tts_active_sessions = 0
        if hasattr(self, '_tts_player_generation'):
            self._tts_player_generation += 1
        if hasattr(self, 'player_window') and self.player_window and self._player_is_on_screen():
            logger.warning("[EVENT QUEUE] Player safety timeout (5 min) - hiding player (playback_finished never received)")
            self._hide_player_window_immediate("player_safety_timeout")

    def _on_tts_non_interrupt_fallback_timeout(self):
        """Fallback close for missing playback_finished on non-interrupt utterances."""
        if self._tts_pending_non_interrupt_closes <= 0:
            return

        self._tts_pending_non_interrupt_closes = max(0, self._tts_pending_non_interrupt_closes - 1)
        if self._tts_active_sessions > 0:
            self._tts_active_sessions = max(0, self._tts_active_sessions - 1)
        if self._tts_active_sessions <= 0 and hasattr(self, '_tts_player_generation'):
            self._tts_player_generation += 1
        logger.warning(
            "[EVENT QUEUE] Missing playback_finished after non-interrupt tts_stopped - fallback closing one session (active=%d pending=%d)",
            self._tts_active_sessions,
            self._tts_pending_non_interrupt_closes,
        )
        self._close_player_if_tts_complete("tts_non_interrupt_fallback")
        if self._tts_pending_non_interrupt_closes > 0 and hasattr(self, '_tts_non_interrupt_fallback_timer'):
            # Drain additional missing completions conservatively.
            self._tts_non_interrupt_fallback_timer.start(2500)

    def _emit_player_signal_if_tts_active(self, generation: int, action: str):
        """Drop delayed player callbacks once playback has already ended."""
        if generation != getattr(self, '_tts_player_generation', 0) or getattr(self, '_tts_active_sessions', 0) <= 0:
            logger.debug(
                "[EVENT QUEUE] Dropped stale player_%s callback (generation=%s current=%s active=%s)",
                action,
                generation,
                getattr(self, '_tts_player_generation', 0),
                getattr(self, '_tts_active_sessions', 0),
            )
            return
        if action == "show":
            signal_manager.show_player_window.emit()
        elif action == "play":
            signal_manager.player_play.emit()

    def _player_is_on_screen(self) -> bool:
        """True when the desktop player is visible or stuck in a fade-out."""
        player = getattr(self, 'player_window', None)
        if not player:
            return False
        if hasattr(player, 'is_on_screen'):
            return bool(player.is_on_screen())
        return bool(player.isVisible())

    def _hide_player_window_immediate(self, reason: str) -> None:
        """Hide the desktop player without fade guards."""
        player = getattr(self, 'player_window', None)
        if player and hasattr(player, 'hide_window_immediate'):
            player.hide_window_immediate()
        else:
            signal_manager.player_stop.emit()
            signal_manager.emit_hide_player_window()
        logger.info("[EVENT QUEUE] Hid player window (%s)", reason)

    def _close_player_if_tts_complete(self, reason: str):
        """Close player when no active TTS sessions remain."""
        player_on_screen = self._player_is_on_screen()
        if self._tts_active_sessions != 0:
            if not player_on_screen:
                return
            logger.warning(
                "[EVENT QUEUE] Force-closing visible player with active_sessions=%d (%s)",
                self._tts_active_sessions,
                reason,
            )
            self._tts_active_sessions = 0
            self._tts_pending_non_interrupt_closes = 0
        if player_on_screen:
            self._hide_player_window_immediate(reason)
        else:
            signal_manager.player_stop.emit()
            logger.info("[EVENT QUEUE] Player not on screen (%s)", reason)

        # Defensive: if oracle is still in 'thinking' after playback is done,
        # force it back to idle so skin state cannot stick.
        try:
            if hasattr(self, 'oracle_window') and self.oracle_window and hasattr(self.oracle_window, '_event_dispatcher'):
                dispatcher = self.oracle_window._event_dispatcher
                if dispatcher and dispatcher.get_current_hook() == "thinking":
                    dispatcher.revert_hook("thinking", trigger="events:player_close_thinking_guard")
                self._force_oracle_idle_if_ptt_stale(f"player_close:{reason}")
        except Exception:
            logger.debug("[EVENT QUEUE] Failed to apply idle-reset guard after %s", reason, exc_info=True)

    # ------------------------------------------------------------------
    # Voice listening state
    # ------------------------------------------------------------------

    def _evt_voice_listening(self, data):
        enabled = data.get('enabled', True)
        logger.info(f"[EVENT QUEUE] Received voice_set_is_listening event: {enabled}")
        signal_manager.voice_set_is_listening.emit(enabled)
        if hasattr(self, 'oracle_window') and self.oracle_window:
            if enabled:
                self.oracle_window.enable_tray()
            else:
                self.oracle_window.disable_tray()
        else:
            logger.warning("[EVENT QUEUE] valid oracle_window not found, cannot update UI")

    # ------------------------------------------------------------------
    # Oracle
    # ------------------------------------------------------------------

    def _evt_oracle(self, event, data):
        if event == 'oracle_change':
            filename = data.get('filename')
            if filename:
                signal_manager.direct_oracle_change.emit(filename)
            else:
                logger.warning(f"[EVENT QUEUE] oracle_change event missing filename: {data}")
        elif event == 'change_oracle':
            signal_manager.change_oracle.emit()
        elif event == 'change_oracle_previous':
            signal_manager.change_oracle_previous.emit()
        elif event == 'hide_oracle':
            signal_manager.hide_oracle.emit()
        elif event == 'show_oracle':
            signal_manager.show_oracle.emit()

    # ------------------------------------------------------------------
    # Chat lifecycle
    # ------------------------------------------------------------------

    def _evt_chat_lifecycle(self, event, data):
        if event == 'chat_created':
            signal_manager.chat_created.emit(data.get('chat_id'))
        elif event == 'chat_updated':
            signal_manager.chat_updated.emit(data.get('chat_id'))
        elif event == 'chat_deleted':
            signal_manager.chat_deleted.emit(data.get('chat_id'))
        elif event == 'current_chat_changed':
            chat_id = data.get('chat_id')
            try:
                self._suppress_current_chat_relay = True
                signal_manager.current_chat_changed.emit(chat_id)
            finally:
                self._suppress_current_chat_relay = False
        elif event == 'model_hot_reload':
            provider = data.get('provider', 'OpenAI')
            model_name = data.get('model_name', '')
            chat_id = data.get('chat_id')
            logger.info(f"[EVENT QUEUE] Received model_hot_reload from agent: provider={provider}, model_name={model_name}, chat_id={chat_id}")
            self._send_command_to_agent('hot_swap_llm', {
                'provider': provider or 'Ollama',
                'model_name': model_name or '',
                'chat_id': chat_id,
            })
        elif event == 'load_chat':
            logger.info(f"[EVENT QUEUE] Received load_chat event: {data.get('chat_id')} (web chat uses API; no GUI to load)")

    # ------------------------------------------------------------------
    # Chat stream
    # ------------------------------------------------------------------

    def _evt_chat_stream(self, event, data):
        if event == 'chat_stream_started':
            # Dedup: reset finished flag when a new stream starts
            self._last_stream_finished_chat_id = None
            signal_manager.chat_stream_started.emit(data.get('chat_id'))
        elif event == 'chat_stream_token':
            signal_manager.chat_stream_token.emit(data.get('token'))
        elif event == 'chat_stream_finished':
            chat_id = data.get('chat_id')
            response_text = data.get('response_text')
            self._last_stream_response_text = response_text
            # Do not time-dedupe stream_finished: interrupt cleanup often fires within 2s of
            # normal completion; dropping it leaves the web UI stuck on the streaming bubble.
            signal_manager.chat_stream_finished.emit(chat_id)
            self._force_oracle_idle_if_ptt_stale("chat_stream_finished")
        elif event == 'transcription_progress':
            _tcid = data.get('chat_id')
            if _tcid is None:
                return
            signal_manager.transcription_progress.emit(
                int(_tcid),
                data.get('status_text') or '',
                bool(data.get('done')),
                bool(data.get('clear_live_preview')),
            )
        elif event == 'chat_stream_error':
            error = data.get('error')
            chat_id = data.get('chat_id')
            signal_manager.chat_stream_error.emit(error)
        elif event == 'typing_indicator_changed':
            show = data.get('show')
            # Dedup: skip duplicate typing_indicator_changed with same value within 1s
            dedup_key = ('typing_indicator_changed', show)
            now = time.time()
            last_time = self._event_dedup_cache.get(dedup_key, 0)
            if now - last_time < 1.0:
                logger.debug("[EVENT QUEUE] Dedup: skipping duplicate typing_indicator_changed show=%s", show)
                return
            self._event_dedup_cache[dedup_key] = now
            signal_manager.typing_indicator_changed.emit(show)
            # Also stop the Telegram typing loop when typing indicator is turned off
            if not show and hasattr(self, 'telegram_manager') and self.telegram_manager:
                try:
                    self.telegram_manager._stop_typing_loop()
                except Exception:
                    pass
        elif event == 'chat_message_added':
            signal_manager.chat_message_added.emit(data.get('chat_id'), data.get('role'), data.get('content'))

    # ------------------------------------------------------------------
    # Actions (recording, playback, naming)
    # ------------------------------------------------------------------

    def _evt_actions(self, event, data):
        if event == 'action_created':
            action_id = data.get('id')
            title = data.get('title', '')
            logger.info(f"[EVENT QUEUE] Received action_created event: id={action_id}, title='{title}'")
            signal_manager.action_created.emit(action_id)
            try:
                from distr.gui.web.workflow_events import increment_workflow_updated
                increment_workflow_updated()
            except Exception:
                pass
        elif event == 'start_action_recording':
            action_id = data.get('action_id')
            if action_id:
                signal_manager.start_action_recording_with_id.emit(action_id)
            else:
                signal_manager.start_action_recording.emit()
        elif event == 'stop_action_recording':
            signal_manager.stop_action_recording.emit()
        elif event == 'play_action_by_name':
            action_name = data.get('action_name')
            if hasattr(self, 'action_playback_service') and self.action_playback_service:
                self.action_playback_service.play_action_by_name(action_name)
            else:
                logger.error("[EVENT QUEUE] Action playback service not available")
        elif event == 'stop_action':
            if hasattr(self, 'action_playback_service') and self.action_playback_service:
                self.action_playback_service.stop_action()
            else:
                logger.error("[EVENT QUEUE] Action playback service not available")
        elif event == 'send_text_input':
            text = data.get('text', '')
            logger.info(f"[EVENT QUEUE] Received send_text_input event: {text[:50]}...")
            signal_manager.send_text_input.emit(text, False, None, None)
        elif event == 'speak_text_directly':
            text = data.get('text', '')
            logger.info(f"[EVENT QUEUE] Received speak_text_directly event: {text}")
            signal_manager.speak_text_directly.emit(text)
        elif event == 'speak_on_desktop':
            text = data.get('text', '')
            logger.info(f"[EVENT QUEUE] Received speak_on_desktop event: {text}")
            signal_manager.speak_text_directly.emit(text)
        elif event == 'transcription_for_action_name':
            text = data.get('text', '').strip()
            if hasattr(self, 'recorder_host') and self.recorder_host:
                waiting_id = getattr(self.recorder_host, 'waiting_for_action_name_id', None)
                if waiting_id and text:
                    logger.info(f"[EVENT QUEUE] Using transcription '{text}' as name for action {waiting_id}")
                    signal_manager.set_action_name.emit(waiting_id, text)
                    return
        elif event == 'set_action_name':
            action_id = data.get('action_id')
            name = data.get('name', '')
            if action_id and name:
                signal_manager.set_action_name.emit(action_id, name)

    # ------------------------------------------------------------------
    # Workflow feedback events
    # ------------------------------------------------------------------

    def _evt_step_waiting_for_feedback(self, data):
        """Handle a step entering the waiting-for-feedback state.

        Emits the ``step_waiting_for_feedback`` signal so the main agent
        (TTS / voice) can speak the result to the user and gather input.
        """
        step_id = data.get('step_id')
        workflow_id = data.get('workflow_id')
        run_id = data.get('run_id')
        result = data.get('result', '')

        if step_id is None or workflow_id is None or run_id is None:
            logger.warning(
                "step_waiting_for_feedback event missing required fields: %s", data,
            )
            return

        try:
            signal_manager.step_waiting_for_feedback.emit(
                step_id, workflow_id, run_id, result,
            )
            logger.info(
                "[EVENT QUEUE] Emitted step_waiting_for_feedback for step %d, "
                "workflow %d, run %d",
                step_id, workflow_id, run_id,
            )
        except Exception as e:
            logger.error(
                "Failed to emit step_waiting_for_feedback: %s", e, exc_info=True,
            )

    # ------------------------------------------------------------------
    # Telegram transcription
    # ------------------------------------------------------------------

    def _evt_telegram_transcription(self, data):
        request_id = data.get('request_id')
        success = data.get('success', False)
        transcript = data.get('transcript')
        error = data.get('error')
        input_type = data.get('input_type', 'voice')
        request_id_str = str(request_id or "")

        # Generic in-app STT callbacks (e.g., WhatsApp/media transcription via agent STT service).
        if hasattr(self, '_pending_stt_callbacks'):
            cb = self._pending_stt_callbacks.pop(request_id, None)
            if cb:
                result_event, result_holder = cb
                result_holder['transcript'] = transcript if success else None
                result_holder['error'] = error
                result_event.set()
                return

        # Check if this is a pending remote UI voice transcription
        if hasattr(self, 'telegram_manager') and self.telegram_manager:
            if hasattr(self.telegram_manager, '_pending_voice_callbacks'):
                cb = self.telegram_manager._pending_voice_callbacks.pop(request_id, None)
                if cb:
                    result_event, result_holder = cb
                    result_holder['transcript'] = transcript if success else None
                    result_holder['error'] = error
                    result_event.set()
                    return  # Don't process as normal Telegram transcription

        # Non-Telegram STT flows (e.g., WhatsApp/media helper transcription) use
        # file_stt_* request IDs. If their callback already timed out/cleared,
        # the late result must not be routed into Telegram agent input.
        if request_id_str.startswith("file_stt_"):
            logger.info(
                "[EVENT QUEUE] ⏭️ Ignoring stale non-Telegram transcription result "
                "(request_id: %s, success=%s)",
                request_id,
                success,
            )
            return

        if success and transcript:
            logger.info("[EVENT QUEUE] ✅ Telegram voice transcription successful (request_id: %s): '%s'", request_id, transcript[:200])
            try:
                threading.current_thread().telegram_request = True
                # Route through the batch buffer so input_type="voice" is
                # propagated to _flush_telegram_batch → _current_input_type,
                # which downstream consumers read to decide text vs voice response.
                if hasattr(self, 'telegram_manager') and self.telegram_manager:
                    self.telegram_manager._enqueue_telegram_batch(str(transcript), input_type=input_type)
                else:
                    # Same agent path as batched Telegram; no thread id when manager absent (no mapping persist).
                    from distr.core.integrations.bus import get_integration_message_bus

                    get_integration_message_bus().deliver_telegram_user_input(
                        text=str(transcript),
                        image_path=None,
                        telegram_chat_id=None,
                        speak=None,
                        input_type=input_type,
                    )
                logger.info("[EVENT QUEUE] 📤 Forwarded Telegram voice transcription to agent (input_type=%s): '%s'", input_type, transcript[:100])
            except Exception as e:
                logger.error("[EVENT QUEUE] ❌ Failed to forward Telegram transcription to agent: %s", e, exc_info=True)
                try:
                    if hasattr(self, 'telegram_manager') and self.telegram_manager:
                        self.telegram_manager._stop_typing_loop()
                        self.telegram_manager.send_to_telegram(
                            "⚠️ I transcribed that voice note, but couldn't hand it to the agent. Please try once more."
                        )
                except Exception as notify_error:
                    logger.error(
                        "[EVENT QUEUE] ❌ Failed to notify Telegram transcription handoff failure: %s",
                        notify_error,
                        exc_info=True,
                    )
        else:
            logger.warning("[EVENT QUEUE] ❌ Telegram voice transcription failed (request_id: %s): %s", request_id, error)
            try:
                if hasattr(self, 'telegram_manager') and self.telegram_manager:
                    self.telegram_manager._stop_typing_loop()
                    self.telegram_manager.send_to_telegram(
                        "⚠️ I couldn't transcribe that voice note. Please try resending it or send it as text."
                    )
            except Exception as e:
                logger.error("[EVENT QUEUE] ❌ Failed to notify Telegram transcription failure: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # send_to_telegram  (the largest single handler)
    # ------------------------------------------------------------------

    def _evt_send_to_telegram(self, data):
        text = data.get('text', '')
        is_done = data.get('is_done', False)
        provider = data.get('provider', '')
        analyzed_image_path = data.get('analyzed_image_path')

        logger.info(f"[EVENT QUEUE] Received send_to_telegram event: text='{text[:50]}...', is_done={is_done}, provider={provider}, analyzed_image={analyzed_image_path}")

        has_telegram_manager = hasattr(self, 'telegram_manager')
        telegram_connected = has_telegram_manager and self.telegram_manager.is_connected() if has_telegram_manager else False

        remote_ctx = None
        if has_telegram_manager and self.telegram_manager:
            remote_ctx = self._consume_remote_response_context()

        if remote_ctx and provider in ('kokoro', 'tool') and has_telegram_manager and telegram_connected:
            app_ref = self

            def send_to_remote_thread():
                try:
                    app_ref._send_to_remote_worker(data, remote_ctx)
                except Exception as e:
                    logger.error(f"Error in send_to_remote thread: {e}", exc_info=True)

            threading.Thread(target=send_to_remote_thread, daemon=True, name="SendToRemoteApp").start()
        elif (provider == 'kokoro' or provider == 'tool') and has_telegram_manager:
            # Capture self reference for thread
            if not telegram_connected:
                logger.warning("[EVENT QUEUE] Telegram not connected; queuing outbound response for reconnect")
            app_ref = self

            def send_to_telegram_thread():
                try:
                    app_ref._send_to_telegram_worker(data)
                except Exception as e:
                    logger.error(f"Error in send_to_telegram thread: {e}", exc_info=True)

            threading.Thread(target=send_to_telegram_thread, daemon=True, name="SendToTelegram").start()
        else:
            if provider not in ('kokoro', 'tool'):
                logger.warning(f"[EVENT QUEUE] ⚠️ Ignoring send_to_telegram (provider={provider}, not Kokoro/tool)")
            elif not has_telegram_manager:
                logger.warning("[EVENT QUEUE] ⚠️ Ignoring send_to_telegram (Telegram manager not available)")
            elif not telegram_connected:
                logger.warning(f"[EVENT QUEUE] ⚠️ Telegram unavailable for send_to_telegram (manager exists: {has_telegram_manager})")

    def _consume_remote_response_context(self):
        """Get and clear one pending remote-app response context, if present."""
        try:
            if not hasattr(self, 'telegram_manager') or not self.telegram_manager:
                return None

            ctx = getattr(self.telegram_manager, '_pending_remote_agent_response', None)
            if not ctx:
                return None

            created_at = float(ctx.get("created_at", 0) or 0)
            age_s = time.time() - created_at if created_at else 0
            if created_at and age_s > 180:
                logger.warning(
                    "[REMOTE TTS] Dropping stale remote context: request_id=%s age=%.1fs",
                    ctx.get("request_id"),
                    age_s,
                )
                setattr(self.telegram_manager, '_pending_remote_agent_response', None)
                return None

            setattr(self.telegram_manager, '_pending_remote_agent_response', None)
            logger.info(
                "[REMOTE TTS] Consumed remote context: request_id=%s source=%s mode=%s",
                ctx.get("request_id"),
                ctx.get("source_command"),
                ctx.get("mode"),
            )
            return ctx
        except Exception:
            logger.exception("[REMOTE TTS] Failed consuming remote context")
            return None

    def _send_to_remote_worker(self, data, remote_ctx):
        """Build and send a remote-app assistant response payload plus Ogg audio stream."""
        text = (data.get('text') or '').strip()
        if not text:
            logger.warning(
                "[REMOTE TTS] Empty assistant text for remote response (request_id=%s)",
                remote_ctx.get("request_id"),
            )
            return

        request_id = remote_ctx.get("request_id")

        def is_cancelled() -> bool:
            try:
                cancelled = getattr(self.telegram_manager, "_cancelled_remote_audio_requests", set())
                return bool(request_id and request_id in cancelled)
            except Exception:
                return False

        logger.info(
            "[REMOTE TTS] Preparing response for remote-app: request_id=%s chars=%d",
            request_id,
            len(text),
        )

        generated_audio_file = None
        stream_audio_file = None
        audio_stream_ready = False

        if not is_cancelled():
            generated_audio_file = self._telegram_generate_tts(text)
            stream_audio_file = generated_audio_file
            if stream_audio_file and stream_audio_file.exists() and str(stream_audio_file).endswith('.wav'):
                stream_audio_file = self._convert_wav_to_ogg(stream_audio_file)

            if stream_audio_file and stream_audio_file.exists() and str(stream_audio_file).endswith(".ogg"):
                audio_stream_ready = True
                logger.info(
                    "[REMOTE TTS] Ogg stream ready: request_id=%s bytes=%d file=%s",
                    request_id,
                    stream_audio_file.stat().st_size,
                    os.path.basename(str(stream_audio_file)),
                )
            elif stream_audio_file and stream_audio_file.exists():
                logger.warning(
                    "[REMOTE TTS] Skipping non-Ogg remote audio stream: request_id=%s file=%s",
                    request_id,
                    stream_audio_file,
                )

        payload = {
            "type": "remote_agent_response",
            "request_id": request_id,
            "data": {
                "text": text,
                "mode": remote_ctx.get("mode") or "command",
                "source_command": remote_ctx.get("source_command"),
                "audio": None,
                "audio_streamed": audio_stream_ready,
                "audio_mime_type": "audio/ogg; codecs=opus" if audio_stream_ready else None,
            },
        }

        try:
            self.telegram_manager._send_websocket_message(payload)
            logger.info(
                "[REMOTE TTS] Sent remote_agent_response: request_id=%s audio_stream_ready=%s",
                request_id,
                audio_stream_ready,
            )

            if audio_stream_ready and stream_audio_file and not is_cancelled():
                from distr.core.integrations.telegram.remote_audio_stream import (
                    iter_remote_audio_stream_messages,
                    remote_audio_stopped_message,
                )

                for message in iter_remote_audio_stream_messages(
                    request_id=str(request_id),
                    audio_path=stream_audio_file,
                ):
                    if is_cancelled():
                        self.telegram_manager._send_websocket_message(
                            remote_audio_stopped_message(str(request_id), reason="user_stop")
                        )
                        logger.info("[REMOTE TTS] Remote audio stream stopped: request_id=%s", request_id)
                        break
                    self.telegram_manager._send_websocket_message(message)
                    if message.get("type") == "remote_agent_audio_chunk":
                        time.sleep(0.002)
        except Exception:
            logger.exception(
                "[REMOTE TTS] Failed sending remote_agent_response: request_id=%s",
                request_id,
            )
        finally:
            # Keep the same cleanup timing/behavior as Telegram worker path.
            time.sleep(2)
            self._telegram_cleanup_temp_files(stream_audio_file, None, None)
            if generated_audio_file and generated_audio_file != stream_audio_file:
                self._telegram_cleanup_temp_files(generated_audio_file, None, None)

    def _integration_reply_chat_id(self):
        """Internal chat id for routing connector replies (Discord / Slack)."""
        cm = getattr(self, "chat_manager", None)
        if cm is not None:
            try:
                cid = cm.get_current_chat()
                if cid is not None:
                    return int(cid)
            except Exception:
                logger.debug(
                    "integration reply: chat_manager.get_current_chat failed",
                    exc_info=True,
                )
        try:
            settings = load_settings_from_db()
            raw = settings.get("agent_current_chat_id")
            return int(raw) if raw is not None else None
        except Exception:
            return None

    def _try_route_integration_text_reply(self, text_to_send, data: dict) -> bool:
        """If this chat maps to Discord or Slack, enqueue text and skip Telegram."""
        if not text_to_send or not str(text_to_send).strip():
            return False
        provider = (data.get("provider") or "").strip()
        if provider not in ("kokoro", "tool"):
            return False
        chat_id = self._integration_reply_chat_id()
        if chat_id is None:
            return False

        from distr.core.integrations.bus import get_integration_message_bus
        from distr.core.integrations.outbound_state import get_discord_outbound_queue, get_slack_outbound_queue

        bus = get_integration_message_bus()
        body = str(text_to_send).strip()
        if len(body) > 40000:
            body = body[:40000]

        for platform, qgetter in (
            ("discord", get_discord_outbound_queue),
            ("slack", get_slack_outbound_queue),
        ):
            tid = bus.resolve_thread_id_for_chat(platform, chat_id)
            if not tid:
                continue
            q = qgetter()
            ok = q.push({"channel_id": tid, "text": body})
            if ok:
                logger.info(
                    "[INTEGRATION] Routed assistant text to %s thread=%s chat_id=%s",
                    platform,
                    tid,
                    chat_id,
                )
                return True
            logger.warning(
                "[INTEGRATION] %s outbound queue full — falling through to Telegram",
                platform,
            )
        return False

    # ---- worker (runs in thread) ----

    def _send_to_telegram_worker(self, data):
        """Heavy lifting for send_to_telegram — runs in a background thread."""
        # Inject input_type from telegram_manager if not already in event data.
        # The LLM service / TTS emitters don't have access to the manager, so
        # the event dict may arrive without input_type.  We read it here once
        # before any downstream logic needs it.
        if 'input_type' not in data:
            try:
                if hasattr(self, 'telegram_manager') and self.telegram_manager:
                    data['input_type'] = getattr(
                        self.telegram_manager, '_current_input_type', 'voice'
                    )
            except Exception:
                pass  # leave default in _telegram_prepare_llm_response

        text = data.get('text', '')
        is_done = data.get('is_done', False)
        analyzed_image_path = data.get('analyzed_image_path')
        audio_path_from_event = data.get('audio_file_path')
        screenshot_path_from_event = data.get('screenshot_path')
        policy_text = (text or data.get('voice_note_message') or '').strip()
        if policy_text and not data.get('bypass_engagement_policy'):
            low_value_status = is_low_value_status_text(policy_text)
            is_remote_link = "/api/remote/" in policy_text
            explicit_notification_intent = bool(
                data.get('explicit_notification_intent')
                or data.get('explicit_user_request')
                or data.get('notify_user')
                or data.get('requires_response')
                or data.get('engagement_priority') in ('high', 'urgent')
                or is_remote_link
            )
            engagement_kind = (
                data.get('engagement_kind')
                or ('remote_link' if is_remote_link else None)
                or ('status_update' if low_value_status else 'telegram_response')
            )
            if low_value_status and not explicit_notification_intent:
                engagement_kind = 'status_update'
            state_fingerprint = (
                data.get('state_fingerprint')
                or data.get('engagement_state_fingerprint')
                or self._telegram_engagement_state_fingerprint(policy_text)
            )
            decision = HumanEngagementService(
                telegram_manager=getattr(self, 'telegram_manager', None),
            ).decide(EngagementIntent(
                source=data.get('engagement_source') or 'app_events',
                surface='telegram',
                kind=engagement_kind,
                priority=data.get('engagement_priority') or ('low' if low_value_status else 'normal'),
                subject_type=data.get('engagement_subject_type') or ('status' if low_value_status else 'telegram_response'),
                subject_id=str(
                    data.get('engagement_subject_id')
                    or data.get('run_id')
                    or data.get('workflow_id')
                    or data.get('chat_id')
                    or 'telegram'
                ),
                state_fingerprint=str(state_fingerprint),
                body=policy_text,
                voice_body=policy_text,
                requires_response=bool(data.get('requires_response')),
                explicit_notification_intent=explicit_notification_intent,
                allow_voice=bool(data.get('allow_voice', True)) and not low_value_status and not is_remote_link,
                workflow_id=data.get('workflow_id'),
                run_id=data.get('run_id'),
                step_id=data.get('step_id'),
                project_id=data.get('project_id'),
                execution_session_id=data.get('execution_session_id'),
                thread_id=str(data.get('thread_id') or data.get('chat_id') or ''),
            ))
            if not decision.should_send:
                logger.info("[Telegram] Engagement policy suppressed send_to_telegram: %s", decision.suppress_reason)
                self._telegram_cleanup_temp_files(
                    Path(audio_path_from_event) if audio_path_from_event and os.path.exists(audio_path_from_event) else None,
                    Path(screenshot_path_from_event) if screenshot_path_from_event and os.path.exists(screenshot_path_from_event) else None,
                    analyzed_image_path,
                )
                return
            if decision.format == 'text' and decision.final_text:
                text = decision.final_text
                data['input_type'] = 'text'
            elif decision.final_voice_text:
                text = decision.final_voice_text

        audio_file = None
        screenshot_file = None
        text_to_send = None

        # Pre-existing audio from event
        if audio_path_from_event and os.path.exists(audio_path_from_event):
            audio_file = Path(audio_path_from_event)
            logger.info(f"[Telegram] 🎵 Using audio_path from event: {audio_path_from_event}")
            try:
                if hasattr(self, 'telegram_manager') and self.telegram_manager:
                    self.telegram_manager._start_typing_loop("record_voice")
            except Exception:
                pass

        # Pre-existing screenshot from event
        if screenshot_path_from_event and os.path.exists(screenshot_path_from_event):
            screenshot_file = Path(screenshot_path_from_event)
            logger.info(f"[Telegram] 📸 Using screenshot_path from event (tool file): {screenshot_path_from_event}")

        if is_done:
            text_to_send, audio_file, screenshot_file = self._telegram_prepare_done_response(
                data, text, audio_file, screenshot_file, audio_path_from_event,
                screenshot_path_from_event, analyzed_image_path,
            )
        else:
            text_to_send, audio_file, screenshot_file = self._telegram_prepare_llm_response(
                data, text, audio_file, screenshot_file, audio_path_from_event,
                screenshot_path_from_event, analyzed_image_path,
            )

        # Send to Telegram
        if hasattr(self, 'telegram_manager'):
            # Convert WAV to OGG Opus for Telegram (native voice note format, ~95% smaller)
            if audio_file and audio_file.exists() and str(audio_file).endswith('.wav'):
                audio_file = self._convert_wav_to_ogg(audio_file)

            if audio_file:
                logger.info(f"[Telegram] 🎵 Sending with audio_file: {audio_file} (exists: {audio_file.exists() if audio_file else False})")
            if screenshot_file:
                logger.info(f"[Telegram] 📤 Sending with screenshot_file: {screenshot_file} (exists: {screenshot_file.exists() if screenshot_file else False})")
            if not audio_file and not screenshot_file:
                logger.debug("[Telegram] No audio_file or screenshot_file; sending text-only response")

            if text_to_send and self._try_route_integration_text_reply(text_to_send, data):
                time.sleep(2)
                self._telegram_cleanup_temp_files(audio_file, screenshot_file, analyzed_image_path)
                return

            if not text_to_send and not audio_file and not screenshot_file:
                logger.info("[Telegram] Nothing to send after response preparation; skipping empty Telegram payload")
                time.sleep(2)
                self._telegram_cleanup_temp_files(audio_file, screenshot_file, analyzed_image_path)
                return

            self.telegram_manager.send_to_telegram(
                text=text_to_send,
                audio_file_path=str(audio_file) if audio_file and audio_file.exists() else None,
                screenshot_path=str(screenshot_file) if screenshot_file and screenshot_file.exists() else None,
            )

        # Cleanup temp files
        time.sleep(2)
        self._telegram_cleanup_temp_files(audio_file, screenshot_file, analyzed_image_path)

    @staticmethod
    def _telegram_engagement_state_fingerprint(text: str) -> str:
        import hashlib
        import re

        clean = re.sub(r"\s+", " ", str(text or "").strip().lower())
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()

    # ---- "Done" response preparation ----

    def _telegram_prepare_done_response(self, data, text, audio_file, screenshot_file,
                                         audio_path_from_event, screenshot_path_from_event,
                                         analyzed_image_path):
        """Prepare text + screenshot for a 'Done' / tool-completion response."""
        # Skip screenshot if explicitly requested (e.g. speak_on_desktop tool)
        explicit_artifact_intent = bool(
            data.get('explicit_artifact_intent')
            or data.get('explicit_user_request')
            or data.get('allow_artifacts')
        )
        skip_screenshot = data.get('skip_screenshot', False) or not explicit_artifact_intent

        # Voice notes don't need screenshots
        if audio_path_from_event and os.path.exists(audio_path_from_event):
            logger.info("[Telegram] 🎵 Voice note detected - skipping screenshot")
            screenshot_file = None
            skip_screenshot = True

        # Check if file was already sent
        file_already_sent = self._telegram_check_file_already_sent(screenshot_path_from_event)
        if file_already_sent:
            logger.info("[Telegram] ⏭️ Skipping 'Done' message - file already sent")
            return None, None, None  # signals caller to return early

        # Skip screenshot for voice notes
        if audio_path_from_event and os.path.exists(audio_path_from_event):
            screenshot_file = None

        text_to_send = text

        # Resolve screenshot (skip if flagged)
        if skip_screenshot:
            screenshot_file = None
        elif not screenshot_file and screenshot_path_from_event and os.path.exists(screenshot_path_from_event):
            screenshot_file = Path(screenshot_path_from_event)
        elif not screenshot_file and analyzed_image_path and os.path.exists(analyzed_image_path):
            screenshot_file = Path(analyzed_image_path)
        elif not screenshot_file and explicit_artifact_intent:
            screenshot_file = self._telegram_capture_screenshot()

        # Fallback screenshot for Done messages (skip if flagged)
        if not skip_screenshot and (not screenshot_file or not screenshot_file.exists()):
            logger.warning("[Telegram] ⚠️ 'Done' message but no screenshot - attempting fallback capture")
            screenshot_file = self._telegram_capture_screenshot_fallback()

        # Draw cursor marker (skip for generated images)
        if screenshot_file and screenshot_file.exists():
            self._telegram_draw_cursor_if_needed(screenshot_file)

        return text_to_send, audio_file, screenshot_file

    # ---- LLM response preparation ----

    def _telegram_prepare_llm_response(self, data, text, audio_file, screenshot_file,
                                        audio_path_from_event, screenshot_path_from_event,
                                        analyzed_image_path):
        """Prepare audio (voice note) or text for an LLM response."""
        # Read input_type from the event data dict (set by message handler)
        input_type = data.get('input_type', 'voice')

        if data.get('is_voice_note') and audio_file and audio_file.exists():
            logger.info("[Telegram] 🎵 Explicit voice-note event - preserving prebuilt audio")
            return None, audio_file, None

        # Load response format settings from the Settings_Store
        try:
            settings = load_settings_from_db()
            text_only_override, auto_match_mode = load_response_format_settings(settings)
        except Exception:
            # Fallback: auto_match_mode enabled (match input format)
            text_only_override, auto_match_mode = False, True

        response_format = determine_response_format(input_type, text_only_override, auto_match_mode)
        wants_text_response = response_format == "text"

        if wants_text_response:
            logger.info(f"[Telegram] 📝 Sending LLM response as TEXT (input_type={input_type}, text_only_override={text_only_override}, auto_match={auto_match_mode})")
            text_to_send = text
            audio_file = None
        else:
            logger.info(f"[Telegram] 🎤 Sending LLM response: audio only (input_type={input_type}, text_only_override={text_only_override}, auto_match={auto_match_mode})")
            text_to_send = None

        explicit_artifact_intent = bool(
            data.get('explicit_artifact_intent')
            or data.get('explicit_user_request')
            or data.get('allow_artifacts')
        )

        # Include analyzed image if available and explicitly requested
        if explicit_artifact_intent and not screenshot_file and analyzed_image_path and os.path.exists(analyzed_image_path):
            screenshot_file = Path(analyzed_image_path)
            logger.info(f"[Telegram] 📸 Including analyzed image with LLM response: {analyzed_image_path}")
            self._telegram_draw_cursor_if_needed(screenshot_file)
        elif explicit_artifact_intent and not screenshot_file and screenshot_path_from_event and os.path.exists(screenshot_path_from_event):
            screenshot_file = Path(screenshot_path_from_event)
        elif explicit_artifact_intent and not screenshot_file:
            # Fallback: capture a screenshot only when the agent performed a visual action
            # but no screenshot was stored (e.g. mouse_movement + smart_open without screenshot_analyzer)
            _ACTION_INDICATORS = (
                'moved', 'clicked', 'opened', 'double-clicked', 'right-clicked',
                'scrolled', 'dragged', 'typed', 'navigated', 'switched',
                'launched', 'closed', 'minimized', 'maximized',
            )
            text_lower = (text or '').lower()
            if any(w in text_lower for w in _ACTION_INDICATORS):
                fallback = self._telegram_capture_screenshot()
                if fallback and fallback.exists():
                    screenshot_file = fallback
                    logger.info(f"[Telegram] 📸 Attached action-result screenshot: {screenshot_file}")
                else:
                    logger.debug(f"[Telegram] No screenshot for action response (fallback capture failed)")
            else:
                logger.debug(f"[Telegram] 📝 Text-only response (no visual action detected)")

        # Check if file already sent — skip TTS generation
        file_already_sent = self._telegram_check_file_already_sent_full(screenshot_path_from_event)
        if file_already_sent:
            logger.info("[Telegram] ⏭️ File already sent - skipping TTS audio generation")
            audio_file = None
        elif text and text.strip() and not wants_text_response and audio_file is None:
            # Generate TTS audio
            try:
                if hasattr(self, 'telegram_manager') and self.telegram_manager:
                    self.telegram_manager._start_typing_loop("record_voice")
            except Exception:
                pass
            audio_file = self._telegram_generate_tts(text)
        else:
            if not text or not text.strip():
                logger.warning(f"[Telegram] ⚠️ No text provided for TTS generation")

        # Fallback: if voice mode produced no audio, send text instead of nothing.
        if not wants_text_response and audio_file is None and text and text.strip():
            logger.info("[Telegram] 🔄 Voice mode but no audio generated — falling back to text response")
            text_to_send = text

        return text_to_send, audio_file, screenshot_file

    # ---- Helpers ----

    def _telegram_check_file_already_sent(self, screenshot_path_from_event):
        """Check if a file was already sent to Telegram (for Done messages)."""
        file_already_sent = (screenshot_path_from_event and os.path.exists(screenshot_path_from_event))
        if not file_already_sent:
            try:
                from PyQt6.QtWidgets import QApplication
                app = QApplication.instance()
                if app and hasattr(app, 'agent_session') and app.agent_session:
                    if hasattr(app.agent_session, 'llm_service') and app.agent_session.llm_service:
                        if hasattr(app.agent_session.llm_service, '_tts_service') and app.agent_session.llm_service._tts_service:
                            if hasattr(app.agent_session.llm_service._tts_service, '_telegram_file_sent'):
                                file_already_sent = app.agent_session.llm_service._tts_service._telegram_file_sent
            except Exception:
                pass
        return file_already_sent

    def _telegram_check_file_already_sent_full(self, screenshot_path_from_event):
        """Full check for file-already-sent (thread-local + TTS service + event path)."""
        file_from_tool = screenshot_path_from_event and os.path.exists(screenshot_path_from_event)
        thread_file_sent = hasattr(threading.current_thread(), 'telegram_file_sent') and threading.current_thread().telegram_file_sent
        tts_file_sent = False
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app and hasattr(app, 'agent_session') and app.agent_session:
                if hasattr(app.agent_session, 'llm_service') and app.agent_session.llm_service:
                    if hasattr(app.agent_session.llm_service, '_tts_service') and app.agent_session.llm_service._tts_service:
                        tts_file_sent = getattr(app.agent_session.llm_service._tts_service, '_telegram_file_sent', False)
        except Exception:
            pass
        return file_from_tool or thread_file_sent or tts_file_sent

    def _telegram_capture_screenshot(self):
        """Capture a screenshot of the screen where the mouse is."""
        try:
            from distr.core.agent.tools.vision.screenshot_analyzer import get_current_mouse_screen, capture_screenshot
            from PyQt6.QtWidgets import QApplication
            import platform
            import subprocess

            temp_dir = Path(tempfile.gettempdir()) / "decisions_ai_telegram"
            temp_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            screenshot_file = temp_dir / f"telegram_screenshot_{timestamp}.png"

            screen = get_current_mouse_screen()
            if screen:
                try:
                    if platform.system() == "Darwin":
                        screen_geo = screen.geometry()
                        result = subprocess.run(
                            ['screencapture', '-R',
                             f"{screen_geo.left()},{screen_geo.top()},{screen_geo.width()},{screen_geo.height()}",
                             str(screenshot_file)],
                            capture_output=True, timeout=10,
                        )
                        if result.returncode == 0 and screenshot_file.exists():
                            logger.info(f"Captured screenshot: {screenshot_file} ({os.path.getsize(screenshot_file)} bytes)")
                            return screenshot_file
                        raise Exception("screencapture failed")
                    else:
                        pixmap = screen.grabWindow(0)
                        if pixmap and not pixmap.isNull() and pixmap.save(str(screenshot_file), 'PNG'):
                            logger.info(f"Captured screenshot: {screenshot_file} ({os.path.getsize(screenshot_file)} bytes)")
                            return screenshot_file
                        raise Exception("Failed to grab window")
                except Exception as e:
                    logger.warning(f"Failed to capture specific screen: {e}, falling back")
                    if capture_screenshot(str(screenshot_file), "full"):
                        return screenshot_file
            else:
                if capture_screenshot(str(screenshot_file), "full"):
                    return screenshot_file
        except Exception as e:
            logger.error(f"Failed to capture screenshot: {e}", exc_info=True)
        return None

    def _telegram_capture_screenshot_fallback(self):
        """Last-resort screenshot capture."""
        try:
            from distr.core.agent.tools.vision.screenshot_analyzer import capture_screenshot
            temp_dir = Path(tempfile.gettempdir()) / "decisions_ai_telegram"
            temp_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            screenshot_file = temp_dir / f"telegram_screenshot_fallback_{timestamp}.png"
            if capture_screenshot(str(screenshot_file), "current_mouse_screen"):
                logger.info(f"[Telegram] ✅ Fallback screenshot captured: {screenshot_file}")
                return screenshot_file
            logger.error("[Telegram] ❌ Fallback screenshot capture also failed")
        except Exception as e:
            logger.error(f"[Telegram] ❌ Fallback screenshot capture error: {e}", exc_info=True)
        return None

    def _telegram_draw_cursor_if_needed(self, screenshot_file):
        """Draw cursor marker on screenshot unless it's a generated image."""
        is_generated = False
        try:
            from distr.core.agent.tools.vision.image_generator import ImageGeneratorTool
            if ImageGeneratorTool._last_generated_image and str(screenshot_file) == ImageGeneratorTool._last_generated_image:
                is_generated = True
        except Exception:
            pass
        if not is_generated and "generated_image" in str(screenshot_file):
            is_generated = True
        if is_generated:
            logger.info(f"[Telegram] 🎨 Skipping cursor marker for generated image: {screenshot_file}")
            return
        try:
            self._telegram_draw_cursor_marker(str(screenshot_file))
        except Exception as e:
            logger.warning(f"Failed to draw cursor marker: {e}")

    def _telegram_draw_cursor_marker(self, image_path: str) -> str:
        """Draw cursor.png at the current cursor position on the screenshot."""
        try:
            import pyautogui
        except ImportError:
            return image_path
        try:
            from PIL import Image
            from distr.core.paths import IMAGES_DIR

            cursor_img_path = os.path.join(IMAGES_DIR, "cursor.png")
            if not os.path.exists(cursor_img_path):
                return image_path

            cursor_x, cursor_y = pyautogui.position()

            from distr.core.agent.tools.vision.screenshot_analyzer import get_current_mouse_screen
            screen = get_current_mouse_screen()

            img = Image.open(image_path)
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            img_width, img_height = img.size

            scale_factor = 1.0
            if screen:
                geo = screen.geometry()
                rel_x = cursor_x - geo.left()
                rel_y = cursor_y - geo.top()
                if img_width != geo.width() or img_height != geo.height():
                    sx = img_width / geo.width()
                    sy = img_height / geo.height()
                    scale_factor = (sx + sy) / 2.0
                    rel_x = int(rel_x * sx)
                    rel_y = int(rel_y * sy)
            else:
                rel_x, rel_y = cursor_x, cursor_y

            rel_x = max(0, min(rel_x, img_width - 1))
            rel_y = max(0, min(rel_y, img_height - 1))

            cursor_img = Image.open(cursor_img_path)
            if cursor_img.mode != 'RGBA':
                cursor_img = cursor_img.convert('RGBA')
            if scale_factor != 1.0:
                cursor_img = cursor_img.resize(
                    (int(cursor_img.width * scale_factor), int(cursor_img.height * scale_factor)),
                    Image.Resampling.LANCZOS,
                )

            img.paste(cursor_img, (rel_x, rel_y), cursor_img)

            if image_path.lower().endswith('.png'):
                img.save(image_path)
            else:
                rgb = Image.new('RGB', img.size, (255, 255, 255))
                rgb.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else None)
                rgb.save(image_path)

            logger.info(f"[Telegram] 🎯 Drew cursor marker at ({rel_x}, {rel_y}) on {image_path}")
            return image_path
        except Exception as e:
            logger.warning(f"Failed to draw cursor marker: {e}")
            return image_path

    def _telegram_resolve_voice_settings(self, settings: dict):
        """Resolve TTS provider and voice model from the current chat, falling back to global settings.

        Returns (tts_provider, voice_id) where tts_provider is the canonical
        settings-style string (e.g. 'Kokoro (Offline)') and voice_id is the
        voice/speaker identifier for that provider.
        """
        from distr.core.agent.constants import normalize_voice_provider
        from distr.core.agent.services.tts.registry import tts_registry

        chat_voice_provider = ""
        chat_voice_model = ""

        try:
            from distr.core.db import get_session, Chat
            chat_id = settings.get('agent_current_chat_id')
            if chat_id:
                with get_session() as session:
                    chat = session.query(Chat).filter(Chat.id == int(chat_id)).first()
                    if chat:
                        # Walk to root chat to get the thread-level voice settings
                        root = chat
                        while root.parent_id:
                            parent = session.query(Chat).filter(Chat.id == root.parent_id).first()
                            if not parent:
                                break
                            root = parent
                        chat_voice_provider = (root.voice_provider or "").strip()
                        chat_voice_model = (root.voice_model or "").strip()
        except Exception as e:
            logger.debug(f"Could not load chat voice settings: {e}")

        # Resolve provider: chat → global settings
        tts_provider = chat_voice_provider or settings.get('tts_provider', 'Kokoro (Offline)')

        # Resolve voice model: chat → provider-specific global setting
        if chat_voice_model:
            voice_id = chat_voice_model
        else:
            provider_id = normalize_voice_provider(tts_provider)
            try:
                descriptor = tts_registry.get(provider_id)
                voice_id = descriptor.get_telegram_voice_id(settings)
            except KeyError:
                voice_id = ''

        return tts_provider, voice_id

    def _telegram_generate_tts(self, text: str):
        """Generate TTS audio file for Telegram voice note."""
        try:
            from distr.core.agent.constants import normalize_voice_provider
            from distr.core.agent.services.tts.registry import tts_registry

            settings = load_settings_from_db()
            tts_provider, voice_id = self._telegram_resolve_voice_settings(settings)
            temp_dir = Path(tempfile.gettempdir()) / "decisions_ai_telegram"
            temp_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

            provider_id = normalize_voice_provider(tts_provider)

            try:
                descriptor = tts_registry.get(provider_id)
            except KeyError:
                logger.warning(f"Unknown TTS provider: {tts_provider}")
                return None

            audio_file = temp_dir / f"telegram_tts_{timestamp}.wav"
            try:
                descriptor.generate_audio(text, voice_id or descriptor.default_voice, 1.0, str(audio_file))
                logger.info(f"Generated {provider_id} TTS audio: {audio_file} ({os.path.getsize(audio_file)} bytes), voice={voice_id}")
                return audio_file
            except ImportError:
                logger.warning(f"{provider_id} TTS library not available")
            except ValueError as e:
                logger.warning("%s TTS unavailable for Telegram voice note: %s", provider_id, e)
            except Exception as e:
                logger.error(f"Failed to generate {provider_id} TTS audio: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Failed to generate TTS audio: {e}", exc_info=True)
        return None

    @staticmethod
    def _convert_wav_to_ogg(wav_path):
        """Convert a WAV file to OGG Opus for Telegram voice notes.

        Returns the Path to the OGG file, or the original WAV path on failure.
        """
        from pathlib import Path
        ogg_path = Path(str(wav_path).rsplit('.', 1)[0] + '.ogg')
        try:
            import subprocess
            result = subprocess.run(
                ['ffmpeg', '-y', '-i', str(wav_path), '-c:a', 'libopus', '-b:a', '64k', str(ogg_path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and ogg_path.exists():
                wav_size = wav_path.stat().st_size
                ogg_size = ogg_path.stat().st_size
                logger.info(f"[Telegram] Converted WAV→OGG Opus: {wav_size:,} → {ogg_size:,} bytes ({ogg_size * 100 // wav_size}%)")
                return ogg_path
            logger.warning(f"[Telegram] ffmpeg WAV→OGG failed: {result.stderr[:200]}")
        except FileNotFoundError:
            logger.debug("[Telegram] ffmpeg not found — sending WAV as-is")
        except Exception as e:
            logger.warning(f"[Telegram] WAV→OGG conversion failed: {e}")
        return wav_path

    def _telegram_cleanup_temp_files(self, audio_file, screenshot_file, analyzed_image_path):
        """Clean up temporary files after sending to Telegram."""
        try:
            if audio_file and audio_file.exists():
                audio_file.unlink()
            if screenshot_file and screenshot_file.exists():
                if "decisions_ai_telegram" in str(screenshot_file) or "temp" in str(screenshot_file).lower():
                    screenshot_file.unlink()
            if analyzed_image_path and os.path.exists(analyzed_image_path):
                if "decisions_ai_telegram" in str(analyzed_image_path) or "decisions_ai_telegram_analyzed" in str(analyzed_image_path):
                    os.unlink(analyzed_image_path)
        except Exception as e:
            logger.warning(f"Failed to clean up temp files: {e}")

    # ------------------------------------------------------------------
    # send_file_to_telegram
    # ------------------------------------------------------------------

    def _evt_send_file_to_telegram(self, data):
        file_path = data.get('file_path')
        file_name = data.get('file_name', '')
        file_type = data.get('file_type', 'document')

        logger.info(f"[EVENT QUEUE] Received send_file_to_telegram event: file_path={file_path}, file_type={file_type}")

        has_tm = hasattr(self, 'telegram_manager')
        connected = has_tm and self.telegram_manager.is_connected() if has_tm else False

        explicit_artifact_intent = bool(
            data.get('explicit_artifact_intent')
            or data.get('explicit_user_request')
            or data.get('allow_artifacts')
        )

        if has_tm and file_path:
            try:
                if not os.path.exists(file_path) or os.path.getsize(file_path) <= 0:
                    logger.warning("[EVENT QUEUE] Skipping empty or missing Telegram file: %s", file_path)
                    return
            except OSError:
                logger.warning("[EVENT QUEUE] Skipping unreadable Telegram file: %s", file_path)
                return
            if not explicit_artifact_intent:
                logger.info("[EVENT QUEUE] Skipping unsolicited Telegram file: %s", file_path)
                return
            if not connected:
                logger.warning("[EVENT QUEUE] Telegram not connected; queuing outbound file for reconnect")
            type_text = {
                'image': 'image',
                'audio': 'audio file',
                'video': 'video',
                'document': 'document',
            }.get(file_type, 'file')
            caption = f"Sending {type_text}: {file_name}"
            decision = HumanEngagementService(
                telegram_manager=self.telegram_manager,
                allow_telegram=True,
            ).decide(EngagementIntent(
                source="app_event",
                surface="telegram",
                kind="artifact_delivery",
                priority="normal",
                subject_type="file",
                subject_id=str(file_path),
                state_fingerprint=str(os.path.getmtime(file_path)),
                body=caption,
                attachments=[EngagementAttachment(path=file_path, kind=file_type, name=file_name)],
                explicit_artifact_intent=True,
            ))
            if not decision.should_send:
                logger.info("[EVENT QUEUE] Engagement policy suppressed Telegram file: %s", decision.suppress_reason)
                return
            caption = decision.final_text or caption

            if file_type == 'image':
                self.telegram_manager.send_to_telegram(text=caption, screenshot_path=file_path)
            elif file_type == 'audio':
                self.telegram_manager.send_to_telegram(text=caption, audio_file_path=file_path)
            elif file_type == 'video':
                self.telegram_manager.send_to_telegram(text=caption, video_path=file_path)
            else:
                self.telegram_manager.send_to_telegram(text=caption, document_path=file_path)
            logger.info(f"[EVENT QUEUE] ✅ Sent {file_type} to Telegram: {file_name}")
        else:
            logger.warning(f"[EVENT QUEUE] ⚠️ Cannot send file - has_manager={has_tm}, connected={connected}, file_path={file_path}")

    # ------------------------------------------------------------------
    # Mouse screen info
    # ------------------------------------------------------------------

    def _evt_get_mouse_screen(self):
        logger.info("[EVENT QUEUE] Received get_current_mouse_screen request from agent")
        screen_info = self.get_current_mouse_screen_info()
        if screen_info:
            try:
                self.agent_event_queue.put(('current_mouse_screen_response', screen_info), block=False)
            except Exception as e:
                logger.error(f"[EVENT QUEUE] Failed to send screen info response: {e}")
        else:
            logger.warning("[EVENT QUEUE] Could not get current mouse screen info")

    # ------------------------------------------------------------------
    # File operation confirmation
    # ------------------------------------------------------------------

    def _evt_file_operation_confirmation(self, data):
        confirmation_id = data.get('confirmation_id')
        plan = data.get('plan')
        operation_type = data.get('operation_type', 'UNKNOWN')
        source_path = data.get('source_path', '')

        logger.info(f"[EVENT QUEUE] File operation confirmation: {operation_type} on {source_path} (ID: {confirmation_id})")

        if not (plan and confirmation_id):
            logger.warning("[EVENT QUEUE] Invalid file operation confirmation request: missing plan or confirmation_id")
            return

        agent_command_queue_ref = getattr(self, 'agent_command_queue', None)
        confirmation_results_dict_ref = getattr(self, 'confirmation_results_dict', None)

        def show_confirmation_dialog():
            confirmed = False
            command_queue_sent = False
            try:
                from distr.gui.dialogs.file_operation import confirm_file_operations_with_plan
                try:
                    confirmed = confirm_file_operations_with_plan(
                        plan,
                        require_confirmation_phrase=True,
                        confirmation_phrase="confirm file changes",
                        parent_window=getattr(self, 'oracle_window', None),
                    )
                    logger.info(f"[EVENT QUEUE] ✅ File operation confirmation result: {confirmed} (ID: {confirmation_id})")
                except (KeyboardInterrupt, SystemExit):
                    confirmed = False
                    raise
                except Exception as e:
                    logger.error(f"[EVENT QUEUE] ❌ Error in confirmation dialog: {e}", exc_info=True)
                    confirmed = False
            except Exception as e:
                logger.error(f"[EVENT QUEUE] ❌ Error importing confirmation dialog: {e}", exc_info=True)
                confirmed = False

            # Send via command queue (primary)
            for attempt in range(3):
                try:
                    if agent_command_queue_ref:
                        agent_command_queue_ref.put(('file_operation_confirmation_response', {
                            'confirmation_id': confirmation_id, 'confirmed': confirmed,
                        }), block=False)
                        command_queue_sent = True
                        logger.info(f"[EVENT QUEUE] ✅ Sent confirmation via command_queue: confirmed={confirmed} (ID: {confirmation_id})")
                        break
                except Exception as e:
                    if attempt == 2:
                        logger.error(f"[EVENT QUEUE] ❌ Failed to send confirmation after 3 attempts: {e}")
                    else:
                        time.sleep(0.1)

            # Also store in shared dict (secondary)
            try:
                if confirmation_results_dict_ref is not None:
                    confirmation_results_dict_ref[confirmation_id] = {
                        'confirmed': confirmed, 'dont_show_again': False,
                    }
            except (BrokenPipeError, OSError, ConnectionError) as e:
                logger.warning(f"[EVENT QUEUE] Manager connection broken: {e}")
            except Exception as e:
                logger.warning(f"[EVENT QUEUE] Failed to store in confirmation_results_dict: {e}")

            if not command_queue_sent:
                logger.error("[EVENT QUEUE] CRITICAL: Failed to send confirmation via both mechanisms!")

        def safe_show():
            try:
                show_confirmation_dialog()
            except (SystemExit, KeyboardInterrupt):
                raise
            except BaseException as e:
                logger.critical(f"[EVENT QUEUE] OUTER SAFETY: {e}", exc_info=True)
                try:
                    if agent_command_queue_ref:
                        agent_command_queue_ref.put(('file_operation_confirmation_response', {
                            'confirmation_id': confirmation_id, 'confirmed': False,
                        }), block=False)
                except Exception:
                    pass

        try:
            QTimer.singleShot(10, safe_show)
        except Exception as e:
            logger.critical(f"[EVENT QUEUE] ❌ Failed to schedule dialog: {e}", exc_info=True)
            try:
                if agent_command_queue_ref:
                    agent_command_queue_ref.put(('file_operation_confirmation_response', {
                        'confirmation_id': confirmation_id, 'confirmed': False,
                    }), block=False)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Rename preview
    # ------------------------------------------------------------------

    def _evt_rename_preview(self, data):
        renames = data.get('renames', [])
        confirmation_id = data.get('confirmation_id')

        if not (renames and confirmation_id):
            logger.warning("[EVENT QUEUE] Invalid rename preview request: missing renames or confirmation_id")
            return

        agent_command_queue_ref = getattr(self, 'agent_command_queue', None)
        confirmation_results_dict_ref = getattr(self, 'confirmation_results_dict', None)

        def show_rename_preview_dialog():
            confirmed = False
            try:
                from distr.gui.dialogs.rename_preview import show_rename_preview
                confirmed = show_rename_preview(
                    renames,
                    parent=getattr(self, 'oracle_window', None),
                    title=f"Preview {len(renames)} Rename(s)",
                )
                logger.info(f"[EVENT QUEUE] Rename preview result: {confirmed}")
            except Exception as e:
                logger.error(f"[EVENT QUEUE] Error showing rename preview dialog: {e}", exc_info=True)

            try:
                if agent_command_queue_ref:
                    agent_command_queue_ref.put(('rename_preview_response', {
                        'confirmation_id': confirmation_id, 'confirmed': confirmed,
                    }), block=False)
            except Exception as e:
                logger.error(f"[EVENT QUEUE] Failed to send rename preview result: {e}")

            try:
                if confirmation_results_dict_ref is not None:
                    confirmation_results_dict_ref[confirmation_id] = {'confirmed': confirmed}
            except Exception as e:
                logger.warning(f"[EVENT QUEUE] Failed to store in confirmation_results_dict: {e}")

        QTimer.singleShot(10, show_rename_preview_dialog)

    # ------------------------------------------------------------------
    # Quota exceeded fallback
    # ------------------------------------------------------------------

    def _handle_quota_exceeded_fallback(self):
        """Handle ElevenLabs quota exceeded by switching to Kokoro and speaking error message."""
        logger.info("[QUOTA FALLBACK] Starting automatic fallback to Kokoro")
        try:
            current_settings = load_settings_from_db()
            current_settings['tts_provider'] = 'Kokoro (Offline)'
            save_settings_to_db(current_settings)
            self.settings = current_settings
            self._send_command_to_agent('hot_swap_tts', {
                'voice_provider': 'Kokoro (Offline)',
                'voice_model': current_settings.get('kokoro_voice', ''),
            })
            error_message = "You've exceeded your quota on ElevenLabs. I've automatically switched to Kokoro."
            QTimer.singleShot(500, lambda: self._speak_quota_error_message(error_message))
        except Exception as e:
            logger.error(f"[QUOTA FALLBACK] Error during fallback: {e}", exc_info=True)

    def _speak_quota_error_message(self, message: str):
        """Speak the quota error message using the current TTS provider."""
        try:
            self._send_command_to_agent('speak_text_directly', {'text': message})
        except Exception as e:
            logger.error(f"[QUOTA FALLBACK] Error speaking message: {e}", exc_info=True)
