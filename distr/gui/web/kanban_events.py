"""Shared state for Ticket Board UI realtime updates."""

import asyncio
import json
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_kanban_update_counter = 0

_kb_ws_connections: set = set()
_kb_ws_lock = threading.Lock()


def register_kb_websocket(ws, loop) -> None:
    with _kb_ws_lock:
        _kb_ws_connections.add((ws, loop))


def unregister_kb_websocket(ws) -> None:
    with _kb_ws_lock:
        stale = {(w, l) for w, l in _kb_ws_connections if w is ws}
        _kb_ws_connections.difference_update(stale)


def _push_ws_update(version: int, board_id: Optional[int], event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
    message = {
        "type": "kanban_updated",
        "version": version,
        "board_id": board_id,
        "event": event_type,
        "payload": payload or {},
    }
    body = json.dumps(message)
    with _kb_ws_lock:
        conns = list(_kb_ws_connections)
    if not conns:
        return
    for ws, loop in conns:
        try:
            if loop and not loop.is_closed():
                fut = asyncio.run_coroutine_threadsafe(ws.send_text(body), loop)

                def _on_done(f, _ws=ws):
                    if f.done() and not f.cancelled():
                        exc = f.exception()
                        if exc is not None:
                            logger.debug("kanban WS send failed (removing stale connection): %s", exc)
                            unregister_kb_websocket(_ws)
                fut.add_done_callback(_on_done)
        except Exception as e:
            logger.debug("kanban WS push failed: %s", e)


def increment_kanban_updated(board_id: Optional[int], event_type: str = "board_changed", payload: Optional[Dict[str, Any]] = None) -> None:
    """Increment board update counter and push websocket event."""
    global _kanban_update_counter
    with _lock:
        _kanban_update_counter += 1
        v = _kanban_update_counter
    _push_ws_update(v, board_id, event_type, payload)


def get_kanban_update_counter() -> int:
    with _lock:
        return _kanban_update_counter

