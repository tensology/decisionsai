"""Push active Oracle/avatar skin updates to remote-control web clients."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


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
