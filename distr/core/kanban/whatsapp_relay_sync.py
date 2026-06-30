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
    warning = str(result.get("warning") or "").strip()
    relay_ok = result.get("relay_link_ok", True)
    if warning and not relay_ok:
        if synced > 0:
            noun = "messages" if synced != 1 else "message"
            return (
                f"Synced {synced} older {noun}, but WhatsApp on the server is not linked. "
                "Re-link in Settings to receive new messages."
            )
        return "WhatsApp on the server is not linked. Scan the QR code in Settings to reconnect."
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


def relay_whatsapp_live_status() -> dict[str, Any]:
    """Current Baileys link state on the relay server."""
    wa_manager = get_whatsapp_manager()
    if wa_manager:
        try:
            data = wa_manager.get_status()
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.debug("relay_whatsapp_live_status via manager failed: %s", exc)
    from distr.core.integrations.whatsapp.relay_client import fetch_relay_whatsapp_status

    return fetch_relay_whatsapp_status()


def is_relay_whatsapp_connected(status: Mapping[str, Any]) -> bool:
    return str(status.get("status") or "").strip().lower() == "connected"


def relay_link_warning(status: Mapping[str, Any]) -> str | None:
    """User-facing warning when the server-side WhatsApp session is not live."""
    state = str(status.get("status") or "").strip().lower()
    if state == "connected":
        return None
    if state == "qr_ready":
        return (
            "WhatsApp on the server needs a QR scan. "
            "Open Settings → Connected Accounts and link again — nothing new will sync until then."
        )
    if state in ("disconnected", "close"):
        return (
            "WhatsApp on the server is disconnected. "
            "Re-link under Settings → Connected Accounts."
        )
    if state == "error":
        err = str(status.get("error") or "").strip()
        base = "Could not reach the WhatsApp service on the server."
        return f"{base} {err}" if err else base
    return "WhatsApp on the server is not linked. Check Settings → Connected Accounts."


def enrich_sync_result_with_relay_status(result: Mapping[str, Any]) -> dict[str, Any]:
    """Attach relay link state so sync UIs can warn when Baileys is down."""
    out = dict(result)
    status = relay_whatsapp_live_status()
    out["relay_status"] = str(status.get("status") or "unknown")
    out["relay_link_ok"] = is_relay_whatsapp_connected(status)
    warning = relay_link_warning(status)
    if warning:
        out["warning"] = warning
    return out


def sync_whatsapp_from_relay(*, mark_processed: bool = False) -> dict[str, Any]:
    """Pull relay messages into the local DB (same path as the kanban web sync button)."""
    logger.info("WhatsApp relay sync starting (mark_processed=%s)", mark_processed)
    wa_manager = get_whatsapp_manager()
    if wa_manager:
        try:
            result = wa_manager.sync_from_relay(mark_processed=mark_processed) or {"synced": 0}
            logger.info("WhatsApp relay sync via manager finished: %s", result)
            return enrich_sync_result_with_relay_status(result)
        except Exception as exc:
            logger.error("WhatsApp relay sync via manager failed: %s", exc, exc_info=True)
            return enrich_sync_result_with_relay_status({"synced": 0, "error": str(exc)})

    from distr.core.integrations.whatsapp.relay_client import sync_messages_from_relay

    if not is_whatsapp_account_connected():
        return enrich_sync_result_with_relay_status({"synced": 0, "error": "WhatsApp not connected"})
    result = sync_messages_from_relay(mark_processed=mark_processed)
    logger.info("WhatsApp relay sync via headless client finished: %s", result)
    return enrich_sync_result_with_relay_status(result)


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
