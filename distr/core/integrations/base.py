"""IntegrationReconnectMixin — shared exponential-backoff reconnect logic.

Both WhatsAppWebSocketManager and TelegramWebSocketManager use the same
pattern. This mixin centralises it so it lives in one place.

Usage
-----
class MyManager(IntegrationReconnectMixin, QObject):
    def __init__(self):
        super().__init__()
        self._init_reconnect_state()   # call once in __init__

    def _do_reconnect(self):
        if not self._active_disconnect:
            self.connect()

In _on_connected:    call self._reset_reconnect_state("MyManager")
In _on_disconnected: call self._schedule_reconnect("MyManager")
In _on_error:        call self._schedule_reconnect("MyManager")
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Default reconnect timing constants (milliseconds)
_DEFAULT_DELAY_MS = 3_000
_DEFAULT_MAX_DELAY_MS = 60_000


class IntegrationReconnectMixin:
    """Mixin that provides exponential-backoff WebSocket reconnection helpers.

    Subclasses must call ``_init_reconnect_state()`` from their own
    ``__init__`` before using any of the helpers.
    """

    def _init_reconnect_state(
        self,
        initial_delay_ms: int = _DEFAULT_DELAY_MS,
        max_delay_ms: int = _DEFAULT_MAX_DELAY_MS,
    ) -> None:
        """Initialise reconnection counters and limits.

        Call this once from ``__init__`` *after* ``super().__init__()``.
        """
        self._reconnect_delay_ms: int = initial_delay_ms
        self._reconnect_delay_max_ms: int = max_delay_ms
        self._reconnect_delay_current_ms: int = initial_delay_ms
        self._reconnect_attempts: int = 0
        self._active_disconnect: bool = False

    def _schedule_reconnect(self, label: str) -> None:
        """Apply exponential backoff and start the reconnect timer.

        Safe to call from ``_on_disconnected`` and ``_on_error``.
        Does nothing if ``_active_disconnect`` is True (intentional disconnect)
        or if the timer is already running.
        """
        if getattr(self, "_active_disconnect", False):
            return
        timer = getattr(self, "_reconnect_timer", None)
        if timer is None:
            logger.debug("%s: _schedule_reconnect called but _reconnect_timer not set", label)
            return
        if timer.isActive():
            return  # already scheduled
        self._reconnect_delay_current_ms = min(
            getattr(self, "_reconnect_delay_current_ms", _DEFAULT_DELAY_MS) * 2,
            getattr(self, "_reconnect_delay_max_ms", _DEFAULT_MAX_DELAY_MS),
        )
        attempt = getattr(self, "_reconnect_attempts", 0) + 1
        logger.info(
            "%s: scheduling reconnect in %dms (attempt #%d)",
            label,
            self._reconnect_delay_current_ms,
            attempt,
        )
        timer.start(self._reconnect_delay_current_ms)

    def _reset_reconnect_state(self, label: str) -> None:
        """Reset backoff counters after a successful connection.

        Call from ``_on_connected``.
        """
        timer = getattr(self, "_reconnect_timer", None)
        if timer is not None:
            timer.stop()
        self._reconnect_attempts = 0
        self._reconnect_delay_current_ms = getattr(
            self, "_reconnect_delay_ms", _DEFAULT_DELAY_MS
        )
        logger.debug("%s: reconnect state reset after successful connection", label)
