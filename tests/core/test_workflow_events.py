"""Tests for distr.gui.web.workflow_events — websocket push and counter logic."""
import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from distr.gui.web.workflow_events import (
    _wf_ws_connections,
    _wf_ws_lock,
    get_workflow_update_counter,
    increment_workflow_updated,
    register_wf_websocket,
    unregister_wf_websocket,
)


@pytest.fixture(autouse=True)
def _clean_ws_state():
    """Reset module-level state between tests."""
    with _wf_ws_lock:
        _wf_ws_connections.clear()
    yield
    with _wf_ws_lock:
        _wf_ws_connections.clear()


async def _wait_for_stop(stop_event: threading.Event):
    """Async helper that keeps the event loop running until stop_event is set."""
    while not stop_event.is_set():
        await asyncio.sleep(0.05)


class TestRegisterUnregister:
    def test_register_adds_connection(self):
        ws = MagicMock()
        loop = MagicMock()
        register_wf_websocket(ws, loop)
        with _wf_ws_lock:
            assert (ws, loop) in _wf_ws_connections

    def test_unregister_removes_connection(self):
        ws = MagicMock()
        loop = MagicMock()
        register_wf_websocket(ws, loop)
        unregister_wf_websocket(ws)
        with _wf_ws_lock:
            assert (ws, loop) not in _wf_ws_connections

    def test_unregister_nonexistent_is_noop(self):
        ws = MagicMock()
        unregister_wf_websocket(ws)  # should not raise


class TestIncrementAndCounter:
    def test_increment_increases_counter(self):
        v1 = get_workflow_update_counter()
        increment_workflow_updated()
        v2 = get_workflow_update_counter()
        assert v2 == v1 + 1

    def test_multiple_increments(self):
        v1 = get_workflow_update_counter()
        for _ in range(5):
            increment_workflow_updated()
        assert get_workflow_update_counter() == v1 + 5


class TestWebSocketPush:
    def test_increment_sends_workflow_updated_message(self):
        """When a WS client is registered, increment_workflow_updated sends a workflow_updated message."""
        loop = asyncio.new_event_loop()
        ws = MagicMock()
        ws.send_text = AsyncMock(return_value=None)

        register_wf_websocket(ws, loop)

        # Run the event loop in a thread so run_coroutine_threadsafe works
        stop_event = threading.Event()

        def run_loop():
            loop.run_until_complete(_wait_for_stop(stop_event))

        t = threading.Thread(target=run_loop, daemon=True)
        t.start()

        try:
            increment_workflow_updated()
            # Give the fire-and-forget send a moment
            import time
            time.sleep(0.2)

            # Verify send_text was called with a workflow_updated payload
            assert ws.send_text.called or ws.send_text.await_count > 0
            call_args = ws.send_text.call_args
            payload = json.loads(call_args[0][0])
            assert payload["type"] == "workflow_updated"
            assert "version" in payload
        finally:
            stop_event.set()
            t.join(timeout=2)
            loop.close()
            unregister_wf_websocket(ws)

    def test_no_clients_no_error(self):
        """increment_workflow_updated works fine with no connected clients."""
        # Should not raise
        increment_workflow_updated()
