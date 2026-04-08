"""EventHookDispatcher — maps application signals to Event_Hook firings.

Connects to existing signals from signal_manager and translates them into
event hook state changes driven by the active SkinConfig.

Requirements: 5.1, 5.9, 5.10, 5.11, 5.12, 7.1-7.8
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from distr.core.skin_config import EventResponse, SkinConfig

logger = logging.getLogger(__name__)

# Hooks that are temporary — they auto-revert to the previous state
TEMPORARY_HOOKS = {"file_drop_success", "tts_response"}


class EventHookDispatcher(QObject):
    """Dispatches event hooks based on application signals and the active skin config.

    Maintains a state stack so temporary events (file_drop_success, tts_response)
    can revert to the previous state when they complete.
    """

    event_hook_fired = pyqtSignal(str, str)  # (new_hook, previous_hook)

    def __init__(self, signal_manager: Optional[QObject] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._signal_manager = signal_manager
        self._config: Optional[SkinConfig] = None
        self._current_hook: str = "idle"
        self._previous_hook: str = "idle"
        self._connected = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_skin_config(self, config: SkinConfig) -> None:
        """Set the active skin configuration."""
        self._config = config

    def get_current_hook(self) -> str:
        """Return the name of the currently active event hook."""
        return self._current_hook

    def get_previous_hook(self) -> str:
        """Return the name of the previous event hook."""
        return self._previous_hook

    def get_event_response(self, hook: str) -> Optional[EventResponse]:
        """Look up the Event_Response for *hook* from the active config.

        Returns ``None`` if the config is not set or the hook is not defined.
        """
        if self._config is None:
            return None
        return self._config.events.get(hook)

    def get_transition(self, from_hook: str, to_hook: str) -> Optional[str]:
        """Look up the transition animation filename for a state change.

        Returns the animation filename if the key ``"{from_hook}-{to_hook}"``
        exists in the config's transitions map, or ``None`` otherwise.
        """
        if self._config is None:
            return None
        key = f"{from_hook}-{to_hook}"
        return self._config.transitions.get(key)

    # Hooks that should not be overridden by background signals
    _USER_PRIORITY_HOOKS = {"ptt_active", "hands_free_listening", "dictation"}

    def fire_hook(self, hook: str) -> None:
        """Fire an event hook.

        User-priority hooks (PTT, hands-free, dictation) cannot be overridden
        by background signals — only explicit revert_hook can end them.
        """
        old_hook = self._current_hook

        # Don't let background hooks override user-initiated states
        if old_hook in self._USER_PRIORITY_HOOKS and hook not in self._USER_PRIORITY_HOOKS:
            logger.info("[Dispatcher] BLOCKED '%s' — priority hook '%s' is active", hook, old_hook)
            return

        if hook in TEMPORARY_HOOKS:
            self._previous_hook = self._current_hook
        else:
            self._previous_hook = self._current_hook
        self._current_hook = hook
        logger.info("[Dispatcher] fire_hook: %s → %s (prev stored: %s)", old_hook, hook, self._previous_hook)
        self.event_hook_fired.emit(hook, old_hook)

    def revert_hook(self, hook: str) -> None:
        """Revert from a hook back to the previous state."""
        if self._current_hook != hook:
            logger.info("[Dispatcher] revert_hook('%s') skipped — current is '%s'", hook, self._current_hook)
            return
        logger.info("[Dispatcher] revert_hook: %s → %s", self._current_hook, self._previous_hook)
        self._do_revert()

    def force_revert(self) -> None:
        """Force revert to previous state regardless of current hook."""
        logger.info("[Dispatcher] force_revert: %s → %s", self._current_hook, self._previous_hook)
        self._do_revert()

    def _do_revert(self) -> None:
        old_hook = self._current_hook
        self._current_hook = self._previous_hook
        self._previous_hook = old_hook
        self.event_hook_fired.emit(self._current_hook, old_hook)

    # ------------------------------------------------------------------
    # Signal connections (lazy — called once when signal_manager is set)
    # ------------------------------------------------------------------

    def connect_signals(self) -> None:
        """Connect to application signals that the window doesn't handle directly.

        Signals that the OracleWindow handles manually (PTT, hands-free, dictation)
        are NOT connected here to avoid double-firing.
        """
        sm = self._signal_manager
        if sm is None or self._connected:
            return
        self._connected = True

        # Only connect signals that aren't manually managed by OracleWindow
        _safe_connect(sm, "action_recording_started", lambda _=None: self.fire_hook("recording_action"))
        _safe_connect(sm, "chat_stream_started", lambda _=None: self.fire_hook("thinking"))
        _safe_connect(sm, "step_runner_run_all_requested", lambda *_: self.fire_hook("running_step_runner"))
        _safe_connect(sm, "action_recording_stopped", lambda _=None: self.revert_hook("recording_action"))
        _safe_connect(sm, "chat_stream_finished", lambda _=None: self.fire_hook("idle"))
        _safe_connect(sm, "action_playback_finished", lambda: self.revert_hook("running_step_runner"))
        _safe_connect(sm, "action_playback_stopped", lambda _=None: self.revert_hook("running_step_runner"))

        logger.debug("EventHookDispatcher connected to signal_manager signals")


def _safe_connect(signal_manager: QObject, signal_name: str, slot) -> None:
    """Connect to a signal if it exists, otherwise log a warning."""
    sig = getattr(signal_manager, signal_name, None)
    if sig is not None:
        try:
            sig.connect(slot)
        except Exception:
            logger.warning("Failed to connect to signal %s", signal_name, exc_info=True)
    else:
        logger.debug("Signal %s not found on signal_manager — skipping", signal_name)
