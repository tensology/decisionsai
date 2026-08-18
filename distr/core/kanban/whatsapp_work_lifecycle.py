"""Durable WhatsApp message -> ticket -> execution -> reply-draft lifecycle."""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from typing import Any, Iterable

from sqlalchemy import text

from distr.core.db import engine, get_session

logger = logging.getLogger(__name__)


def ensure_tables() -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS whatsapp_work_lifecycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL UNIQUE,
                board_id INTEGER,
                project_id INTEGER,
                source_jid VARCHAR,
                source_phone VARCHAR,
                source_contact VARCHAR,
                message_ids TEXT NOT NULL DEFAULT '[]',
                execution_kind VARCHAR,
                run_id INTEGER,
                status VARCHAR NOT NULL DEFAULT 'ticket_created',
                reply_draft TEXT,
                reply_status VARCHAR,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                error TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS whatsapp_reply_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token VARCHAR NOT NULL UNIQUE,
                lifecycle_id INTEGER NOT NULL,
                telegram_chat_id VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'pending',
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                resolved_action VARCHAR,
                error TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_whatsapp_reply_reviews_pending "
            "ON whatsapp_reply_reviews(status, telegram_chat_id, created_at)"
        ))


def record_ticket_created(
    *, ticket_id: int, board_id: int | None, project_id: int | None,
    source_jid: str, source_phone: str, source_contact: str,
    message_ids: Iterable[int],
) -> dict[str, Any]:
    ensure_tables()
    now = time.time()
    payload = json.dumps([int(value) for value in message_ids if value])
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO whatsapp_work_lifecycles(
                ticket_id, board_id, project_id, source_jid, source_phone,
                source_contact, message_ids, status, created_at, updated_at
            ) VALUES (
                :ticket_id, :board_id, :project_id, :source_jid, :source_phone,
                :source_contact, :message_ids, 'ticket_created', :now, :now
            ) ON CONFLICT(ticket_id) DO UPDATE SET
                board_id=excluded.board_id, project_id=excluded.project_id,
                source_jid=excluded.source_jid, source_phone=excluded.source_phone,
                source_contact=excluded.source_contact, message_ids=excluded.message_ids,
                updated_at=excluded.updated_at
        """), {
            "ticket_id": int(ticket_id), "board_id": board_id, "project_id": project_id,
            "source_jid": source_jid or "", "source_phone": source_phone or "",
            "source_contact": source_contact or "", "message_ids": payload, "now": now,
        })
        row = conn.execute(text(
            "SELECT * FROM whatsapp_work_lifecycles WHERE ticket_id=:ticket_id"
        ), {"ticket_id": int(ticket_id)}).mappings().first()
    return dict(row or {})


def mark_execution_started(*, ticket_id: int, execution_kind: str, run_id: int | None = None) -> None:
    ensure_tables()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE whatsapp_work_lifecycles SET execution_kind=:kind, run_id=:run_id,
              status='executing', updated_at=:now, error=NULL WHERE ticket_id=:ticket_id
        """), {"kind": execution_kind, "run_id": run_id, "now": time.time(), "ticket_id": int(ticket_id)})


def _client_draft(ticket_title: str, result_summary: str, contact: str) -> str:
    from distr.core.kanban.client_message_humanize import build_client_work_update

    return build_client_work_update(
        contact=contact,
        work_title=ticket_title,
        result_summary=result_summary,
    )


def prepare_completed_reply(
    *, ticket_id: int, run_id: int, status: str, result_summary: str = "",
) -> dict[str, Any] | None:
    """Create a WhatsApp draft and Telegram review token after verified completion."""
    ensure_tables()
    normalized = (status or "").strip().lower()
    with engine.connect() as conn:
        lifecycle = conn.execute(text(
            "SELECT * FROM whatsapp_work_lifecycles WHERE ticket_id=:ticket_id"
        ), {"ticket_id": int(ticket_id)}).mappings().first()
    if not lifecycle:
        return None
    if normalized != "completed":
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE whatsapp_work_lifecycles SET status='execution_failed', run_id=:run_id,
                  updated_at=:now, error=:error WHERE ticket_id=:ticket_id
            """), {"run_id": int(run_id), "now": time.time(), "error": normalized or "failed", "ticket_id": int(ticket_id)})
        return None

    from distr.core.db.kanban import KanbanTicket
    from distr.core.kanban.whatsapp_compose_drafts import save_compose_draft

    with get_session() as db:
        ticket = db.get(KanbanTicket, int(ticket_id))
        if not ticket:
            return None
        title = ticket.title or "the requested work"
    contact = str(lifecycle.get("source_contact") or "").strip()
    draft = _client_draft(title, result_summary, contact)
    phone = str(lifecycle.get("source_phone") or "").strip()
    jid = str(lifecycle.get("source_jid") or "").strip()
    if not phone and jid:
        phone = jid.split("@", 1)[0]
    if not phone:
        return None
    save_compose_draft(
        jid_phone=phone, jid=jid, contact_name=contact, board_id=lifecycle.get("board_id"),
        text=draft, source="agent", sanitize=True,
    )
    token = secrets.token_urlsafe(12)
    now = time.time()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE whatsapp_work_lifecycles SET run_id=:run_id, execution_kind=COALESCE(execution_kind, 'workflow'),
              status='awaiting_reply_review', reply_draft=:draft, reply_status='pending',
              updated_at=:now, error=NULL WHERE ticket_id=:ticket_id
        """), {"run_id": int(run_id), "draft": draft, "now": now, "ticket_id": int(ticket_id)})
        lifecycle_id = conn.execute(text(
            "SELECT id FROM whatsapp_work_lifecycles WHERE ticket_id=:ticket_id"
        ), {"ticket_id": int(ticket_id)}).scalar_one()
        conn.execute(text("UPDATE whatsapp_reply_reviews SET status='superseded', updated_at=:now WHERE lifecycle_id=:id AND status IN ('pending','awaiting_revision')"), {"now": now, "id": lifecycle_id})
        conn.execute(text("""
            INSERT INTO whatsapp_reply_reviews(token, lifecycle_id, status, created_at, updated_at)
            VALUES (:token, :lifecycle_id, 'pending', :now, :now)
        """), {"token": token, "lifecycle_id": lifecycle_id, "now": now})
    return {"token": token, "ticket_id": int(ticket_id), "draft": draft, "contact": contact or phone}


def review_markup(token: str) -> dict[str, Any]:
    return {"inline_keyboard": [[
        {"text": "Send", "callback_data": f"wa:{token}:send"},
        {"text": "Revise", "callback_data": f"wa:{token}:revise"},
        {"text": "Leave draft", "callback_data": f"wa:{token}:leave"},
    ]]}


def notify_telegram_review(review: dict[str, Any]) -> bool:
    try:
        from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app
        manager = _telegram_manager_from_app()
        if not manager:
            return False
        return bool(manager.send_to_telegram(
            f"The work is in QA. I prepared this WhatsApp reply for {review['contact']}:\n\n{review['draft']}\n\nSend it, revise it, or leave it as a draft?",
            reply_markup=review_markup(review["token"]),
        ))
    except Exception:
        logger.exception("Could not send WhatsApp reply review to Telegram")
        return False


def _review_row(token: str) -> dict[str, Any] | None:
    ensure_tables()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT r.*, l.ticket_id, l.source_jid, l.source_phone, l.source_contact,
                   l.board_id, l.reply_draft
            FROM whatsapp_reply_reviews r
            JOIN whatsapp_work_lifecycles l ON l.id=r.lifecycle_id
            WHERE r.token=:token
        """), {"token": token}).mappings().first()
    return dict(row) if row else None


def handle_telegram_reply(value: str, *, chat_id: int | str | None = None) -> dict[str, Any] | None:
    """Resolve reply-review callbacks or capture requested revised wording."""
    ensure_tables()
    clean = str(value or "").strip()
    callback = re.fullmatch(r"wa:([A-Za-z0-9_-]+):(send|revise|leave)", clean)
    if callback:
        token, action = callback.groups()
        row = _review_row(token)
        if not row:
            return {"handled": True, "text": "That WhatsApp draft review no longer exists."}
        if row["status"] in {"sent", "left_draft"}:
            return {"handled": True, "text": "That WhatsApp draft decision was already applied."}
        if row["status"] == "resolving":
            return {"handled": True, "text": "That WhatsApp draft decision is already being applied."}
        if action == "revise":
            with engine.begin() as conn:
                changed = conn.execute(text("UPDATE whatsapp_reply_reviews SET status='awaiting_revision', telegram_chat_id=:chat, updated_at=:now WHERE token=:token AND status='pending'"), {"chat": str(chat_id) if chat_id is not None else None, "now": time.time(), "token": token}).rowcount
            if not changed:
                return {"handled": True, "text": "That WhatsApp draft is no longer waiting for revision."}
            return {"handled": True, "text": "Send me the revised wording in your next Telegram message. I will show it again before sending."}
        with engine.begin() as conn:
            claimed = conn.execute(text("UPDATE whatsapp_reply_reviews SET status='resolving', telegram_chat_id=:chat, updated_at=:now WHERE token=:token AND status='pending'"), {"chat": str(chat_id) if chat_id is not None else None, "now": time.time(), "token": token}).rowcount
        if not claimed:
            return {"handled": True, "text": "That WhatsApp draft decision is already being applied or has expired."}
        if action == "leave":
            with engine.begin() as conn:
                conn.execute(text("UPDATE whatsapp_reply_reviews SET status='left_draft', resolved_action='leave', updated_at=:now WHERE token=:token"), {"now": time.time(), "token": token})
                conn.execute(text("UPDATE whatsapp_work_lifecycles SET status='reply_draft_ready', reply_status='left_draft', updated_at=:now WHERE id=:id"), {"now": time.time(), "id": row["lifecycle_id"]})
            return {"handled": True, "text": "I left the reply in the WhatsApp composer as a draft."}
        from distr.core.integrations.whatsapp.relay_client import send_message_via_relay
        try:
            result = send_message_via_relay(jid=row["source_jid"], text=row["reply_draft"])
        except Exception as exc:
            logger.exception("WhatsApp relay failed while sending reviewed draft")
            result = {"success": False, "error": str(exc)}
        if not result.get("success"):
            with engine.begin() as conn:
                conn.execute(text("UPDATE whatsapp_reply_reviews SET status='pending', error=:error, updated_at=:now WHERE token=:token AND status='resolving'"), {"error": str(result.get("error") or "send failed"), "now": time.time(), "token": token})
            return {"handled": True, "text": f"WhatsApp did not accept the message: {result.get('error') or 'send failed'}. The draft is still saved."}
        from distr.core.kanban.whatsapp_compose_drafts import delete_compose_draft
        delete_compose_draft(row["source_phone"] or str(row["source_jid"] or "").split("@", 1)[0])
        with engine.begin() as conn:
            conn.execute(text("UPDATE whatsapp_reply_reviews SET status='sent', resolved_action='send', updated_at=:now WHERE token=:token"), {"now": time.time(), "token": token})
            conn.execute(text("UPDATE whatsapp_work_lifecycles SET status='reply_sent', reply_status='sent', updated_at=:now WHERE id=:id"), {"now": time.time(), "id": row["lifecycle_id"]})
        return {"handled": True, "text": "WhatsApp message sent. The ticket remains in QA until you move it to Complete."}

    with engine.connect() as conn:
        pending = conn.execute(text("""
            SELECT token FROM whatsapp_reply_reviews
            WHERE status='awaiting_revision' AND (:chat IS NULL OR telegram_chat_id IS NULL OR telegram_chat_id=:chat)
            ORDER BY updated_at DESC LIMIT 2
        """), {"chat": str(chat_id) if chat_id is not None else None}).mappings().all()
    if len(pending) != 1 or not clean:
        return None
    row = _review_row(pending[0]["token"])
    from distr.core.kanban.whatsapp_compose_drafts import save_compose_draft
    save_compose_draft(
        jid_phone=row["source_phone"], jid=row["source_jid"], contact_name=row["source_contact"],
        board_id=row["board_id"], text=clean, source="agent", sanitize=True,
    )
    with engine.begin() as conn:
        conn.execute(text("UPDATE whatsapp_reply_reviews SET status='pending', updated_at=:now WHERE token=:token"), {"now": time.time(), "token": row["token"]})
        conn.execute(text("UPDATE whatsapp_work_lifecycles SET reply_draft=:draft, updated_at=:now WHERE id=:id"), {"draft": clean, "now": time.time(), "id": row["lifecycle_id"]})
    return {"handled": True, "text": f"Updated WhatsApp draft:\n\n{clean}\n\nSend it, revise it, or leave it as a draft?", "reply_markup": review_markup(row["token"]), "token": row["token"]}
