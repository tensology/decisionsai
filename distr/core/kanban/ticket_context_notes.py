"""Compact orchestrator/workflow notes stored on the ticket (not the workflow)."""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_NOTE_CHARS = 320
_MAX_TOTAL_CHARS = 12_000
_ACTIVE_TICKET_BY_CHAT: dict[int, int] = {}
_LOCK = threading.Lock()


def _compact_note(text: str, *, limit: int = _MAX_NOTE_CHARS) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def register_ticket_chat_context(chat_id: int, ticket_id: int) -> None:
    """Remember that this chat is discussing a specific local ticket."""
    if chat_id < 1 or ticket_id < 1:
        return
    with _LOCK:
        _ACTIVE_TICKET_BY_CHAT[int(chat_id)] = int(ticket_id)


def get_registered_ticket_for_chat(chat_id: int) -> Optional[int]:
    with _LOCK:
        value = _ACTIVE_TICKET_BY_CHAT.get(int(chat_id))
    return int(value) if value else None


def append_ticket_context_note(
    session,
    ticket_id: int,
    note: str,
    *,
    source: str = "orchestrator",
) -> bool:
    """Append a single compact note line to the ticket. Returns False if skipped."""
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orm_compat import orm_get_by_id

    compact = _compact_note(note)
    if not compact:
        return False
    ticket = orm_get_by_id(session, KanbanTicket, int(ticket_id))
    if not ticket:
        return False
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"{stamp} [{source}] {compact}"
    existing = (getattr(ticket, "context_notes", None) or "").strip()
    combined = f"{existing}\n{line}".strip() if existing else line
    if len(combined) > _MAX_TOTAL_CHARS:
        lines = combined.splitlines()
        while lines and len("\n".join(lines)) > _MAX_TOTAL_CHARS:
            lines.pop(0)
        combined = "\n".join(lines)
    ticket.context_notes = combined
    return True


def format_context_notes_block(notes: str) -> str:
    text = (notes or "").strip()
    if not text:
        return ""
    return "\n\n**Ticket notes (from orchestrator)**\n" + text


def maybe_capture_orchestrator_turn(
    chat_id: Optional[int],
    assistant_text: Optional[str] = None,
) -> None:
    """Silently store a compact exchange summary on the active ticket."""
    if not chat_id:
        return
    ticket_id = get_registered_ticket_for_chat(int(chat_id))
    if not ticket_id:
        return
    assistant = _compact_note(assistant_text or "", limit=180)
    if not assistant:
        return
    try:
        from distr.core.db import get_session
        from distr.core.chat import ChatService

        user_line = ""
        try:
            messages = ChatService.get_chat_history(int(chat_id)) or []
            for msg in reversed(messages[-8:]):
                if (msg.get("role") or "").lower() == "user":
                    user_line = _compact_note(msg.get("content") or "", limit=120)
                    break
        except Exception:
            logger.debug("ticket context note: could not read user message", exc_info=True)

        if user_line:
            summary = f"User: {user_line} | Agent: {assistant}"
        else:
            summary = f"Agent: {assistant}"

        with get_session() as session:
            if append_ticket_context_note(session, ticket_id, summary, source="orchestrator"):
                session.commit()
    except Exception:
        logger.debug("maybe_capture_orchestrator_turn failed", exc_info=True)
