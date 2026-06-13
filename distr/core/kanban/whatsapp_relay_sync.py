"""Shared WhatsApp relay sync + spoken summary for tray menu (not web UI sync)."""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def build_whatsapp_sync_speech(result: Mapping[str, Any]) -> str:
    """Return a short TTS-friendly line for a relay sync result."""
    if result.get("error"):
        return "WhatsApp sync did not complete."
    synced = int(result.get("synced") or 0)
    if synced > 0:
        noun = "messages" if synced != 1 else "message"
        return f"Synced {synced} new {noun} from WhatsApp."
    return "No new messages on WhatsApp."


def is_whatsapp_account_connected() -> bool:
    """True when WhatsApp is linked in Settings (no Qt imports)."""
    try:
        from distr.core.utils import load_settings_from_db

        settings = load_settings_from_db()
        raw = settings.get("connected_accounts") or "[]"
        accounts = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(accounts, list):
            return False
        return any(
            isinstance(acc, dict)
            and acc.get("provider") == "whatsapp"
            and acc.get("status") == "connected"
            for acc in accounts
        )
    except Exception:
        return False


def get_whatsapp_manager():
    """Return the desktop WhatsApp manager when the Qt app is running."""
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        return getattr(app, "whatsapp_manager", None) if app else None
    except Exception:
        return None


def sync_whatsapp_from_relay(*, mark_processed: bool = False) -> dict[str, Any]:
    """Pull relay messages into the local DB (same path as the kanban web sync button)."""
    logger.info("WhatsApp relay sync starting (mark_processed=%s)", mark_processed)
    wa_manager = get_whatsapp_manager()
    if wa_manager:
        try:
            result = wa_manager.sync_from_relay(mark_processed=mark_processed) or {"synced": 0}
            logger.info("WhatsApp relay sync via manager finished: %s", result)
            return result
        except Exception as exc:
            logger.error("WhatsApp relay sync via manager failed: %s", exc, exc_info=True)
            return {"synced": 0, "error": str(exc)}

    from distr.core.integrations.whatsapp.relay_client import sync_messages_from_relay

    if not is_whatsapp_account_connected():
        return {"synced": 0, "error": "WhatsApp not connected"}
    result = sync_messages_from_relay(mark_processed=mark_processed)
    logger.info("WhatsApp relay sync via headless client finished: %s", result)
    return result


def announce_whatsapp_sync(result: Mapping[str, Any]) -> None:
    """Speak the sync outcome through the agent TTS pipeline."""
    try:
        from distr.core.signals import speak_text_directly_event_queue

        speak_text_directly_event_queue(build_whatsapp_sync_speech(result))
    except Exception:
        logger.debug("announce_whatsapp_sync failed", exc_info=True)


def sync_whatsapp_from_relay_and_announce(*, mark_processed: bool = False) -> dict[str, Any]:
    """Sync relay messages and speak how many new messages were pulled in."""
    result = sync_whatsapp_from_relay(mark_processed=mark_processed)
    announce_whatsapp_sync(result)
    return dict(result)
