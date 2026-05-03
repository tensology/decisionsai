"""Thread-safe in-process event bus (pub/sub)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

EventHandler = Callable[[str, Any], None]


class EventBus:
    """Publish/subscribe event routing within one process.

    - ``subscribe`` / ``unsubscribe`` mutate the subscriber map under a lock.
    - ``publish`` copies the handler list under the lock, then invokes handlers
      without holding the lock so slow handlers cannot deadlock publishers.
    - Per-handler exceptions are logged and do not prevent other handlers from running.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[EventHandler]] = {}

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register ``handler`` for ``event_type``. Duplicate registrations are allowed."""
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove one registration of ``handler`` for ``event_type`` (no-op if missing)."""
        with self._lock:
            lst = self._subscribers.get(event_type)
            if not lst:
                return
            try:
                lst.remove(handler)
            except ValueError:
                return
            if not lst:
                del self._subscribers[event_type]

    def publish(self, event_type: str, data: Any = None) -> None:
        """Notify subscribers of ``event_type`` with optional ``data`` payload."""
        with self._lock:
            handlers = list(self._subscribers.get(event_type, []))
        for fn in handlers:
            try:
                fn(event_type, data)
            except Exception:
                logger.exception(
                    "EventBus handler failed for event_type=%r", event_type
                )


_global_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Process-wide default bus (MCP, SSE hooks, cross-module signals)."""
    global _global_bus
    with _bus_lock:
        if _global_bus is None:
            _global_bus = EventBus()
        return _global_bus


def reset_event_bus_for_tests() -> None:
    """Replace the global bus with a fresh instance (tests only)."""
    global _global_bus
    with _bus_lock:
        _global_bus = EventBus()
