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
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Default reconnect timing constants (milliseconds)
_DEFAULT_DELAY_MS = 3_000
_DEFAULT_MAX_DELAY_MS = 60_000


def _timer_is_active(timer) -> bool:
    try:
        return bool(timer.isActive())
    except AttributeError:
        return False


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
        if _timer_is_active(timer):
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

    def _init_socket_heartbeat_state(
        self,
        label: str,
        *,
        interval_ms: int = 30_000,
        timeout_ms: int = 8_000,
        ping_payload: Optional[dict] = None,
    ) -> None:
        """Initialise active WebSocket heartbeat timers.

        Subclasses must implement ``is_connected()`` and ``_send_websocket_message``.
        Any inbound WebSocket frame should call ``_mark_socket_heartbeat_seen()``.
        """
        from PyQt6.QtCore import QTimer

        self._socket_heartbeat_label = label
        self._socket_heartbeat_interval_ms = interval_ms
        self._socket_heartbeat_timeout_ms = timeout_ms
        self._socket_heartbeat_ping_payload = ping_payload or {"type": "ping"}
        self._socket_heartbeat_waiting_for_pong = False
        self._socket_heartbeat_ping_sent_at = 0.0

        self._socket_heartbeat_timer = QTimer()
        self._socket_heartbeat_timer.timeout.connect(self._socket_heartbeat_tick)

        self._socket_heartbeat_timeout_timer = QTimer()
        self._socket_heartbeat_timeout_timer.setSingleShot(True)
        self._socket_heartbeat_timeout_timer.timeout.connect(
            self._socket_heartbeat_timeout
        )

    def _start_socket_heartbeat(self) -> None:
        timer = getattr(self, "_socket_heartbeat_timer", None)
        if timer is None:
            return
        if not _timer_is_active(timer):
            timer.start(getattr(self, "_socket_heartbeat_interval_ms", 30_000))

    def _stop_socket_heartbeat(self) -> None:
        timer = getattr(self, "_socket_heartbeat_timer", None)
        if timer is not None:
            timer.stop()
        timeout_timer = getattr(self, "_socket_heartbeat_timeout_timer", None)
        if timeout_timer is not None:
            timeout_timer.stop()
        self._socket_heartbeat_waiting_for_pong = False

    def _mark_socket_heartbeat_seen(self) -> None:
        self._last_message_time = time.time()
        self._socket_heartbeat_waiting_for_pong = False
        timeout_timer = getattr(self, "_socket_heartbeat_timeout_timer", None)
        if timeout_timer is not None:
            timeout_timer.stop()

    def _socket_heartbeat_tick(self) -> None:
        label = getattr(self, "_socket_heartbeat_label", "Integration")
        if getattr(self, "_active_disconnect", False):
            return

        try:
            connected = bool(self.is_connected())
        except Exception:
            connected = False

        if not connected:
            logger.info("%s: heartbeat found disconnected socket; scheduling reconnect", label)
            self._schedule_reconnect(label)
            return

        if getattr(self, "_socket_heartbeat_waiting_for_pong", False):
            self._socket_heartbeat_timeout()
            return

        payload = dict(getattr(self, "_socket_heartbeat_ping_payload", {"type": "ping"}))
        try:
            sent = bool(self._send_websocket_message(payload))
        except Exception as exc:
            logger.warning("%s: heartbeat ping failed: %s", label, exc)
            self._force_socket_reconnect(label, "heartbeat ping failed")
            return

        if not sent:
            logger.warning("%s: heartbeat ping was not sent; forcing reconnect", label)
            self._force_socket_reconnect(label, "heartbeat ping not sent")
            return

        self._socket_heartbeat_waiting_for_pong = True
        self._socket_heartbeat_ping_sent_at = time.time()
        timeout_timer = getattr(self, "_socket_heartbeat_timeout_timer", None)
        if timeout_timer is not None:
            timeout_timer.start(getattr(self, "_socket_heartbeat_timeout_ms", 8_000))

    def _socket_heartbeat_timeout(self) -> None:
        if not getattr(self, "_socket_heartbeat_waiting_for_pong", False):
            return
        label = getattr(self, "_socket_heartbeat_label", "Integration")
        sent_at = getattr(self, "_socket_heartbeat_ping_sent_at", 0.0)
        elapsed_ms = max(0, int((time.time() - sent_at) * 1000)) if sent_at else 0
        self._force_socket_reconnect(
            label,
            f"heartbeat pong timeout after {elapsed_ms}ms",
        )

    def _force_socket_reconnect(self, label: str, reason: str) -> None:
        logger.warning("%s: %s; forcing reconnect", label, reason)
        self._socket_heartbeat_waiting_for_pong = False
        timeout_timer = getattr(self, "_socket_heartbeat_timeout_timer", None)
        if timeout_timer is not None:
            timeout_timer.stop()

        self._active_disconnect = False
        if hasattr(self, "_connected"):
            self._connected = False

        socket = getattr(self, "socket", None)
        if socket is not None:
            abort = getattr(socket, "abort", None)
            close = getattr(socket, "close", None)
            try:
                if callable(abort):
                    abort()
                elif callable(close):
                    close()
            except Exception as exc:
                logger.debug("%s: socket close during forced reconnect failed: %s", label, exc)

        self._schedule_reconnect(label)
