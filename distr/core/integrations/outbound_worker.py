"""Daemon worker: drain connector outbound queues with exponential backoff retries."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Generic, TypeVar

from distr.core.integrations.outbound_queue import BoundedOutboundQueue

logger = logging.getLogger(__name__)

T = TypeVar("T")

ATTEMPT_META_KEY = "_decisionsai_outbound_attempt"


def _attempt_count(item: object) -> int:
    if isinstance(item, dict):
        raw = item.get(ATTEMPT_META_KEY)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0
    return 0


def _with_attempt_increment(item: T) -> T:
    if isinstance(item, dict):
        out = dict(item)
        out[ATTEMPT_META_KEY] = _attempt_count(item) + 1
        return out  # type: ignore[return-value]
    return item


def _strip_attempt_meta_for_delivery(item: T) -> T:
    if isinstance(item, dict) and ATTEMPT_META_KEY in item:
        return {k: v for k, v in item.items() if k != ATTEMPT_META_KEY}  # type: ignore[return-value]
    return item


class IntegrationOutboundWorker(Generic[T]):
    """Blocking loop: ``pop_wait`` → deliver; on failure re-queue with backoff until ``max_attempts``."""

    def __init__(
        self,
        queue: BoundedOutboundQueue[T],
        deliver: Callable[[T], None],
        *,
        base_delay_s: float = 0.5,
        max_delay_s: float = 60.0,
        max_attempts: int = 5,
        poll_s: float = 0.5,
        thread_name: str = "integration-outbound",
    ) -> None:
        self._queue = queue
        self._deliver = deliver
        self._base_delay_s = max(0.05, float(base_delay_s))
        self._max_delay_s = max(self._base_delay_s, float(max_delay_s))
        self._max_attempts = max(1, int(max_attempts))
        self._poll_s = max(0.05, float(poll_s))
        self._thread_name = thread_name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start_daemon(self) -> threading.Thread:
        """Start the worker on a daemon thread. Idempotent if already running."""
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self._thread_name, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, join_timeout_s: float = 2.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=join_timeout_s)

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.pop_wait(timeout=self._poll_s)
            if item is None:
                continue
            attempt = _attempt_count(item)
            to_send = _strip_attempt_meta_for_delivery(item)
            try:
                self._deliver(to_send)
            except Exception:
                logger.exception(
                    "%s: deliver failed (attempt %s/%s)",
                    self._thread_name,
                    attempt + 1,
                    self._max_attempts,
                )
                if attempt + 1 >= self._max_attempts:
                    logger.error("%s: dropping payload after max attempts", self._thread_name)
                    continue
                bumped = _with_attempt_increment(item)
                delay = min(self._max_delay_s, self._base_delay_s * (2**attempt))
                if self._stop.wait(timeout=delay):
                    if not self._queue.push(bumped):
                        logger.warning("%s: re-queue failed (full) after stop", self._thread_name)
                    break
                if not self._queue.push(bumped):
                    logger.warning("%s: re-queue failed (full); payload lost", self._thread_name)
