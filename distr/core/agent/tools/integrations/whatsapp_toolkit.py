"""Agent-facing WhatsApp toolkit.

Read and draft operations are intentionally separate from the outbound send
operation.  A draft produces a short-lived-in-practice approval token derived
from the exact draft text.  Sending requires that token, ``approved=True``,
and an explicit approval phrase, so an agent cannot turn a request to write a
message into an outbound message by accident.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


_APPROVAL_PHRASES = {"approved", "send", "send it", "go ahead", "yes send"}


def _approval_token(jid_phone: str, text: str) -> str:
    payload = f"{jid_phone.strip()}\0{text.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _approval_is_explicit(approval_phrase: str) -> bool:
    normalized = re.sub(r"\s+", " ", (approval_phrase or "").strip().lower())
    return normalized in _APPROVAL_PHRASES


def _message_body(message) -> str:
    text = (getattr(message, "text", None) or "").strip()
    caption = (getattr(message, "caption", None) or "").strip()
    if text and caption:
        return f"{text}\n{caption}"
    return text or caption


def _message_dict(message, include_media: bool) -> dict[str, Any]:
    timestamp = getattr(message, "whatsapp_timestamp", None)
    iso_timestamp = None
    if timestamp:
        try:
            iso_timestamp = datetime.fromtimestamp(int(timestamp)).isoformat()
        except (TypeError, ValueError, OSError):
            iso_timestamp = str(timestamp)
    media_path = (getattr(message, "media_local_path", None) or "").strip()
    if media_path:
        try:
            from distr.core.integrations.whatsapp.paths import resolve_whatsapp_media_disk_path

            resolved = resolve_whatsapp_media_disk_path(media_path)
            if resolved:
                media_path = resolved
        except Exception:
            pass
    return {
        "id": int(message.id),
        "message_id": message.message_id,
        "jid": message.jid,
        "jid_phone": message.jid_phone,
        "sender": message.sender_push_name or message.sender_phone or message.sender_jid,
        "text": _message_body(message),
        "media_type": message.media_type,
        "media_filename": message.media_filename,
        "timestamp": iso_timestamp,
        "from_me": bool(message.from_me),
        "media_path": media_path if include_media and media_path else None,
        "media_available": bool(media_path),
    }


def _resolve_contact(query: str, jid_phone: str = "") -> tuple[str, str, str]:
    """Return (phone, jid, display name) from the local message cache."""
    from distr.core.db import WhatsAppMessage, get_session

    with get_session() as session:
        if jid_phone.strip():
            phone = jid_phone.strip().split("@", 1)[0]
            row = (
                session.query(WhatsAppMessage)
                .filter(WhatsAppMessage.jid_phone == phone)
                .order_by(WhatsAppMessage.whatsapp_timestamp.desc())
                .first()
            )
            if not row:
                return phone, f"{phone}@s.whatsapp.net", query.strip() or phone
        else:
            needle = f"%{query.strip()}%"
            row = (
                session.query(WhatsAppMessage)
                .filter(
                    (WhatsAppMessage.sender_push_name.ilike(needle))
                    | (WhatsAppMessage.sender_phone.ilike(needle))
                    | (WhatsAppMessage.jid_phone.ilike(needle))
                )
                .order_by(WhatsAppMessage.whatsapp_timestamp.desc())
                .first()
            )
        if not row:
            return "", "", ""
        phone = (row.jid_phone or row.jid or "").split("@", 1)[0]
        return phone, row.jid or f"{phone}@s.whatsapp.net", row.sender_push_name or query.strip()


class WhatsAppToolkitInput(BaseModel):
    action: str = Field(
        default="search",
        description=(
            "search (find contacts/messages), read (return a conversation with media paths), "
            "analyze_media (read cached image media with vision), draft (save an unsent reply), "
            "send (send only an explicitly approved draft), or status."
        ),
    )
    query: str = Field(default="", description="Contact name, phone number, message text, or search term.")
    jid_phone: str = Field(default="", description="WhatsApp phone number or JID when already known.")
    text: str = Field(default="", description="Exact reply text for draft/send actions.")
    prompt: str = Field(default="", description="Question to ask about attached image media.")
    include_media: bool = Field(default=True, description="Include cached local media paths in read results.")
    approved: bool = Field(default=False, description="Must be true for an outbound send.")
    approval_token: str = Field(default="", description="Token returned by the draft action.")
    approval_phrase: str = Field(default="", description="Fresh approval: approved, send, send it, go ahead, or yes send.")


class WhatsAppToolkitTool(BaseTool):
    name: str = "whatsapp_toolkit"
    description: str = (
        "Use the Decisions AI WhatsApp toolkit for repeatable contact and message work. "
        "Search or read WhatsApp chats and cached screenshots/media, analyze image media, "
        "and prepare an unsent reply draft. Never send from search/read/draft. "
        "To send, first show the exact draft, then require the returned approval_token, "
        "approved=true, and a fresh explicit approval_phrase from the user. "
        "Use the existing send_voice_note_to_telegram and speak_on_desktop tools for Telegram voice or desktop speech."
    )
    args_schema: type[BaseModel] = WhatsAppToolkitInput

    def _sync(self) -> dict[str, Any]:
        from distr.core.kanban.whatsapp_relay_sync import sync_whatsapp_from_relay

        return sync_whatsapp_from_relay(mark_processed=False)

    def _search_messages(self, query: str, jid_phone: str, include_media: bool, limit: int = 50) -> list[dict[str, Any]]:
        from distr.core.db import WhatsAppMessage, get_session

        with get_session() as session:
            filters = []
            if jid_phone.strip():
                filters.append(WhatsAppMessage.jid_phone == jid_phone.strip().split("@", 1)[0])
            if query.strip():
                needle = f"%{query.strip()}%"
                filters.append(
                    (WhatsAppMessage.sender_push_name.ilike(needle))
                    | (WhatsAppMessage.sender_phone.ilike(needle))
                    | (WhatsAppMessage.text.ilike(needle))
                    | (WhatsAppMessage.caption.ilike(needle))
                    | (WhatsAppMessage.jid_phone.ilike(needle))
                )
            statement = session.query(WhatsAppMessage)
            if filters:
                from sqlalchemy import and_

                statement = statement.filter(and_(*filters))
            rows = (
                statement.order_by(WhatsAppMessage.whatsapp_timestamp.desc(), WhatsAppMessage.id.desc())
                .limit(max(1, min(int(limit), 200)))
                .all()
            )
            return [_message_dict(row, include_media) for row in rows]

    def _run(
        self,
        action: str = "search",
        query: str = "",
        jid_phone: str = "",
        text: str = "",
        prompt: str = "",
        include_media: bool = True,
        approved: bool = False,
        approval_token: str = "",
        approval_phrase: str = "",
        **kwargs: Any,
    ) -> str:
        action_name = (action or "search").strip().lower().replace("-", "_").replace(" ", "_")
        if action_name in {"status", "connection_status"}:
            from distr.core.integrations.whatsapp.relay_client import fetch_relay_whatsapp_status

            return json.dumps(fetch_relay_whatsapp_status(), ensure_ascii=False, indent=2)

        if action_name in {"search", "read", "conversation", "ingest"}:
            sync_result = self._sync()
            rows = self._search_messages(query, jid_phone, include_media)
            return json.dumps({"action": action_name, "sync": sync_result, "messages": rows}, ensure_ascii=False, indent=2, default=str)

        if action_name in {"analyze_media", "inspect_media"}:
            rows = self._search_messages(query, jid_phone, True)
            media_rows = [
                row for row in rows
                if row.get("media_path")
                and (
                    str(row.get("media_type") or "").lower() in {"photo", "image"}
                    or Path(str(row["media_path"])).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".heic", ".heif"}
                )
            ]
            if not media_rows:
                return "No cached WhatsApp image media was found for that contact or query."
            from distr.core.agent.tools.vision.vision_analyzer import VisionAnalyzerTool

            analyzer = VisionAnalyzerTool()
            question = prompt.strip() or "Read this WhatsApp screenshot carefully. Transcribe visible text and explain what it shows."
            analyses = []
            for row in media_rows:
                result = analyzer._run(prompt=question, file_path=row["media_path"])
                analyses.append({"message": row, "analysis": result})
            return json.dumps({"action": "analyze_media", "items": analyses}, ensure_ascii=False, indent=2, default=str)

        if action_name in {"draft", "prepare_draft", "write_reply"}:
            phone, jid, contact = _resolve_contact(query, jid_phone)
            if not phone:
                return "I could not resolve a WhatsApp contact. Provide the contact name or phone number."
            if not text.strip():
                return "Draft action requires the exact reply text."
            from distr.core.kanban.whatsapp_compose_drafts import save_compose_draft

            draft = save_compose_draft(
                jid_phone=phone,
                jid=jid,
                contact_name=contact,
                text=text,
                source="agent",
                sanitize=True,
            )
            token = _approval_token(phone, draft["text"])
            return json.dumps(
                {"action": "draft", "draft": draft, "approval_token": token, "send_requires_explicit_approval": True},
                ensure_ascii=False,
                indent=2,
            )

        if action_name in {"send", "send_approved", "send_reply"}:
            if not approved or not _approval_is_explicit(approval_phrase):
                return "Not sent. This action requires approved=true and a fresh explicit approval_phrase."
            phone, jid, contact = _resolve_contact(query, jid_phone)
            if not phone:
                return "Not sent. I could not resolve the WhatsApp contact."
            from distr.core.kanban.whatsapp_compose_drafts import get_compose_draft, delete_compose_draft

            draft = get_compose_draft(phone)
            if not draft:
                return "Not sent. There is no saved WhatsApp draft for that contact."
            expected = _approval_token(phone, draft["text"])
            if not approval_token.strip() or approval_token.strip() != expected:
                return "Not sent. The approval token does not match the current draft."
            from distr.core.integrations.whatsapp.relay_client import send_message_via_relay

            result = send_message_via_relay(jid=jid or draft.get("jid") or f"{phone}@s.whatsapp.net", text=draft["text"])
            if result.get("success"):
                delete_compose_draft(phone)
            return json.dumps({"action": "send", "contact": contact, "result": result}, ensure_ascii=False, indent=2, default=str)

        return f"Unknown WhatsApp toolkit action: {action_name}"

    async def _arun(self, **kwargs: Any) -> str:
        return self._run(**kwargs)
