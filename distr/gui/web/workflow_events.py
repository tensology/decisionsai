"""
Shared state for Workflow UI updates.

When the agent records a tool execution or workflow data changes,
the counter increments AND a WebSocket push is sent so the web UI
refreshes immediately without polling.
"""
import threading
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_workflow_update_counter = 0
_pending_single_step = None
_lock = threading.Lock()

# All connected workflow WebSocket clients (set of websocket objects)
_wf_ws_connections: set = set()
_wf_ws_lock = threading.Lock()


def register_wf_websocket(ws, loop) -> None:
    with _wf_ws_lock:
        _wf_ws_connections.add((ws, loop))


def unregister_wf_websocket(ws) -> None:
    with _wf_ws_lock:
        to_remove = {(w, l) for w, l in _wf_ws_connections if w is ws}
        _wf_ws_connections.difference_update(to_remove)


def _push_ws_update(version: int) -> None:
    """Fire-and-forget push to all connected workflow WS clients."""
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
                        logger.debug("workflow WS send failed: %s", e)
                future.add_done_callback(_log_err)
        except Exception as e:
            logger.debug("workflow WS push failed: %s", e)


def increment_workflow_updated() -> None:
    """Call when Workflow data changes — increments counter and pushes WS event."""
    global _workflow_update_counter
    with _lock:
        _workflow_update_counter += 1
        v = _workflow_update_counter
    _push_ws_update(v)


def get_workflow_update_counter() -> int:
    """Return current counter for web UI polling."""
    with _lock:
        return _workflow_update_counter


def set_pending_single_step(payload: dict) -> None:
    """Set pending single-step execution state."""
    global _pending_single_step
    with _lock:
        _pending_single_step = payload


def get_pending_single_step() -> dict:
    """Get pending single-step execution state."""
    with _lock:
        return dict(_pending_single_step) if _pending_single_step else {}


def clear_pending_single_step() -> None:
    """Clear pending single-step execution state."""
    global _pending_single_step
    with _lock:
        _pending_single_step = None
