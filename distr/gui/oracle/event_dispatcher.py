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

# Visible-state priority. Hooks remain active while hidden by a higher-priority
# hook, and their own completion event can still deactivate them. This is the
# key difference from a single previous-state pointer: out-of-order completion
# cannot resurrect a state that has already ended.
HOOK_PRIORITY = {
    "idle": 0,
    "hands_free_listening": 10,
    "thinking": 20,
    "running_action": 30,
    "running_step_runner": 30,
    "recording_action": 30,
    "needs_attention": 35,
    "snippet_copied": 40,
    "file_drop_success": 40,
    "tts_response": 50,
    "talking": 50,
    "ptt_active": 60,
    "dictation": 60,
    "ticket_dictation": 60,
}


class EventHookDispatcher(QObject):
    """Dispatches event hooks based on application signals and the active skin config.

    Tracks independently active hooks and selects the highest-priority visible
    hook. Completion events deactivate their hook even while another state is
    visible, preventing stale states from returning later.
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
        self._active_hooks: dict[str, int] = {}
        self._activation_sequence = 0
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

    def fire_hook(self, hook: str, *, trigger: str | None = None) -> None:
        """Activate a hook and display the highest-priority active state."""
        old_hook = self._current_hook

        if hook == "idle":
            self._active_hooks.clear()
            self._thinking_timer.stop()
            self._apply_visible_hook("idle", trigger=trigger, refresh=(old_hook == "idle"))
            return

        self._activation_sequence += 1
        self._active_hooks[hook] = self._activation_sequence
        new_hook = self._select_visible_hook()

        if new_hook == old_hook and hook == old_hook:
            self._log_avatar_state(old_hook, hook, trigger=trigger)
            logger.debug(
                "[Dispatcher] repeated hook active=%s",
                sorted(self._active_hooks),
            )
            self.event_hook_fired.emit(hook, old_hook)
        elif new_hook != old_hook:
            self._apply_visible_hook(new_hook, trigger=trigger)
        else:
            self._log_avatar_state(old_hook, hook, trigger=trigger or "fire_hook", blocked=True)
            logger.info(
                "[Dispatcher] hook '%s' active but hidden by '%s'",
                hook,
                old_hook,
            )

        if hook == "thinking":
            self._thinking_timer.start(self.THINKING_TIMEOUT_MS)

    def _on_thinking_timeout(self):
        """Safety timeout: deactivate thinking even when it is hidden."""
        if "thinking" in self._active_hooks:
            logger.warning("[Dispatcher] Thinking timeout (%dms) — forcing idle", self.THINKING_TIMEOUT_MS)
            self.revert_hook("thinking", trigger="thinking_timeout_ms")
        self._thinking_timer.stop()

    def revert_hook(self, hook: str, *, trigger: str | None = None) -> None:
        """Deactivate a hook, including when another hook currently hides it."""
        if hook == "thinking":
            self._thinking_timer.stop()

        if hook not in self._active_hooks:
            logger.info(
                "[avatar-state] revert_hook no-op (inactive %s) | current=%s | trigger=%s",
                hook,
                self._current_hook,
                trigger if trigger is not None else "unspecified",
            )
            return
        old_hook = self._current_hook
        self._active_hooks.pop(hook, None)
        new_hook = self._select_visible_hook()
        if new_hook != old_hook:
            self._apply_visible_hook(new_hook, trigger=trigger)
        else:
            logger.info(
                "[avatar-state] deactivated hidden hook=%s | current=%s | trigger=%s",
                hook,
                old_hook,
                trigger if trigger is not None else "unspecified",
            )

    def clear_hooks(self, hooks, *, trigger: str | None = None) -> None:
        """Deactivate several related hooks and recompute the visible state once."""
        hooks = set(hooks)
        old_hook = self._current_hook
        removed = hooks.intersection(self._active_hooks)
        if not removed:
            return
        for hook in removed:
            self._active_hooks.pop(hook, None)
        if "thinking" in removed:
            self._thinking_timer.stop()
        new_hook = self._select_visible_hook()
        if new_hook != old_hook:
            self._apply_visible_hook(new_hook, trigger=trigger)
        else:
            logger.info(
                "[avatar-state] deactivated hidden hooks=%s | current=%s | trigger=%s",
                sorted(removed),
                old_hook,
                trigger if trigger is not None else "unspecified",
            )

    def force_revert(self) -> None:
        """Deactivate the currently visible hook and reveal the next active hook."""
        if self._current_hook != "idle":
            self.revert_hook(self._current_hook, trigger="force_revert")

    def force_idle(self, reason: str = "") -> None:
        """Force hook state to idle even if a priority hook is active.

        Used as a safety recovery path when external state indicates PTT is no
        longer active but the UI hook was not reverted correctly.
        """
        old_hook = self._current_hook
        if old_hook == "idle" and not self._active_hooks:
            return
        self._thinking_timer.stop()
        self._active_hooks.clear()
        trig = f"force_idle:{reason}" if reason else "force_idle"
        if reason:
            logger.warning("[Dispatcher] force_idle detail: %s", reason)
        self._apply_visible_hook("idle", trigger=trig)

    def _on_typing_indicator_changed(self, show: bool) -> None:
        """Typing indicator is authoritative for 'agent is actively generating'.

        When it turns off while the avatar is stuck in 'thinking', recover to
        idle immediately. This avoids cases where chat_stream_* signals are
        skipped during interruption/cancellation.
        """
        if show:
            return
        self.revert_hook("thinking", trigger="signal:typing_indicator_changed_false")

    def _select_visible_hook(self) -> str:
        if not self._active_hooks:
            return "idle"
        return max(
            self._active_hooks,
            key=lambda hook: (HOOK_PRIORITY.get(hook, 25), self._active_hooks[hook]),
        )

    def _apply_visible_hook(
        self,
        hook: str,
        *,
        trigger: str | None = None,
        refresh: bool = False,
    ) -> None:
        old_hook = self._current_hook
        if hook == old_hook and not refresh:
            return
        self._previous_hook = old_hook
        self._current_hook = hook
        self._log_avatar_state(old_hook, hook, trigger=trigger)
        logger.debug(
            "[Dispatcher] visible=%s previous=%s active=%s",
            self._current_hook,
            self._previous_hook,
            sorted(self._active_hooks),
        )
        self.event_hook_fired.emit(hook, old_hook)

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
            "workflow_run_all_requested",
            lambda *_: self.fire_hook("running_step_runner", trigger="signal:workflow_run_all_requested"),
        )
        _safe_connect(
            sm,
            "action_recording_stopped",
            lambda _=None: self.revert_hook("recording_action", trigger="signal:action_recording_stopped"),
        )
        _safe_connect(
            sm,
            "action_recording_cancelled",
            lambda _=None: self.revert_hook("recording_action", trigger="signal:action_recording_cancelled"),
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
