"""Forward live web-chat and time-entry events to the paired remote (phone) app.

The desktop web chat gets ``stream_*``/``message_added``/``transcription_progress`` on
its local ``/api/ws`` socket. The phone sits behind the relay, so those events are
re-sent over the relay websocket as ``{"type": "chat_event", "data": payload}``; the
relay forwards ``chat_event`` and ``remote_app_event`` to the paired channel.

Only sent while the remote page has been active recently (it probes presence every
10 s), and stream tokens are coalesced per chat so the relay sees at most a few
frames per second.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

CHAT_EVENTS = frozenset({
    "stream_started",
    "stream_token",
    "stream_finished",
    "stream_error",
    "message_added",
    "transcription_progress",
})
APP_TOPICS = frozenset({"time_entries"})
REMOTE_ACTIVE_WINDOW_SECONDS = 60.0
TOKEN_FLUSH_SECONDS = 0.3
MAX_CONTENT_CHARS = 4000

_lock = threading.Lock()
_token_buffers: Dict[int, list] = {}
_token_timers: Dict[int, threading.Timer] = {}


def _remote_recently_active() -> bool:
    try:
        from distr.core.notification_routing import last_surface_activity

        at = last_surface_activity("remote")
    except Exception:
        return False
    return bool(at) and (time.time() - float(at)) <= REMOTE_ACTIVE_WINDOW_SECONDS


def _manager():
    try:
        from distr.core.integrations.telegram.remote_skin import _resolve_telegram_manager

        return _resolve_telegram_manager()
    except Exception:
        return None


def _send(message: Dict[str, Any]) -> bool:
    manager = _manager()
    if manager is None:
        return False
    try:
        return bool(manager._send_websocket_message(message))
    except Exception:
        logger.debug("Remote chat event send failed", exc_info=True)
        return False


def _flush_tokens(chat_id: int) -> None:
    with _lock:
        timer = _token_timers.pop(chat_id, None)
        buf = _token_buffers.pop(chat_id, None)
    if timer is not None:
        timer.cancel()
    if buf:
        _send({"type": "chat_event", "data": {"event": "stream_token", "chat_id": chat_id, "token": "".join(buf)}})


def forward_chat_event(payload: Dict[str, Any]) -> None:
    """Relay one local web-chat event to the phone (never raises)."""
    try:
        event = str(payload.get("event") or "")
        if event not in CHAT_EVENTS:
            return
        chat_id = int(payload.get("chat_id"))
        if not _remote_recently_active():
            with _lock:
                _token_buffers.pop(chat_id, None)
                timer = _token_timers.pop(chat_id, None)
            if timer is not None:
                timer.cancel()
            return
        if event == "stream_token":
            token = str(payload.get("token") or "")
            if not token:
                return
            with _lock:
                _token_buffers.setdefault(chat_id, []).append(token)
                if chat_id not in _token_timers:
                    timer = threading.Timer(TOKEN_FLUSH_SECONDS, _flush_tokens, args=(chat_id,))
                    timer.daemon = True
                    _token_timers[chat_id] = timer
                    timer.start()
            return
        # Keep order: buffered tokens go out before the terminal/other event.
        _flush_tokens(chat_id)
        data = {k: v for k, v in payload.items() if k != "type"}
        for key in ("content", "response_text"):
            value = data.get(key)
            if isinstance(value, str) and len(value) > MAX_CONTENT_CHARS:
                # The phone re-reads the thread on these events; it only needs a preview.
                data[key] = value[:MAX_CONTENT_CHARS]
        data["event"] = event
        _send({"type": "chat_event", "data": data})
    except Exception:
        logger.debug("forward_chat_event failed", exc_info=True)


def forward_topic_event(topic: str, payload: Dict[str, Any]) -> None:
    """Relay a topic event (e.g. ``time_entries``) so phone Reports can refresh live."""
    try:
        if topic not in APP_TOPICS or not _remote_recently_active():
            return
        data = {k: v for k, v in (payload or {}).items() if isinstance(v, (str, int, float, bool)) or v is None}
        data["topic"] = topic
        _send({"type": "remote_app_event", "data": data})
    except Exception:
        logger.debug("forward_topic_event failed", exc_info=True)
