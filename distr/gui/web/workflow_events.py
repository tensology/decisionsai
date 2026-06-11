"""
Shared state for Workflow UI updates.

When the agent records a tool execution or workflow data changes,
the counter increments AND a WebSocket push is sent so the web UI
refreshes immediately without polling.
"""
import collections
import time
import threading
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_workflow_update_counter = 0
_lock = threading.Lock()
_workflow_updated_callbacks: list = []
_callbacks_lock = threading.Lock()

# All connected workflow WebSocket clients (set of websocket objects)
_wf_ws_connections: set = set()
_wf_ws_lock = threading.Lock()

# Ring buffer of the last 200 push events for client catch-up after reconnect.
_event_log: collections.deque = collections.deque(maxlen=200)
_event_log_lock = threading.Lock()


def register_wf_websocket(ws, loop) -> None:
    with _wf_ws_lock:
        _wf_ws_connections.add((ws, loop))


def unregister_wf_websocket(ws) -> None:
    with _wf_ws_lock:
        to_remove = {(w, l) for w, l in _wf_ws_connections if w is ws}
        _wf_ws_connections.difference_update(to_remove)


def _push_ws_update(version: int) -> None:
    """Fire-and-forget push to all connected workflow WS clients."""
    with _event_log_lock:
        _event_log.append({"version": version, "ts": time.time()})
    payload = json.dumps({"type": "workflow_updated", "version": version})
    with _wf_ws_lock:
        conns = list(_wf_ws_connections)
    if not conns:
        return
    logger.debug("workflow WS: pushing to %d client(s)", len(conns))
    for ws, loop in conns:
        try:
            if loop and not loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(ws.send_text(payload), loop)
                # Don't wait — fire and forget, but log failures
                def _log_err(f, _ws=ws):
                    try:
                        f.result(timeout=2)
                    except Exception as e:
                        logger.debug("workflow WS send failed (removing stale connection): %s", e)
                        unregister_wf_websocket(_ws)
                future.add_done_callback(_log_err)
        except Exception as e:
            logger.debug("workflow WS push failed: %s", e)


def register_workflow_updated_callback(callback) -> None:
    """Register a desktop-side callback fired after workflow/automation data changes."""
    if not callable(callback):
        return
    with _callbacks_lock:
        if callback not in _workflow_updated_callbacks:
            _workflow_updated_callbacks.append(callback)


def increment_workflow_updated() -> None:
    """Call when Workflow data changes — increments counter and pushes WS event."""
    global _workflow_update_counter
    with _lock:
        _workflow_update_counter += 1
        v = _workflow_update_counter
    _push_ws_update(v)
    with _callbacks_lock:
        callbacks = list(_workflow_updated_callbacks)
    for callback in callbacks:
        try:
            callback()
        except Exception as exc:
            logger.debug("workflow updated callback failed: %s", exc)


def get_workflow_update_counter() -> int:
    """Return current counter for web UI polling."""
    with _lock:
        return _workflow_update_counter


def get_events_since(version: int) -> list:
    """Return all logged events with version > *version*.

    Used by clients reconnecting after a gap to determine whether they
    missed any updates and need to refresh their state.
    """
    with _event_log_lock:
        return [e for e in _event_log if e["version"] > version]


