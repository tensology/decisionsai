"""Headless WhatsApp relay REST client (no Qt / WebSocket manager required).

Used by the agent subprocess and other code paths that cannot access
``QApplication.instance().whatsapp_manager``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from distr.core.integrations.telegram.utils import relay_internal_token

logger = logging.getLogger(__name__)

_DEVICE_IDENTITY_PATH = Path.home() / ".decisions" / "device_identity.json"
_cached_relay_headers: dict[str, str] | None = None
_cached_relay_headers_at: float = 0.0
_RELAY_HEADERS_TTL_SECONDS = 300.0


def relay_api_base() -> str:
    explicit = str(os.environ.get("DECISIONSAI_WA_API_BASE") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    use_local = str(os.environ.get("DECISIONSAI_USE_LOCAL_RELAY", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if use_local:
        return "http://localhost:8090/api/whatsapp"
    return "https://www.decisionsai.net/api/whatsapp"


def load_or_create_device_identity() -> dict[str, Any]:
    try:
        if _DEVICE_IDENTITY_PATH.exists():
            obj = json.loads(_DEVICE_IDENTITY_PATH.read_text(encoding="utf-8"))
            if obj.get("device_id") and obj.get("private_key"):
                return obj
    except Exception:
        pass
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ident = {
        "device_id": f"dev-{int(time.time())}-{secrets.token_hex(8)}",
        "private_key": base64.b64encode(priv_raw).decode(),
    }
    _DEVICE_IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DEVICE_IDENTITY_PATH.write_text(json.dumps(ident), encoding="utf-8")
    return ident


def relay_auth_headers(*, ws_token: str = "") -> dict[str, str]:
    token = relay_internal_token()
    if token:
        return {"X-Relay-Internal-Token": token}
    ws_token = (ws_token or "").strip()
    if ws_token:
        return {"Authorization": f"Bearer {ws_token}"}
    return {}


def fetch_ws_token(*, subscribe_phones: Optional[list[str]] = None) -> str:
    """Return a short-lived relay bearer token (internal secret or device challenge)."""
    phones = list(subscribe_phones or [])
    api_base = relay_api_base()
    payload = {"app_user_id": "local-ui", "subscribe_phones": phones}
    payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    headers = relay_auth_headers()
    if headers:
        try:
            resp = requests.post(f"{api_base}/ws-auth", json=payload, headers=headers, timeout=10)
            obj = resp.json()
            if resp.status_code == 200 and obj.get("success") and obj.get("ws_token"):
                return str(obj["ws_token"])
        except Exception as exc:
            logger.debug("WhatsApp relay ws-auth (internal) failed: %s", exc)

    try:
        ident = load_or_create_device_identity()
        priv_raw = base64.b64decode(str(ident.get("private_key") or "").encode())
        priv = Ed25519PrivateKey.from_private_bytes(priv_raw)
        pub_raw = priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        ch = requests.post(
            f"{api_base}/device/challenge",
            json={
                "device_id": ident.get("device_id"),
                "public_key": base64.b64encode(pub_raw).decode(),
            },
            timeout=10,
        )
        ch_obj = ch.json()
        if ch.status_code != 200 or not ch_obj.get("success"):
            return ""
        msg = str(ch_obj.get("challenge_message") or "")
        sig = priv.sign(msg.encode())
        resp = requests.post(
            f"{api_base}/device/ws-auth",
            json={
                "device_id": ident.get("device_id"),
                "challenge_id": ch_obj.get("challenge_id"),
                "signature": base64.b64encode(sig).decode(),
                "subscribe_phones": phones,
            },
            timeout=10,
        )
        obj = resp.json()
        if resp.status_code == 200 and obj.get("success") and obj.get("ws_token"):
            return str(obj["ws_token"])
    except Exception as exc:
        logger.debug("WhatsApp relay device ws-auth failed: %s", exc)
    return ""


def relay_request_headers(*, force_refresh: bool = False) -> dict[str, str]:
    global _cached_relay_headers, _cached_relay_headers_at
    if (
        not force_refresh
        and _cached_relay_headers
        and (time.time() - _cached_relay_headers_at) < _RELAY_HEADERS_TTL_SECONDS
    ):
        return dict(_cached_relay_headers)

    headers = relay_auth_headers()
    if not headers:
        ws_token = fetch_ws_token()
        headers = relay_auth_headers(ws_token=ws_token)
    _cached_relay_headers = dict(headers)
    _cached_relay_headers_at = time.time()
    return dict(headers)


def mark_relay_processed(relay_id: int, *, headers: dict[str, str] | None = None) -> bool:
    if not relay_id:
        return False
    try:
        resp = requests.post(
            f"{relay_api_base()}/messages/{relay_id}/processed",
            headers=headers or relay_request_headers(),
            timeout=5,
        )
        return 200 <= int(resp.status_code) < 300
    except Exception as exc:
        logger.debug("WhatsApp relay mark processed failed for %s: %s", relay_id, exc)
        return False


def sync_messages_from_relay(*, mark_processed: bool = True) -> dict[str, Any]:
    """Pull relay messages into the local WhatsAppMessage table."""
    try:
        headers = relay_request_headers()
        unprocessed_only = "true" if mark_processed else "false"
        resp = requests.get(
            f"{relay_api_base()}/messages",
            params={"limit": 1000, "unprocessed_only": unprocessed_only},
            headers=headers,
            timeout=15,
        )
        data = resp.json()
        messages = list(data.get("messages") or [])
        synced = 0
        relay_ids_to_mark: list[int] = []

        from distr.core.db import WhatsAppMessage, get_session

        with get_session() as session:
            for msg in messages:
                existing = session.query(WhatsAppMessage).filter_by(
                    message_id=msg.get("message_id", "")
                ).first()
                if existing:
                    if not (existing.raw_data or "").strip():
                        try:
                            existing.raw_data = json.dumps(msg, default=str)
                        except Exception:
                            pass
                    if not (existing.sender_push_name or "").strip():
                        existing.sender_push_name = msg.get("sender_push_name", "") or existing.sender_push_name
                    if not (existing.chat_type or "").strip():
                        existing.chat_type = msg.get("chat_type", "private") or existing.chat_type
                    if not (existing.jid or "").strip():
                        existing.jid = msg.get("jid", "") or existing.jid
                    if not (existing.jid_phone or "").strip():
                        existing.jid_phone = msg.get("jid_phone", "") or existing.jid_phone
                    if mark_processed and not (
                        msg.get("media_type") and not (existing.media_local_path or "").strip()
                    ):
                        relay_id = msg.get("id")
                        if relay_id is not None:
                            relay_ids_to_mark.append(int(relay_id))
                    continue

                relay_media_path = msg.get("media_local_path")
                local_media_path = None
                if relay_media_path:
                    relay_media_path = str(relay_media_path)
                    if Path(relay_media_path).is_absolute() and os.path.exists(relay_media_path):
                        local_media_path = relay_media_path

                row = WhatsAppMessage(
                    message_id=msg.get("message_id", ""),
                    jid=msg.get("jid", ""),
                    jid_phone=msg.get("jid_phone", ""),
                    chat_type=msg.get("chat_type", "private"),
                    sender_jid=msg.get("sender_jid", ""),
                    sender_phone=msg.get("sender_phone", ""),
                    sender_push_name=msg.get("sender_push_name", ""),
                    text=msg.get("text"),
                    caption=msg.get("caption"),
                    media_type=msg.get("media_type"),
                    media_mime_type=msg.get("media_mime_type"),
                    media_filename=msg.get("media_filename"),
                    media_local_path=local_media_path,
                    media_file_length=msg.get("media_file_length"),
                    whatsapp_timestamp=msg.get("whatsapp_timestamp"),
                    from_me=msg.get("from_me", False),
                    raw_data=json.dumps(msg, default=str),
                    processed=True,
                )
                session.add(row)
                synced += 1
                if mark_processed and not (msg.get("media_type") and not (local_media_path or "").strip()):
                    relay_id = msg.get("id")
                    if relay_id is not None:
                        relay_ids_to_mark.append(int(relay_id))

            session.commit()

        if mark_processed and relay_ids_to_mark:
            for relay_id in relay_ids_to_mark:
                mark_relay_processed(relay_id, headers=headers)

        logger.info("WhatsApp relay sync: synced=%s total=%s", synced, len(messages))
        return {"synced": synced, "total": len(messages)}
    except Exception as exc:
        logger.error("WhatsApp relay sync failed: %s", exc, exc_info=True)
        return {"synced": 0, "total": 0, "error": str(exc)}


def send_message_via_relay(*, jid: str, text: str, caption: str = "") -> dict[str, Any]:
    payload = {"jid": jid, "text": text, "caption": caption or "", "audio": None}
    headers = relay_request_headers()
    try:
        resp = requests.post(
            f"{relay_api_base()}/send",
            json=payload,
            headers=headers,
            timeout=10,
        )
        try:
            data = resp.json()
        except Exception:
            data = {"raw": (resp.text or "")[:300]}
        if resp.status_code == 200 and data.get("success", True):
            return {"success": True, "jid": jid}
        return {"success": False, "error": data.get("error") or data, "status_code": resp.status_code}
    except Exception as exc:
        logger.error("WhatsApp relay send failed: %s", exc, exc_info=True)
        return {"success": False, "error": str(exc)}


def list_chats_from_relay(*, limit: int = 100, offset: int = 0, search: str = "") -> dict[str, Any]:
    try:
        resp = requests.get(
            f"{relay_api_base()}/chats",
            params={"limit": limit, "offset": offset, "search": search or ""},
            headers=relay_request_headers(),
            timeout=10,
        )
        return resp.json() if resp.status_code == 200 else {"chats": [], "error": resp.text[:200]}
    except Exception as exc:
        return {"chats": [], "error": str(exc)}
