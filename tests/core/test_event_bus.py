"""Tests for distr.core.events.EventBus."""

import threading
from unittest.mock import MagicMock

from distr.core.events import (
    CHAT_MESSAGE_RECEIVED,
    EventBus,
    WORKFLOW_STEP_STARTED,
    get_event_bus,
    reset_event_bus_for_tests,
)


def test_publish_delivers_to_subscriber():
    bus = EventBus()
    seen = []

    def h(etype, data):
        seen.append((etype, data))

    bus.subscribe(CHAT_MESSAGE_RECEIVED, h)
    bus.publish(CHAT_MESSAGE_RECEIVED, {"id": 1})
    assert seen == [(CHAT_MESSAGE_RECEIVED, {"id": 1})]


def test_unsubscribe_removes_handler():
    bus = EventBus()
    calls = []

    def h(etype, data):
        calls.append(data)

    bus.subscribe(WORKFLOW_STEP_STARTED, h)
    bus.publish(WORKFLOW_STEP_STARTED, "a")
    bus.unsubscribe(WORKFLOW_STEP_STARTED, h)
    bus.publish(WORKFLOW_STEP_STARTED, "b")
    assert calls == ["a"]


def test_handler_exception_does_not_block_others(caplog):
    bus = EventBus()

    def bad(_etype, _data):
        raise RuntimeError("boom")

    def good(_etype, data, box):
        box.append(data)

    received = []
    bus.subscribe(CHAT_MESSAGE_RECEIVED, bad)
    bus.subscribe(CHAT_MESSAGE_RECEIVED, lambda et, d: good(et, d, received))
    with caplog.at_level("ERROR"):
        bus.publish(CHAT_MESSAGE_RECEIVED, 42)
    assert received == [42]
    assert "EventBus handler failed" in caplog.text


def test_concurrent_publish_thread_safe():
    bus = EventBus()
    barrier = threading.Barrier(4)
    counts = {"n": 0}
    lock = threading.Lock()

    def bump(_etype, _data):
        with lock:
            counts["n"] += 1

    bus.subscribe(CHAT_MESSAGE_RECEIVED, bump)

    def worker():
        barrier.wait()
        for _ in range(100):
            bus.publish(CHAT_MESSAGE_RECEIVED, None)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counts["n"] == 400


def test_publish_snapshots_handlers_so_new_subscribers_mid_publish_not_called():
    """Handlers registered during publish should not run for that same publish."""
    bus = EventBus()
    order = []

    def first(_etype, _data):
        order.append("first")

        def late(_et2, _d2):
            order.append("late")

        bus.subscribe(CHAT_MESSAGE_RECEIVED, late)

    bus.subscribe(CHAT_MESSAGE_RECEIVED, first)
    bus.publish(CHAT_MESSAGE_RECEIVED, None)
    assert order == ["first"]
    bus.publish(CHAT_MESSAGE_RECEIVED, None)
    assert order == ["first", "first", "late"]


def test_unsubscribe_during_publish_does_not_affect_current_round():
    """Removing a handler during publish does not change the in-flight copy."""
    bus = EventBus()
    second_called = MagicMock()

    def first(etype, data):
        bus.unsubscribe(etype, second)

    def second(etype, data):
        second_called(etype, data)

    bus.subscribe(CHAT_MESSAGE_RECEIVED, first)
    bus.subscribe(CHAT_MESSAGE_RECEIVED, second)
    bus.publish(CHAT_MESSAGE_RECEIVED, "x")
    second_called.assert_called_once_with(CHAT_MESSAGE_RECEIVED, "x")


def test_get_event_bus_returns_singleton():
    reset_event_bus_for_tests()
    a = get_event_bus()
    b = get_event_bus()
    assert a is b
