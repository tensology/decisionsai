"""Unified incoming communication view for the Development workspace.

This module deliberately reads the existing channel stores and orchestration
ledger. It does not introduce a second inbox database or copy message bodies.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
from html.parser import HTMLParser
from typing import Any

from distr.core.db import TelegramGroupMessage, WhatsAppMessage, WhatsAppPhoneLink, get_session
from distr.core.db.kanban import KanbanBoard


def _iso_from_epoch(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _whatsapp_source_sequence(row: WhatsAppMessage) -> int | None:
    """Relay ids retain arrival order when a newest-first batch is imported."""
    try:
        raw = json.loads(row.raw_data or "{}")
        value = raw.get("id") if isinstance(raw, dict) else None
        sequence = int(value) if value is not None and not isinstance(value, bool) else 0
        return sequence if sequence > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


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


def _whatsapp_incoming_item(row: WhatsAppMessage, row_links: list[WhatsAppPhoneLink], boards: dict[int, str]) -> dict[str, Any]:
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
    return {
        "key": f"whatsapp:{int(row.id)}",
        "source": "whatsapp",
        "source_label": "WhatsApp",
        "database_id": int(row.id),
        "source_sequence": _whatsapp_source_sequence(row),
        "source_message_id": row.message_id or str(row.id),
        "source_thread_id": row.jid or row.jid_phone or "",
        "conversation_label": _whatsapp_conversation_label(row, row_links),
        "chat_type": "group" if str(row.chat_type or "").lower() == "group" or str(row.jid or "").endswith("@g.us") else "private",
        "sender": row.sender_push_name or row.sender_phone or row.jid_phone or "WhatsApp",
        "text": text,
        "created_at": _iso_from_epoch(row.whatsapp_timestamp) or (row.created_date.isoformat() if row.created_date else ""),
        "source_sent_at": _iso_from_epoch(row.whatsapp_timestamp),
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
    }


def list_whatsapp_history(thread_id: str, before_id: int = 0, limit: int = 40) -> dict[str, Any]:
    """Read a page for the same conversation identity used by Incoming."""
    from sqlalchemy import and_, func, or_

    timestamp = func.coalesce(WhatsAppMessage.whatsapp_timestamp, 0)
    with get_session() as session:
        rows = session.query(WhatsAppMessage).filter(or_(
            WhatsAppMessage.jid == thread_id,
            and_(or_(WhatsAppMessage.jid.is_(None), WhatsAppMessage.jid == ""),
                 WhatsAppMessage.jid_phone == thread_id),
        ))
        if before_id:
            anchor = rows.filter(WhatsAppMessage.id == before_id).first()
            if anchor is None:
                raise LookupError("This WhatsApp history cursor is no longer available.")
            sent_at = anchor.whatsapp_timestamp or 0
            rows = rows.filter(or_(timestamp < sent_at, and_(timestamp == sent_at, WhatsAppMessage.id < before_id)))
        page = rows.order_by(timestamp.desc(), WhatsAppMessage.id.desc()).limit(limit + 1).all()
        return {
            "items": [_whatsapp_incoming_item(row, [], {}) for row in page[:limit]],
            "has_older": len(page) > limit,
        }


def telegram_incoming_item(row: TelegramGroupMessage) -> dict[str, Any]:
    sender = ""
    try:
        payload = json.loads(row.sender_data or "{}")
        if isinstance(payload, dict):
            sender = (
                str(payload.get("username") or "").strip()
                or " ".join(
                    part for part in (
                        str(payload.get("first_name") or "").strip(),
                        str(payload.get("last_name") or "").strip(),
                    ) if part
                ).strip()
                or str(payload.get("id") or "")
            )
    except Exception:
        sender = ""
    body = str(row.text or row.caption or "").strip()
    if not body and row.media_type:
        body = f"[{row.media_type} message]"
    return {
        "key": f"telegram:{row.id}",
        "source": "telegram",
        "source_label": "Telegram",
        "source_message_id": str(row.telegram_message_id or row.id),
        "source_thread_id": str(row.chat_id or ""),
        "conversation_label": str(row.chat_title or row.chat_username or row.chat_id or "Telegram"),
        "thread_label": str(row.chat_title or row.chat_username or row.chat_id or "Telegram"),
        "chat_type": str(row.chat_type or "group"),
        "sender": sender or "Unknown sender",
        "text": body,
        "snippet": body[:240],
        "created_at": (
            row.created_date.isoformat()
            if getattr(row, "created_date", None) is not None
            else _iso_from_epoch(row.telegram_date)
        ),
        "processed": bool(getattr(row, "processed", False)),
        "unread": not bool(getattr(row, "processed", False)),
        "board_id": None,
        "board_name": "",
        "can_snapshot": False,
        "database_id": int(row.id),
    }


class _EmailTextParser(HTMLParser):
    """Small, dependency-free HTML email to readable text converter."""

    _SKIP = frozenset({"script", "style", "head", "title", "noscript", "template"})
    _BREAK = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if name in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if name in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.parts.append(data)


def _email_text(value: Any) -> str:
    """Strip tags/entities from email HTML into readable plain text."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "<" not in raw or ">" not in raw:
        return html.unescape(raw)
    parser = _EmailTextParser()
    try:
        parser.feed(raw)
        lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
        return "\n".join(line for line in lines if line).strip()
    except Exception:
        return html.unescape(raw)


def _email_list_snippet(*parts: Any) -> str:
    """One-line plain preview for Incoming mail list rows (Gmail + Mailshot)."""
    for part in parts:
        plain = _email_text(part)
        if plain:
            return " ".join(plain.split())
    return ""


def _mail_plain_and_html(row: dict[str, Any]) -> tuple[str, str, str]:
    """Shared Gmail/Mailshot field shaping: (text, html_body, snippet)."""
    raw_body = str(row.get("body") or "")
    html_body = str(row.get("html_body") or "").strip()
    if not html_body and "<" in raw_body and ">" in raw_body and (
        "</" in raw_body or "<br" in raw_body.lower() or "<div" in raw_body.lower()
    ):
        html_body = raw_body
    explicit_text = str(row.get("text_body") or "").strip()
    text = (
        _email_text(explicit_text or raw_body)
        or _email_text(html_body)
        or _email_text(row.get("snippet") or "")
    )
    snippet = _email_list_snippet(row.get("snippet"), explicit_text or raw_body, html_body, text)
    return text, html_body, snippet


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



def _mail_attachment_preview_url(
    attachment: dict[str, Any],
    *,
    message_id: str = "",
    provider: str = "",
) -> str:
    """Build a same-origin preview URL for an Incoming mail attachment (session auth)."""
    if not isinstance(attachment, dict):
        return ""
    mid = str(attachment.get("message_id") or message_id or "").strip()
    aid = str(attachment.get("attachment_id") or "").strip()
    cid = str(attachment.get("content_id") or "").strip().strip("<>")
    if not mid or (not aid and not cid):
        return ""
    from urllib.parse import urlencode
    params = {"message_id": mid}
    if aid:
        params["attachment_id"] = aid
    if cid:
        params["content_id"] = cid
    provider_key = str(provider or attachment.get("provider") or "").strip().lower()
    if provider_key:
        params["provider"] = provider_key
    elif mid.startswith("email-"):
        params["provider"] = "mailshot"
    return f"/api/workflows/studio/incoming/mail/attachment?{urlencode(params)}"


def _with_mail_preview_urls(
    attachments: Any,
    *,
    message_id: str = "",
    provider: str = "",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        mid = str(row.get("message_id") or message_id or "").strip()
        if mid and not row.get("message_id"):
            row["message_id"] = mid
        if provider and not row.get("provider"):
            row["provider"] = provider
        preview = _mail_attachment_preview_url(row, message_id=mid, provider=provider)
        if preview:
            row["preview_url"] = preview
        out.append(row)
    return out

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


def ingest_gmail_as_work(
    *,
    subject: str,
    body: str,
    message_id: str = "",
    thread_id: str = "",
    sender: str = "",
    board_hint: str = "",
    project_hint: str = "",
    force_ticket: bool = False,
) -> dict[str, Any]:
    """Create a WorkIntake(source=gmail) decision, mirroring Telegram channel intake."""
    from distr.core.work_intake import WorkIntake, get_work_intake_service

    text_body = str(body or "").strip() or str(subject or "").strip() or "Incoming Gmail message"
    subject_clean = str(subject or "").strip()
    if force_ticket and not text_body.lower().startswith(("create a ticket", "create ticket")):
        user_text = f"Create a ticket: {subject_clean + ' — ' if subject_clean else ''}{text_body}"
    else:
        user_text = text_body if not subject_clean else f"{subject_clean}\n\n{text_body}"
    decision = get_work_intake_service().ingest(
        WorkIntake(
            source="gmail",
            user_text=user_text,
            source_user_id=str(sender or ""),
            source_thread_id=str(thread_id or message_id or ""),
            source_message_id=str(message_id or ""),
            board_hint=str(board_hint or ""),
            project_hint=str(project_hint or ""),
            metadata={"subject": subject_clean, "channel": "gmail"},
        )
    )
    return decision.to_dict()


def _load_mailshot_inbox(*, max_results: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read Tensology Mailshot using the existing connected account."""
    from distr.core.kanban.jira_intake import fetch_mailshot_intake_messages
    from distr.core.tensology_client import TensologyApiError

    error = "Mailshot could not be refreshed. Try again."
    error_code = "refresh_failed"
    try:
        rows = fetch_mailshot_intake_messages(
            limit=max(1, min(int(max_results), 30)),
            max_pages=1,
            jira_only=False,
            hydrate=True,
            strict=True,
            source="decisionsai-development-incoming",
        )
    except TensologyApiError as exc:
        if exc.code == "not_configured":
            error = "Connect Tensology in Settings to load Mailshot."
            error_code = "connection_required"
        elif exc.status in (401, 403):
            error = "Reconnect Tensology in Settings to load Mailshot."
            error_code = "connection_required"
    except Exception:
        pass
    else:
        return rows, {"connected": True, "error": ""}
    return [], {"connected": False, "error": error, "error_code": error_code}


def list_development_incoming(*, limit: int = 100, network: bool = True) -> dict[str, Any]:
    """Return incoming channel messages, board links, and channel health.

    ``network=False`` skips the slow remote channels (Gmail, Mailshot) and returns
    only the locally-stored channels. Proposals use this so the list is fast.
    """
    requested = max(1, min(int(limit or 100), 250))
    items: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []

    with get_session() as session:
        from distr.core.kanban.whatsapp_jid import prefer_whatsapp_jid, whatsapp_jid_match_keys

        boards = {int(row.id): row.name for row in session.query(KanbanBoard).all()}
        link_rows = session.query(WhatsAppPhoneLink).order_by(WhatsAppPhoneLink.id.asc()).all()
        link_by_key: dict[str, list[WhatsAppPhoneLink]] = {}
        preferred_thread_by_link_id: dict[int, str] = {}
        for row in link_rows:
            keys = whatsapp_jid_match_keys(row.phone_jid, row.phone_number)
            observed = (
                session.query(WhatsAppMessage.jid)
                .filter(
                    (WhatsAppMessage.jid.in_(keys))
                    | (WhatsAppMessage.jid_phone.in_(keys))
                )
                .order_by(WhatsAppMessage.whatsapp_timestamp.desc(), WhatsAppMessage.id.desc())
                .limit(1)
                .scalar()
            )
            thread_id = prefer_whatsapp_jid(row.phone_jid, observed) or (row.phone_jid or "")
            preferred_thread_by_link_id[int(row.id)] = thread_id
            links.append({
                "id": int(row.id),
                "source": "whatsapp",
                "board_id": int(row.board_id),
                "board_name": boards.get(int(row.board_id), "Unknown board"),
                "source_thread_id": thread_id,
                "source_address": row.phone_number or "",
                "label": row.contact_name or row.phone_number or row.phone_jid or "WhatsApp",
                "auto_snapshot": bool(row.auto_snapshot),
            })
            for key in keys:
                link_by_key.setdefault(key, []).append(row)

        whatsapp_rows = (
            session.query(WhatsAppMessage)
            .order_by(WhatsAppMessage.whatsapp_timestamp.desc(), WhatsAppMessage.id.desc())
            .limit(requested)
            .all()
        )
        seen_message_ids = {int(row.id) for row in whatsapp_rows}
        linked_key_set: set[str] = set()
        for row in link_rows:
            linked_key_set.update(whatsapp_jid_match_keys(row.phone_jid, row.phone_number))
        for address in (value for value in linked_key_set if value):
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
            row_links = (
                link_by_key.get(str(row.jid or ""))
                or link_by_key.get(str(row.jid_phone or ""))
                or []
            )
            items.append(_whatsapp_incoming_item(row, row_links, boards))

    gmail_items_by_id: dict[str, dict[str, Any]] = {}
    gmail_rows, gmail_status = _load_gmail_inbox(max_results=requested) if network else ([], {"connected": False, "error": "skipped"})
    for row in gmail_rows:
        message_id = str(row.get("id") or "").strip()
        thread_id = str(row.get("threadId") or row.get("thread_id") or message_id).strip()
        subject = str(row.get("subject") or "").strip() or "(No subject)"
        sender = str(row.get("from") or "").strip() or "Unknown sender"
        text_body, html_body, snippet = _mail_plain_and_html(row)
        labels = [str(label) for label in (row.get("labels") or [])]
        gmail_item = {
            "key": f"gmail:{message_id or thread_id}",
            "source": "gmail",
            "source_label": "Gmail",
            "source_message_id": message_id,
            "source_thread_id": thread_id,
            "conversation_label": subject,
            "thread_label": subject,
            "chat_type": "thread",
            "sender": sender,
            "reply_to": str(row.get('reply_to') or '').strip(),
            "from_me": 'SENT' in labels,
            "recipient": str(row.get("to") or "").strip(),
            "subject": subject,
            "text": text_body,
            "html_body": html_body,
            "snippet": snippet,
            "created_at": _email_created_at(row.get("date")),
            "processed": "UNREAD" not in labels,
            "unread": "UNREAD" in labels,
            "labels": labels,
            "attachments": _with_mail_preview_urls(row.get("attachments") or [], message_id=message_id),
            "board_id": None,
            "board_name": "",
            "can_snapshot": False,
        }
        items.append(gmail_item)
        if message_id:
            gmail_items_by_id[message_id] = gmail_item

    mailshot_rows, mailshot_status = _load_mailshot_inbox(max_results=requested) if network else ([], {"connected": False, "error": "skipped"})
    for row in mailshot_rows:
        message_id = str(row.get("id") or "").strip()
        thread_id = str(row.get("thread_id") or message_id).strip()
        subject = str(row.get("subject") or "").strip() or "(No subject)"
        labels = [str(label) for label in (row.get("labels") or [])]
        text_body, html_body, snippet = _mail_plain_and_html(row)
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
            "text": text_body,
            "html_body": html_body,
            "snippet": snippet,
            "created_at": _email_created_at(row.get("date")),
            "processed": bool(row.get("read")),
            "unread": not bool(row.get("read")),
            "labels": labels,
            "attachments": _with_mail_preview_urls(
                row.get("attachments") or [],
                message_id=message_id,
                provider="mailshot",
            ),
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
            message_id = str(row.get("source_message_id") or "")
            if message_id and message_id in gmail_items_by_id:
                existing = gmail_items_by_id[message_id]
                if row.get("ticket_id") and not existing.get("ticket_id"):
                    existing["ticket_id"] = row["ticket_id"]
                    existing["board_id"] = row.get("board_id")
                continue
            intake_item = {
                "key": f"intake:{row.get('event_id')}",
                "event_id": row.get("event_id"),
                "source": source,
                "source_label": "Gmail",
                "sender": row.get("source_contact") or row.get("source_user_id") or "",
                "source_message_id": message_id,
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
            }
            items.append(intake_item)
            if message_id:
                gmail_items_by_id[message_id] = intake_item
    except Exception:
        pass

    # Telegram group/channel messages stored for later action (Incoming folders).
    telegram_status: dict[str, Any] = {"connected": False, "error": ""}
    try:
        with get_session() as session:
            telegram_rows = (
                session.query(TelegramGroupMessage)
                .order_by(TelegramGroupMessage.created_date.desc(), TelegramGroupMessage.id.desc())
                .limit(requested)
                .all()
            )
            items.extend(telegram_incoming_item(row) for row in telegram_rows)
            telegram_status = {"connected": True, "error": "", "count": len(telegram_rows)}
    except Exception as exc:
        telegram_status = {"connected": False, "error": str(exc) or "Telegram inbox unavailable."}

    try:
        from distr.core.kanban.jira_work_lifecycle import jira_status_surface_for_ticket

        for row in items:
            ticket_id = row.get("ticket_id")
            if not ticket_id:
                continue
            if str(row.get("source") or "").lower() not in {"jira", "gmail", "whatsapp", "telegram", "mailshot"}:
                continue
            surface = jira_status_surface_for_ticket(int(ticket_id))
            if surface:
                row.update(surface)
    except Exception:
        pass

    from distr.core.incoming.project_links import apply_project_links
    apply_project_links(items, refresh=network)
    items.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    counts: dict[str, int] = {}
    for row in items:
        source = str(row.get("source") or "unknown")
        counts[source] = counts.get(source, 0) + 1
    return {
        "items": items,
        "links": links,
        "counts": counts,
        "channels": {
            "gmail": gmail_status,
            "mailshot": mailshot_status,
            "telegram": telegram_status,
        },
    }
