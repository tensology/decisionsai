"""Tests for ``BoundedOutboundQueue.pop_wait`` and ``IntegrationOutboundWorker``."""

from __future__ import annotations

import threading
import time

from distr.core.integrations.outbound_queue import BoundedOutboundQueue
from distr.core.integrations.outbound_worker import ATTEMPT_META_KEY, IntegrationOutboundWorker


def test_pop_wait_returns_after_push() -> None:
    q: BoundedOutboundQueue[str] = BoundedOutboundQueue(max_items=8)
    seen: list[str | None] = []

    def waiter() -> None:
        seen.append(q.pop_wait(timeout=2.0))

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.05)
    assert q.push("x") is True
    t.join(timeout=2.0)
    assert seen == ["x"]


def test_worker_retries_then_succeeds() -> None:
    q = BoundedOutboundQueue[dict](max_items=16)
    calls = {"n": 0}

    def deliver(item: dict) -> None:
        assert ATTEMPT_META_KEY not in item
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")

    worker = IntegrationOutboundWorker(
        q,
        deliver,
        base_delay_s=0.02,
        max_delay_s=0.05,
        max_attempts=5,
        poll_s=0.05,
        thread_name="test-outbound",
    )
    worker.start_daemon()
    assert q.push({"channel_id": "c1", "text": "hi"}) is True
    deadline = time.time() + 3.0
    while calls["n"] < 3 and time.time() < deadline:
        time.sleep(0.03)
    worker.stop(join_timeout_s=2.0)
    assert calls["n"] == 3


def test_worker_drops_after_max_attempts() -> None:
    q = BoundedOutboundQueue[dict](max_items=16)

    def deliver(_item: dict) -> None:
        raise RuntimeError("always")

    worker = IntegrationOutboundWorker(
        q,
        deliver,
        base_delay_s=0.01,
        max_delay_s=0.03,
        max_attempts=2,
        poll_s=0.05,
        thread_name="test-drop",
    )
    worker.start_daemon()
    assert q.push({"x": 1}) is True
    time.sleep(0.5)
    worker.stop(join_timeout_s=2.0)
    assert len(q) == 0
