"""Bounded outbound queues for connector rate-limit / retry staging (TASK 16–17)."""

from __future__ import annotations

from collections import deque
from threading import Condition, Lock
from typing import Generic, TypeVar

T = TypeVar("T")


class BoundedOutboundQueue(Generic[T]):
    """Thread-safe FIFO with a hard cap; ``push`` returns False when full."""

    def __init__(self, max_items: int = 512) -> None:
        self._max = max(1, int(max_items))
        self._items: deque[T] = deque()
        self._cv = Condition(Lock())

    def push(self, item: T) -> bool:
        with self._cv:
            if len(self._items) >= self._max:
                return False
            self._items.append(item)
            self._cv.notify()
            return True

    def pop(self) -> T | None:
        with self._cv:
            return self._items.popleft() if self._items else None

    def pop_wait(self, timeout: float | None) -> T | None:
        """Block until an item is available or ``timeout`` seconds elapse (then return ``None``)."""
        with self._cv:
            if not self._items:
                self._cv.wait(timeout=timeout)
            return self._items.popleft() if self._items else None

    def __len__(self) -> int:
        with self._cv:
            return len(self._items)
