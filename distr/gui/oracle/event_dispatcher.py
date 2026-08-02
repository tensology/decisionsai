"""EventHookDispatcher — maps application signals to Event_Hook firings.

Connects to existing signals from signal_manager and translates them into
event hook state changes driven by the active SkinConfig.

Requirements: 5.1, 5.9, 5.10, 5.11, 5.12, 7.1-7.8
"""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

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

    # Maximum time (ms) to stay in 'thinking' before auto-reverting to idle
    THINKING_TIMEOUT_MS = 120_000  # 2 minutes

    @staticmethod
    def _log_avatar_state(
        from_hook: str,
        to_hook: str,
        *,
        trigger: str | None = None,
        blocked: bool = False,
    ) -> None:
        """One-line INFO log for every avatar hook transition (grep: ``[avatar-state]``)."""
        tag = "[avatar-state] BLOCKED" if blocked else "[avatar-state]"
        t = trigger if trigger is not None else "unspecified"
        logger.info("%s %s → %s | trigger=%s", tag, from_hook, to_hook, t)

    def __init__(self, signal_manager: Optional[QObject] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._signal_manager = signal_manager
        self._config: Optional[SkinConfig] = None
        self._current_hook: str = "idle"
        self._previous_hook: str = "idle"
        self._connected = False
        self._thinking_timer = QTimer(self)
        self._thinking_timer.setSingleShot(True)
        self._thinking_timer.timeout.connect(self._on_thinking_timeout)

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
    _USER_PRIORITY_HOOKS = {"ptt_active", "hands_free_listening", "dictation", "ticket_dictation"}

    def fire_hook(self, hook: str, *, trigger: str | None = None) -> None:
        """Fire an event hook.

        User-priority hooks (PTT, hands-free, dictation) cannot be overridden
        by background signals — only explicit revert_hook can end them.
        """
        old_hook = self._current_hook

        # Don't let background hooks override user-initiated states
        if old_hook in self._USER_PRIORITY_HOOKS and hook not in self._USER_PRIORITY_HOOKS:
            self._log_avatar_state(old_hook, hook, trigger=trigger or "fire_hook", blocked=True)
            logger.info("[Dispatcher] detail: priority hook '%s' active", old_hook)
            return

        # Re-emitting the active hook may be useful to refresh its visual, but
        # it must not replace the saved return state with itself. Otherwise a
        # duplicate hands-free-listening event makes disable revert from
        # hands_free_listening straight back to hands_free_listening.
        if hook == old_hook:
            self._log_avatar_state(old_hook, hook, trigger=trigger)
            logger.debug(
                "[Dispatcher] repeated hook preserves previous=%s",
                self._previous_hook,
            )
            self.event_hook_fired.emit(hook, old_hook)
            if hook == "thinking":
                self._thinking_timer.start(self.THINKING_TIMEOUT_MS)
            return

        if hook in TEMPORARY_HOOKS:
            self._previous_hook = self._current_hook
        else:
            self._previous_hook = self._current_hook
        self._current_hook = hook
        self._log_avatar_state(old_hook, hook, trigger=trigger)
        logger.debug(
            "[Dispatcher] fire_hook stack: current=%s stored_previous=%s",
            self._current_hook,
            self._previous_hook,
        )
        self.event_hook_fired.emit(hook, old_hook)

        # Start a safety timeout for 'thinking' to prevent stuck state
        if hook == "thinking":
            self._thinking_timer.start(self.THINKING_TIMEOUT_MS)
        else:
            self._thinking_timer.stop()

    def _on_thinking_timeout(self):
        """Safety timeout — revert stuck 'thinking' state back to idle."""
        if self._current_hook == "thinking":
            logger.warning("[Dispatcher] Thinking timeout (%dms) — forcing idle", self.THINKING_TIMEOUT_MS)
            self._log_avatar_state("thinking", "idle", trigger="thinking_timeout_ms")
            self._current_hook = "idle"
            self._previous_hook = "idle"
            self.event_hook_fired.emit("idle", "thinking")
        self._thinking_timer.stop()

    def revert_hook(self, hook: str, *, trigger: str | None = None) -> None:
        """Revert from a hook back to the previous state."""
        # Always stop the thinking timer on any revert
        self._thinking_timer.stop()

        if self._current_hook != hook:
            logger.info(
                "[avatar-state] revert_hook no-op (want %s) | current=%s | trigger=%s",
                hook,
                self._current_hook,
                trigger if trigger is not None else "unspecified",
            )
            return
        self._do_revert(trigger=trigger)

    def force_revert(self) -> None:
        """Force revert to previous state regardless of current hook."""
        self._do_revert(trigger="force_revert")

    def force_idle(self, reason: str = "") -> None:
        """Force hook state to idle even if a priority hook is active.

        Used as a safety recovery path when external state indicates PTT is no
        longer active but the UI hook was not reverted correctly.
        """
        old_hook = self._current_hook
        if old_hook == "idle":
            return
        self._thinking_timer.stop()
        self._current_hook = "idle"
        self._previous_hook = "idle"
        trig = f"force_idle:{reason}" if reason else "force_idle"
        self._log_avatar_state(old_hook, "idle", trigger=trig)
        if reason:
            logger.warning("[Dispatcher] force_idle detail: %s", reason)
        self.event_hook_fired.emit("idle", old_hook)

    def _on_typing_indicator_changed(self, show: bool) -> None:
        """Typing indicator is authoritative for 'agent is actively generating'.

        When it turns off while the avatar is stuck in 'thinking', recover to
        idle immediately. This avoids cases where chat_stream_* signals are
        skipped during interruption/cancellation.
        """
        if show:
            return
        if self._current_hook != "thinking":
            return

        old_hook = self._current_hook
        self._thinking_timer.stop()
        self._current_hook = "idle"
        self._previous_hook = "idle"
        self._log_avatar_state(old_hook, "idle", trigger="signal:typing_indicator_changed_false")
        self.event_hook_fired.emit("idle", old_hook)

    def _do_revert(self, *, trigger: str | None = None) -> None:
        old_hook = self._current_hook
        new_hook = self._previous_hook
        self._log_avatar_state(old_hook, new_hook, trigger=trigger)
        self._current_hook = new_hook
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
        _safe_connect(
            sm,
            "action_recording_started",
            lambda _=None: self.fire_hook("recording_action", trigger="signal:action_recording_started"),
        )
        _safe_connect(
            sm,
            "chat_stream_started",
            lambda _=None: self.fire_hook("thinking", trigger="signal:chat_stream_started"),
        )
        _safe_connect(
            sm,
            "step_runner_run_all_requested",
            lambda *_: self.fire_hook("running_step_runner", trigger="signal:step_runner_run_all_requested"),
        )
        _safe_connect(
            sm,
            "action_recording_stopped",
            lambda _=None: self.revert_hook("recording_action", trigger="signal:action_recording_stopped"),
        )
        _safe_connect(
            sm,
            "chat_stream_finished",
            lambda _=None: self.revert_hook("thinking", trigger="signal:chat_stream_finished"),
        )
        _safe_connect(
            sm,
            "chat_stream_error",
            lambda _: self.revert_hook("thinking", trigger="signal:chat_stream_error"),
        )
        _safe_connect(
            sm,
            "action_playback_finished",
            lambda: self.revert_hook("running_step_runner", trigger="signal:action_playback_finished"),
        )
        _safe_connect(
            sm,
            "action_playback_stopped",
            lambda _=None: self.revert_hook("running_step_runner", trigger="signal:action_playback_stopped"),
        )
        _safe_connect(
            sm,
            "typing_indicator_changed",
            lambda show: self._on_typing_indicator_changed(show),
        )

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
