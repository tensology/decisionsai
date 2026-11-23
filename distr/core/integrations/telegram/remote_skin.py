"""Push active Oracle/avatar skin updates to remote-control web clients."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_AVATAR_STATE_MIN_INTERVAL_S = 1.0
_avatar_state_lock = threading.Lock()
_last_avatar_state: Optional[dict] = None
_last_avatar_state_sent_at = float("-inf")
_pending_avatar_state: Optional[dict] = None
_avatar_state_timer: Optional[threading.Timer] = None


def _resolve_telegram_manager():
    """Return the connected Telegram relay manager when available."""
    try:
        from PyQt6.QtCore import QCoreApplication
    except Exception:
        return None

    app = QCoreApplication.instance()
    if app is None:
        return None

    manager = getattr(app, "telegram_manager", None)
    if manager is None:
        return None

    try:
        if hasattr(manager, "is_connected") and not manager.is_connected():
            return None
    except Exception:
        return None

    return manager


def notify_remote_skin_changed(
    *,
    folder_name: str,
    skin_name: Optional[str] = None,
    skin_type: Optional[str] = None,
    idle_animation: Optional[str] = None,
) -> bool:
    """Notify connected remote-control pages that the desktop skin changed."""
    manager = _resolve_telegram_manager()
    if manager is None:
        return False

    selected = str(folder_name or "").strip()
    if not selected:
        return False

    payload = {
        "type": "remote_skin_changed",
        "data": {
            "selected_skin": selected,
            "name": skin_name or selected,
            "type": skin_type or "oracle",
            "idle_animation": idle_animation,
        },
    }

    try:
        sent = bool(manager._send_websocket_message(payload))
        if sent:
            logger.info(
                "Notified remote control of skin change: %s (%s)",
                selected,
                idle_animation or "default idle",
            )
        return sent
    except Exception:
        logger.exception("Failed to notify remote control of skin change")
        return False


def _send_avatar_state(manager, payload: dict) -> bool:
    global _last_avatar_state, _last_avatar_state_sent_at

    if not manager._send_websocket_message(payload):
        return False
    _last_avatar_state = payload
    _last_avatar_state_sent_at = time.monotonic()
    return True


def _flush_pending_avatar_state() -> None:
    global _pending_avatar_state, _avatar_state_timer

    with _avatar_state_lock:
        payload = _pending_avatar_state
        _pending_avatar_state = None
        _avatar_state_timer = None
        if payload is None or payload == _last_avatar_state:
            return

        try:
            manager = _resolve_telegram_manager()
            if manager is not None:
                _send_avatar_state(manager, payload)
        except Exception:
            logger.exception("Failed to notify remote control of avatar state")


def _reset_avatar_state_throttle() -> None:
    global _last_avatar_state, _last_avatar_state_sent_at, _pending_avatar_state, _avatar_state_timer

    with _avatar_state_lock:
        if _avatar_state_timer is not None:
            _avatar_state_timer.cancel()
        _avatar_state_timer = None
        _pending_avatar_state = None
        _last_avatar_state = None
        _last_avatar_state_sent_at = float("-inf")


def notify_remote_avatar_state(
    *,
    folder_name: str,
    hook: str,
    animation: str,
    playback: str = "loop",
) -> bool:
    """Push the active skin animation to connected remote-control pages."""
    global _pending_avatar_state, _avatar_state_timer

    manager = _resolve_telegram_manager()
    if manager is None:
        return False

    selected = str(folder_name or "").strip()
    state = str(hook or "").strip()
    filename = str(animation or "").strip()
    if not selected or not state or not filename:
        return False

    payload = {
        "type": "remote_avatar_state",
        "data": {
            "selected_skin": selected,
            "hook": state,
            "animation": filename,
            "playback": playback or "loop",
        },
    }

    with _avatar_state_lock:
        if _pending_avatar_state is None and payload == _last_avatar_state:
            return True

        remaining = _AVATAR_STATE_MIN_INTERVAL_S - (time.monotonic() - _last_avatar_state_sent_at)
        if _avatar_state_timer is None and remaining <= 0:
            try:
                return _send_avatar_state(manager, payload)
            except Exception:
                logger.exception("Failed to notify remote control of avatar state")
                return False

        _pending_avatar_state = payload
        if _avatar_state_timer is None:
            _avatar_state_timer = threading.Timer(remaining, _flush_pending_avatar_state)
            _avatar_state_timer.daemon = True
            _avatar_state_timer.start()
        return True
