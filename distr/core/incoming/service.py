"""Unified incoming communication view for the Development workspace.

This module deliberately reads the existing channel stores and orchestration
ledger. It does not introduce a second inbox database or copy message bodies.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

from distr.core.db import WhatsAppMessage, WhatsAppPhoneLink, get_session
from distr.core.db.kanban import KanbanBoard


def _iso_from_epoch(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _whatsapp_conversation_label(row: WhatsAppMessage, links: list[WhatsAppPhoneLink]) -> str:
    link = next((candidate for candidate in links if candidate.contact_name), None)
    if link and link.contact_name:
        return str(link.contact_name)
    is_group = str(row.chat_type or "").lower() == "group" or str(row.jid or "").endswith("@g.us")
    if is_group:
        try:
            raw = json.loads(row.raw_data or "{}")
        except (TypeError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            for key in ("group_name", "group_subject", "chat_name", "chat_subject", "subject", "name"):
                value = raw.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return str(row.jid or "WhatsApp group").split("@")[0]
    return str(row.sender_push_name or row.sender_phone or row.jid_phone or row.jid or "WhatsApp contact")


class _EmailTextParser(HTMLParser):
    """Small, dependency-free HTML email to readable text converter."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _email_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "<" not in raw or ">" not in raw:
        return raw
    parser = _EmailTextParser()
    try:
        parser.feed(raw)
        lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
        return "\n".join(line for line in lines if line).strip()
    except Exception:
        return raw


def _email_created_at(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return raw


def _load_gmail_inbox(*, max_results: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read a bounded slice of the connected Gmail inbox."""
    try:
        from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

        connector = GoogleWorkspaceConnector()
        if not connector.is_connected():
            return [], {"connected": False, "error": "Connect Gmail in Settings to load inbox threads."}
        rows = connector.check_inbox(max_results=max(1, min(int(max_results), 30)), query="in:inbox")
        if connector.last_error:
            return [], {"connected": False, "error": "Reconnect Gmail in Settings to load inbox threads."}
        return [row for row in (rows or []) if isinstance(row, dict)], {"connected": True, "error": ""}
    except Exception:
        return [], {"connected": False, "error": "Gmail is temporarily unavailable."}


def _load_mailshot_inbox(*, max_results: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read Tensology Mailshot using the existing connected account."""
    try:
        from distr.core.kanban.jira_intake import fetch_mailshot_intake_messages
        from distr.core.tensology_client import configured_tensology_client

        configured_tensology_client(source="decisionsai-development-incoming")
        rows = fetch_mailshot_intake_messages(
            limit=max(1, min(int(max_results), 30)),
            max_pages=1,
            jira_only=False,
        )
        return [row for row in rows if isinstance(row, dict)], {"connected": True, "error": ""}
    except Exception:
        return [], {"connected": False, "error": "Connect Tensology in Settings to load Mailshot."}


def list_development_incoming(*, limit: int = 100) -> dict[str, Any]:
    """Return incoming channel messages, board links, and channel health."""
    requested = max(1, min(int(limit or 100), 250))
    items: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []

    with get_session() as session:
        boards = {int(row.id): row.name for row in session.query(KanbanBoard).all()}
        link_rows = session.query(WhatsAppPhoneLink).order_by(WhatsAppPhoneLink.id.asc()).all()
        link_by_jid: dict[str, list[WhatsAppPhoneLink]] = {}
        link_by_phone: dict[str, list[WhatsAppPhoneLink]] = {}
        for row in link_rows:
            links.append({
                "id": int(row.id),
                "source": "whatsapp",
                "board_id": int(row.board_id),
                "board_name": boards.get(int(row.board_id), "Unknown board"),
                "source_thread_id": row.phone_jid or "",
                "source_address": row.phone_number or "",
                "label": row.contact_name or row.phone_number or row.phone_jid or "WhatsApp",
                "auto_snapshot": bool(row.auto_snapshot),
            })
            if row.phone_jid:
                link_by_jid.setdefault(str(row.phone_jid), []).append(row)
            if row.phone_number:
                link_by_phone.setdefault(str(row.phone_number), []).append(row)

        whatsapp_rows = (
            session.query(WhatsAppMessage)
            .order_by(WhatsAppMessage.whatsapp_timestamp.desc(), WhatsAppMessage.id.desc())
            .limit(requested)
            .all()
        )
        seen_message_ids = {int(row.id) for row in whatsapp_rows}
        linked_addresses = {str(row.phone_jid or row.phone_number or "").strip() for row in link_rows}
        for address in (value for value in linked_addresses if value):
            linked_rows = (
                session.query(WhatsAppMessage)
                .filter((WhatsAppMessage.jid == address) | (WhatsAppMessage.jid_phone == address))
                .order_by(WhatsAppMessage.whatsapp_timestamp.desc(), WhatsAppMessage.id.desc())
                .limit(30)
                .all()
            )
            for linked_row in linked_rows:
                if int(linked_row.id) not in seen_message_ids:
                    whatsapp_rows.append(linked_row)
                    seen_message_ids.add(int(linked_row.id))
        whatsapp_rows.sort(key=lambda row: (int(row.whatsapp_timestamp or 0), int(row.id)), reverse=True)
        for row in whatsapp_rows:
            row_links = link_by_jid.get(str(row.jid or "")) or link_by_phone.get(str(row.jid_phone or "")) or []
            link = row_links[0] if row_links else None
            serialized_links = [{
                "id": int(candidate.id),
                "board_id": int(candidate.board_id),
                "board_name": boards.get(int(candidate.board_id), "Unknown board"),
                "auto_snapshot": bool(candidate.auto_snapshot),
            } for candidate in row_links]
            text = str(row.text or row.caption or "").strip()
            if not text and row.media_type:
                text = f"{str(row.media_type).replace('_', ' ').title()} attachment"
            items.append({
                "key": f"whatsapp:{int(row.id)}",
                "source": "whatsapp",
                "source_label": "WhatsApp",
                "database_id": int(row.id),
                "source_message_id": row.message_id or str(row.id),
                "source_thread_id": row.jid or row.jid_phone or "",
                "conversation_label": _whatsapp_conversation_label(row, row_links),
                "chat_type": "group" if str(row.chat_type or "").lower() == "group" or str(row.jid or "").endswith("@g.us") else "private",
                "sender": row.sender_push_name or row.sender_phone or row.jid_phone or "WhatsApp",
                "text": text,
                "created_at": _iso_from_epoch(row.whatsapp_timestamp) or (row.created_date.isoformat() if row.created_date else ""),
                "processed": bool(row.processed),
                "snapshotted": bool(row.snapshot_group),
                "media_type": row.media_type or "",
                "media_mime_type": row.media_mime_type or "",
                "media_filename": row.media_filename or "",
                "media_duration": row.media_duration,
                "from_me": bool(row.from_me),
                "direction": "outbound" if row.from_me else "inbound",
                "links": serialized_links,
                "board_id": int(link.board_id) if link else None,
                "board_name": boards.get(int(link.board_id), "") if link else "",
                "link_id": int(link.id) if link else None,
                "auto_snapshot": bool(link.auto_snapshot) if link else False,
                "can_snapshot": bool(link and not row.processed),
            })

    gmail_message_ids: set[str] = set()
    gmail_rows, gmail_status = _load_gmail_inbox(max_results=requested)
    for row in gmail_rows:
        message_id = str(row.get("id") or "").strip()
        thread_id = str(row.get("threadId") or row.get("thread_id") or message_id).strip()
        subject = str(row.get("subject") or "").strip() or "(No subject)"
        sender = str(row.get("from") or "").strip() or "Unknown sender"
        body = _email_text(row.get("body")) or str(row.get("snippet") or "").strip()
        labels = [str(label) for label in (row.get("labels") or [])]
        if message_id:
            gmail_message_ids.add(message_id)
        items.append({
            "key": f"gmail:{message_id or thread_id}",
            "source": "gmail",
            "source_label": "Gmail",
            "source_message_id": message_id,
            "source_thread_id": thread_id,
            "conversation_label": subject,
            "thread_label": subject,
            "chat_type": "thread",
            "sender": sender,
            "recipient": str(row.get("to") or "").strip(),
            "subject": subject,
            "text": body,
            "snippet": str(row.get("snippet") or "").strip(),
            "created_at": _email_created_at(row.get("date")),
            "processed": "UNREAD" not in labels,
            "unread": "UNREAD" in labels,
            "labels": labels,
            "attachments": row.get("attachments") or [],
            "board_id": None,
            "board_name": "",
            "can_snapshot": False,
        })

    mailshot_rows, mailshot_status = _load_mailshot_inbox(max_results=requested)
    for row in mailshot_rows:
        message_id = str(row.get("id") or "").strip()
        thread_id = str(row.get("thread_id") or message_id).strip()
        subject = str(row.get("subject") or "").strip() or "(No subject)"
        labels = [str(label) for label in (row.get("labels") or [])]
        items.append({
            "key": f"mailshot:{message_id or thread_id}",
            "source": "mailshot",
            "source_label": "Mailshot",
            "source_message_id": message_id,
            "source_thread_id": thread_id,
            "conversation_label": subject,
            "thread_label": subject,
            "chat_type": "thread",
            "sender": str(row.get("from") or "").strip() or "Unknown sender",
            "recipient": str(row.get("to") or "").strip(),
            "subject": subject,
            "text": _email_text(row.get("body")) or str(row.get("snippet") or "").strip(),
            "snippet": str(row.get("snippet") or "").strip(),
            "created_at": _email_created_at(row.get("date")),
            "processed": bool(row.get("read")),
            "unread": not bool(row.get("read")),
            "labels": labels,
            "attachments": row.get("attachments") or [],
            "board_id": None,
            "board_name": "",
            "can_snapshot": False,
        })

    # Preserve Gmail items already normalized by WorkIntake, without duplicating
    # messages returned directly by the connected Gmail inbox.
    try:
        from distr.core.work_intake import get_work_intake_service

        for row in get_work_intake_service().list_inbox(limit=requested):
            source = str(row.get("source") or "unknown").lower()
            if source != "gmail":
                continue
            if str(row.get("source_message_id") or "") in gmail_message_ids:
                continue
            items.append({
                "key": f"intake:{row.get('event_id')}",
                "event_id": row.get("event_id"),
                "source": source,
                "source_label": "Gmail",
                "sender": row.get("source_contact") or row.get("source_user_id") or "",
                "source_thread_id": row.get("source_thread_id") or row.get("source_user_id") or row.get("source_contact") or str(row.get("event_id") or ""),
                "conversation_label": row.get("subject") or row.get("source_contact") or row.get("source_user_id") or "(No subject)",
                "subject": row.get("subject") or "(No subject)",
                "chat_type": "thread",
                "text": row.get("text") or row.get("response_text") or "",
                "created_at": row.get("created_at") or "",
                "processed": False,
                "status": row.get("status") or "triaged",
                "action": row.get("action") or "",
                "ticket_id": row.get("ticket_id"),
                "workflow_id": row.get("workflow_id"),
                "workflow_run_id": row.get("workflow_run_id"),
                "board_id": row.get("board_id"),
                "project_id": row.get("project_id"),
                "needs_attention": bool(row.get("needs_attention")),
                "can_snapshot": False,
            })
    except Exception:
        pass

    items.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    counts: dict[str, int] = {}
    for row in items:
        source = str(row.get("source") or "unknown")
        counts[source] = counts.get(source, 0) + 1
    return {
        "items": items,
        "links": links,
        "counts": counts,
        "channels": {"gmail": gmail_status, "mailshot": mailshot_status},
    }
