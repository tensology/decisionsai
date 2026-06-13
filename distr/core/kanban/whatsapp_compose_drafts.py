"""Persisted WhatsApp reply drafts (user typing + agent-prepared messages)."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_WHITESPACE_ONLY = re.compile(r"^\s*$", re.UNICODE)
_EM_DASH = re.compile(r"\s*[—–]\s*")
_MULTI_SPACE = re.compile(r" +")

WHATSAPP_DRAFT_STYLE_HINT = (
    "Write like a person texting on WhatsApp: short sentences, plain words, no em dashes, "
    "no semicolon chains, no rhetorical setup ('Here's the thing'), no lists of three, "
    "no purple prose, no AP-essay wrap-up. Say what you mean in one pass."
)


def normalize_draft_text(text: Optional[str]) -> str:
    """Return stripped draft text, or empty when only whitespace."""
    if text is None:
        return ""
    cleaned = str(text).replace("\r\n", "\n").strip()
    if _WHITESPACE_ONLY.match(cleaned):
        return ""
    return cleaned


def sanitize_agent_draft_text(text: Optional[str]) -> str:
    """Normalize and strip common AI-prose markers from agent-authored drafts."""
    cleaned = normalize_draft_text(text)
    if not cleaned:
        return ""
    cleaned = _EM_DASH.sub(", ", cleaned)
    cleaned = cleaned.replace(";", ".")
    cleaned = _MULTI_SPACE.sub(" ", cleaned)
    return cleaned.strip()


def draft_row_to_dict(row) -> Dict[str, Any]:
    """Serialize a WhatsAppComposeDraft ORM row."""
    return {
        "jid_phone": row.jid_phone,
        "jid": row.jid,
        "chat_type": row.chat_type,
        "contact_name": row.contact_name,
        "text": row.draft_text or "",
        "source": row.source or "user",
        "board_id": row.board_id,
        "updated_at": row.updated_date.isoformat() if row.updated_date else None,
    }


def list_compose_drafts() -> List[Dict[str, Any]]:
    """Return all non-empty drafts."""
    from distr.core.db import get_session, WhatsAppComposeDraft

    with get_session() as session:
        rows = (
            session.query(WhatsAppComposeDraft)
            .filter(WhatsAppComposeDraft.draft_text.isnot(None))
            .filter(WhatsAppComposeDraft.draft_text != "")
            .order_by(WhatsAppComposeDraft.updated_date.desc())
            .all()
        )
        return [draft_row_to_dict(r) for r in rows if normalize_draft_text(r.draft_text)]


def get_compose_draft(jid_phone: str) -> Optional[Dict[str, Any]]:
    """Fetch one draft by chat phone key."""
    key = (jid_phone or "").strip()
    if not key:
        return None
    from distr.core.db import get_session, WhatsAppComposeDraft

    with get_session() as session:
        row = session.get(WhatsAppComposeDraft, key)
        if not row or not normalize_draft_text(row.draft_text):
            return None
        return draft_row_to_dict(row)


def save_compose_draft(
    *,
    jid_phone: str,
    text: str,
    jid: Optional[str] = None,
    chat_type: Optional[str] = None,
    contact_name: Optional[str] = None,
    source: str = "user",
    board_id: Optional[int] = None,
    sanitize: bool = False,
) -> Optional[Dict[str, Any]]:
    """Upsert or delete a draft. Returns None when draft cleared."""
    key = (jid_phone or "").strip()
    if not key:
        raise ValueError("jid_phone is required")

    body = sanitize_agent_draft_text(text) if sanitize else normalize_draft_text(text)
    from distr.core.db import get_session, WhatsAppComposeDraft

    with get_session() as session:
        row = session.get(WhatsAppComposeDraft, key)
        if not body:
            if row:
                session.delete(row)
                session.commit()
            return None

        if row is None:
            row = WhatsAppComposeDraft(jid_phone=key)
            session.add(row)

        row.draft_text = body
        row.jid = (jid or row.jid or "").strip() or None
        row.chat_type = (chat_type or row.chat_type or "private").strip() or "private"
        row.contact_name = (contact_name or row.contact_name or "").strip() or None
        row.source = (source or "user").strip() or "user"
        row.board_id = board_id if board_id is not None else row.board_id
        row.updated_date = datetime.utcnow()
        session.commit()
        session.refresh(row)
        return draft_row_to_dict(row)


def delete_compose_draft(jid_phone: str) -> bool:
    """Remove a draft. Returns True if a row was deleted."""
    key = (jid_phone or "").strip()
    if not key:
        return False
    from distr.core.db import get_session, WhatsAppComposeDraft

    with get_session() as session:
        row = session.get(WhatsAppComposeDraft, key)
        if not row:
            return False
        session.delete(row)
        session.commit()
        return True


def delete_drafts_for_phones(jid_phones: List[str]) -> int:
    """Delete drafts for the given chat keys (e.g. when chats are removed)."""
    keys = [str(k).strip() for k in jid_phones if str(k).strip()]
    if not keys:
        return 0
    from distr.core.db import get_session, WhatsAppComposeDraft

    with get_session() as session:
        deleted = (
            session.query(WhatsAppComposeDraft)
            .filter(WhatsAppComposeDraft.jid_phone.in_(keys))
            .delete(synchronize_session=False)
        )
        session.commit()
        return int(deleted or 0)


def delete_all_compose_drafts() -> int:
    """Clear every stored WhatsApp compose draft."""
    from distr.core.db import get_session, WhatsAppComposeDraft

    with get_session() as session:
        deleted = session.query(WhatsAppComposeDraft).delete(synchronize_session=False)
        session.commit()
        return int(deleted or 0)


def resolve_board_linked_jid(
    board_id: Optional[int] = None,
    board_name: str = "",
) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
    """Return (jid_phone, jid, board_id, contact_name) from a board's WhatsApp link."""
    from distr.core.db import get_session, WhatsAppPhoneLink
    from distr.core.db.kanban import KanbanBoard

    with get_session() as session:
        board = None
        if board_id:
            board = session.get(KanbanBoard, int(board_id))
        elif (board_name or "").strip():
            board = (
                session.query(KanbanBoard)
                .filter(KanbanBoard.name.ilike((board_name or "").strip()))
                .first()
            )
        if not board:
            return None, None, None, None

        link = (
            session.query(WhatsAppPhoneLink)
            .filter(WhatsAppPhoneLink.board_id == board.id)
            .order_by(WhatsAppPhoneLink.created_date.desc())
            .first()
        )
        if not link:
            return None, None, int(board.id), None

        jid = (link.phone_jid or "").strip() or None
        phone = (link.phone_number or "").strip()
        if not phone and jid:
            phone = jid.split("@")[0]
        return phone or None, jid, int(board.id), (link.contact_name or "").strip() or None
