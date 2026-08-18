"""Jira/email-sourced work → execution → humanized client draft → Telegram approval.

Outbound writes (email reply, WhatsApp, or Jira comment) happen only after you
approve on Telegram. Voice revise is a back-and-forth: Revise → send new wording
→ confirm again.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from typing import Any, Callable, Optional

from sqlalchemy import text

from distr.core.db import engine, get_session
from distr.core.kanban.client_message_humanize import build_client_work_update, humanize_client_message

logger = logging.getLogger(__name__)


def ensure_tables() -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS jira_work_lifecycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL UNIQUE,
                board_id INTEGER,
                project_id INTEGER,
                issue_key VARCHAR,
                execution_kind VARCHAR,
                run_id INTEGER,
                status VARCHAR NOT NULL DEFAULT 'ticket_created',
                comment_draft TEXT,
                review_status VARCHAR,
                time_spent_snapshot VARCHAR,
                outbound_channel VARCHAR,
                outbound_target VARCHAR,
                client_contact VARCHAR,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                error TEXT
            )
        """))
        # ponytail: additive columns for older DBs that already have the table
        for col, decl in (
            ("outbound_channel", "VARCHAR"),
            ("outbound_target", "VARCHAR"),
            ("client_contact", "VARCHAR"),
        ):
            try:
                conn.execute(text(f"ALTER TABLE jira_work_lifecycles ADD COLUMN {col} {decl}"))
            except Exception:
                pass
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS jira_reply_reviews (
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
            "CREATE INDEX IF NOT EXISTS ix_jira_reply_reviews_pending "
            "ON jira_reply_reviews(status, telegram_chat_id, created_at)"
        ))


def record_ticket_created(
    *,
    ticket_id: int,
    board_id: int | None,
    project_id: int | None,
    issue_key: str,
    outbound_channel: str = "",
    outbound_target: str = "",
    client_contact: str = "",
) -> dict[str, Any]:
    ensure_tables()
    now = time.time()
    key = str(issue_key or "").strip().upper()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO jira_work_lifecycles(
                ticket_id, board_id, project_id, issue_key, status,
                outbound_channel, outbound_target, client_contact,
                created_at, updated_at
            ) VALUES (
                :ticket_id, :board_id, :project_id, :issue_key, 'ticket_created',
                :outbound_channel, :outbound_target, :client_contact,
                :now, :now
            ) ON CONFLICT(ticket_id) DO UPDATE SET
                board_id=excluded.board_id,
                project_id=excluded.project_id,
                issue_key=COALESCE(NULLIF(excluded.issue_key, ''), jira_work_lifecycles.issue_key),
                outbound_channel=COALESCE(NULLIF(excluded.outbound_channel, ''), jira_work_lifecycles.outbound_channel),
                outbound_target=COALESCE(NULLIF(excluded.outbound_target, ''), jira_work_lifecycles.outbound_target),
                client_contact=COALESCE(NULLIF(excluded.client_contact, ''), jira_work_lifecycles.client_contact),
                updated_at=excluded.updated_at
        """), {
            "ticket_id": int(ticket_id),
            "board_id": board_id,
            "project_id": project_id,
            "issue_key": key,
            "outbound_channel": outbound_channel or "",
            "outbound_target": outbound_target or "",
            "client_contact": client_contact or "",
            "now": now,
        })
        row = conn.execute(text(
            "SELECT * FROM jira_work_lifecycles WHERE ticket_id=:ticket_id"
        ), {"ticket_id": int(ticket_id)}).mappings().first()
    return dict(row or {})


def mark_execution_started(*, ticket_id: int, execution_kind: str, run_id: int | None = None) -> None:
    ensure_tables()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE jira_work_lifecycles SET execution_kind=:kind, run_id=:run_id,
              status='executing', updated_at=:now, error=NULL WHERE ticket_id=:ticket_id
        """), {
            "kind": execution_kind,
            "run_id": run_id,
            "now": time.time(),
            "ticket_id": int(ticket_id),
        })


def _resolve_outbound(ticket: Any, lifecycle: dict[str, Any]) -> tuple[str, str, str]:
    channel = str(lifecycle.get("outbound_channel") or "").strip().lower()
    target = str(lifecycle.get("outbound_target") or "").strip()
    contact = str(lifecycle.get("client_contact") or "").strip()
    provider = str(getattr(ticket, "source_provider", "") or "").strip().lower()
    thread = str(getattr(ticket, "source_thread_id", "") or "").strip()
    contact = contact or str(getattr(ticket, "source_contact", "") or "").strip()
    if not channel:
        if provider == "whatsapp" or (thread and "@" in thread and not thread.count("@") > 1):
            channel = "whatsapp"
            target = target or thread
        elif provider in {"gmail", "email"} or (thread and thread.isalnum() and len(thread) > 8):
            channel = "email"
            target = target or thread or str(getattr(ticket, "source_external_id", "") or "")
        else:
            channel = "jira_comment"
            target = target or str(getattr(ticket, "external_id", "") or lifecycle.get("issue_key") or "")
    if channel == "jira_comment" and not target:
        target = str(getattr(ticket, "external_id", "") or lifecycle.get("issue_key") or "")
    return channel, target, contact


def prepare_completed_jira_review(
    *,
    ticket_id: int,
    run_id: int,
    status: str,
    result_summary: str = "",
) -> dict[str, Any] | None:
    """Create an unsent client draft + Telegram review after verified completion."""
    ensure_tables()
    normalized = (status or "").strip().lower()
    with engine.connect() as conn:
        lifecycle = conn.execute(text(
            "SELECT * FROM jira_work_lifecycles WHERE ticket_id=:ticket_id"
        ), {"ticket_id": int(ticket_id)}).mappings().first()
    if not lifecycle:
        return None
    if normalized != "completed":
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE jira_work_lifecycles SET status='execution_failed', run_id=:run_id,
                  updated_at=:now, error=:error WHERE ticket_id=:ticket_id
            """), {
                "run_id": int(run_id),
                "now": time.time(),
                "error": normalized or "failed",
                "ticket_id": int(ticket_id),
            })
        return None

    from distr.core.db.kanban import KanbanTicket

    with get_session() as db:
        ticket = db.get(KanbanTicket, int(ticket_id))
        if not ticket:
            return None
        title = ticket.title or "this work"
        time_spent = str(ticket.time_spent or "").strip()
        issue_key = str(ticket.external_id or lifecycle.get("issue_key") or "").strip().upper()
        channel, target, contact = _resolve_outbound(ticket, dict(lifecycle))

    draft = build_client_work_update(
        contact=contact,
        work_title=title,
        result_summary=result_summary,
        time_spent=time_spent,
    )
    token = secrets.token_urlsafe(12)
    now = time.time()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE jira_work_lifecycles SET run_id=:run_id,
              execution_kind=COALESCE(execution_kind, 'workflow'),
              status='awaiting_reply_review', comment_draft=:draft, review_status='pending',
              time_spent_snapshot=:time_spent, issue_key=COALESCE(NULLIF(:issue_key, ''), issue_key),
              outbound_channel=:channel, outbound_target=:target, client_contact=:contact,
              updated_at=:now, error=NULL WHERE ticket_id=:ticket_id
        """), {
            "run_id": int(run_id),
            "draft": draft,
            "time_spent": time_spent,
            "issue_key": issue_key,
            "channel": channel,
            "target": target,
            "contact": contact,
            "now": now,
            "ticket_id": int(ticket_id),
        })
        lifecycle_id = conn.execute(text(
            "SELECT id FROM jira_work_lifecycles WHERE ticket_id=:ticket_id"
        ), {"ticket_id": int(ticket_id)}).scalar_one()
        conn.execute(text("""
            UPDATE jira_reply_reviews SET status='superseded', updated_at=:now
            WHERE lifecycle_id=:id AND status IN ('pending','awaiting_revision')
        """), {"now": now, "id": lifecycle_id})
        conn.execute(text("""
            INSERT INTO jira_reply_reviews(token, lifecycle_id, status, created_at, updated_at)
            VALUES (:token, :lifecycle_id, 'pending', :now, :now)
        """), {"token": token, "lifecycle_id": lifecycle_id, "now": now})
    _audit(ticket_id, run_id, "client_draft_ready", f"Draft ready for {channel or 'client'}")
    return {
        "token": token,
        "ticket_id": int(ticket_id),
        "draft": draft,
        "issue_key": issue_key,
        "time_spent": time_spent,
        "outbound_channel": channel,
        "outbound_target": target,
        "contact": contact,
    }


def review_markup(token: str) -> dict[str, Any]:
    return {"inline_keyboard": [[
        {"text": "Send to client", "callback_data": f"jr:{token}:send"},
        {"text": "Revise", "callback_data": f"jr:{token}:revise"},
        {"text": "Leave draft", "callback_data": f"jr:{token}:leave"},
    ]]}


def notify_telegram_jira_review(review: dict[str, Any]) -> bool:
    try:
        from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app

        manager = _telegram_manager_from_app()
        if not manager:
            return False
        channel = review.get("outbound_channel") or "client"
        contact = review.get("contact") or review.get("issue_key") or "the client"
        time_bit = f"\nTime on it: {review['time_spent']}" if review.get("time_spent") else ""
        body = (
            f"Work is done. I drafted this for {contact} ({channel}):{time_bit}\n\n"
            f"{review['draft']}\n\n"
            "Send it to the client, revise it, or leave it as a draft?"
        )
        return bool(manager.send_to_telegram(body, reply_markup=review_markup(review["token"])))
    except Exception:
        logger.exception("Could not send client review to Telegram")
        return False


def _review_row(token: str) -> dict[str, Any] | None:
    ensure_tables()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT r.*, l.ticket_id, l.issue_key, l.board_id, l.comment_draft,
                   l.time_spent_snapshot, l.project_id, l.outbound_channel,
                   l.outbound_target, l.client_contact
            FROM jira_reply_reviews r
            JOIN jira_work_lifecycles l ON l.id=r.lifecycle_id
            WHERE r.token=:token
        """), {"token": token}).mappings().first()
    return dict(row) if row else None


def _audit(ticket_id: int, run_id: int | None, verdict: str, summary: str) -> None:
    try:
        from distr.core.db import get_session as _gs
        from distr.core.kanban.ticket_audit import append_ticket_audit_entry

        with _gs() as db:
            append_ticket_audit_entry(
                db,
                ticket_id=int(ticket_id),
                run_id=int(run_id) if run_id else None,
                step_id=None,
                step_result_id=None,
                execution_lane="jira_lifecycle",
                status="completed",
                final_verdict=verdict,
                summary=summary[:1000],
            )
            db.commit()
    except Exception:
        logger.debug("Jira lifecycle audit skipped", exc_info=True)


def post_jira_comment(
    acct: dict[str, Any],
    issue_key: str,
    body: str,
    *,
    http_post: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    import requests
    from requests.auth import HTTPBasicAuth

    from distr.core.kanban.jira_intake import jira_domain_from_account

    domain = jira_domain_from_account(acct)
    if not domain or not issue_key or not body:
        return {"success": False, "error": "missing domain, key, or body"}
    poster = http_post or requests.post
    payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": body}]}],
        }
    }
    try:
        resp = poster(
            f"https://{domain}/rest/api/3/issue/{issue_key}/comment",
            auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        code = getattr(resp, "status_code", 0)
        if code not in (200, 201, 204):
            return {"success": False, "error": f"HTTP {code}: {getattr(resp, 'text', '')[:300]}"}
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _send_client_message(row: dict[str, Any], *, comment_fn: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    channel = str(row.get("outbound_channel") or "jira_comment").strip().lower()
    target = str(row.get("outbound_target") or "").strip()
    draft = str(row.get("comment_draft") or "").strip()
    if not draft:
        return {"success": False, "error": "empty draft"}

    if channel == "whatsapp":
        from distr.core.integrations.whatsapp.relay_client import send_message_via_relay

        try:
            result = send_message_via_relay(jid=target, text=draft)
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        return {"success": bool(result.get("success")), "error": result.get("error"), "channel": "whatsapp"}

    if channel == "email":
        try:
            from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

            connector = GoogleWorkspaceConnector()
            if not connector.is_connected():
                return {"success": False, "error": "Gmail is not connected"}
            # Prefer reply when target is a Gmail message/thread id.
            if hasattr(connector, "reply_to_email") and target:
                result = connector.reply_to_email(target, draft)
            elif hasattr(connector, "send_email"):
                to_addr = str(row.get("client_contact") or "").strip()
                if not to_addr or "@" not in to_addr:
                    return {"success": False, "error": "No client email address on the ticket"}
                result = connector.send_email(to_addr, "Update", draft)
            else:
                return {"success": False, "error": "Gmail reply is unavailable"}
            ok = bool(result is True or (isinstance(result, dict) and result.get("success", True)))
            return {"success": ok, "error": None if ok else str(result), "channel": "email"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # Default: Jira comment (visible on the issue; still gated by Telegram approve)
    issue_key = target or str(row.get("issue_key") or "").strip().upper()
    if comment_fn is not None:
        result = comment_fn({}, issue_key, draft)
        result = dict(result or {})
        result["channel"] = "jira_comment"
        return result

    from distr.core.kanban.jira_intake import load_jira_account

    acct = load_jira_account()
    if not acct:
        return {"success": False, "error": "No valid Jira account is connected"}
    result = post_jira_comment(acct, issue_key, draft)
    result = dict(result or {})
    result["channel"] = "jira_comment"
    return result


def handle_telegram_jira_reply(
    value: str,
    *,
    chat_id: int | str | None = None,
    comment_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Resolve client-draft callbacks. External sends only after Send to client."""
    ensure_tables()
    clean = str(value or "").strip()
    # Accept legacy comment/time actions as send for older messages.
    callback = re.fullmatch(r"jr:([A-Za-z0-9_-]+):(send|revise|leave|comment|time)", clean)
    if callback:
        token, action = callback.groups()
        if action in {"comment", "time"}:
            action = "send"
        row = _review_row(token)
        if not row:
            return {"handled": True, "text": "That client draft review no longer exists."}
        if row["status"] in {"sent", "left_draft", "commented", "timed"}:
            return {"handled": True, "text": "That client draft decision was already applied."}
        if row["status"] == "resolving":
            return {"handled": True, "text": "That client draft decision is already being applied."}

        if action == "revise":
            with engine.begin() as conn:
                changed = conn.execute(text("""
                    UPDATE jira_reply_reviews SET status='awaiting_revision',
                      telegram_chat_id=:chat, updated_at=:now
                    WHERE token=:token AND status='pending'
                """), {
                    "chat": str(chat_id) if chat_id is not None else None,
                    "now": time.time(),
                    "token": token,
                }).rowcount
            if not changed:
                return {"handled": True, "text": "That draft is no longer waiting for revision."}
            return {
                "handled": True,
                "text": "Send the revised client message in your next Telegram message (voice or text). I will show it again before sending.",
            }

        if action == "leave":
            with engine.begin() as conn:
                claimed = conn.execute(text("""
                    UPDATE jira_reply_reviews SET status='resolving', telegram_chat_id=:chat, updated_at=:now
                    WHERE token=:token AND status='pending'
                """), {
                    "chat": str(chat_id) if chat_id is not None else None,
                    "now": time.time(),
                    "token": token,
                }).rowcount
            if not claimed:
                return {"handled": True, "text": "That client draft decision is already being applied or has expired."}
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE jira_reply_reviews SET status='left_draft', resolved_action='leave', updated_at=:now
                    WHERE token=:token
                """), {"now": time.time(), "token": token})
                conn.execute(text("""
                    UPDATE jira_work_lifecycles SET status='comment_draft_ready', review_status='left_draft', updated_at=:now
                    WHERE id=:id
                """), {"now": time.time(), "id": row["lifecycle_id"]})
            _audit(int(row["ticket_id"]), None, "client_draft_left", "Left client draft unsent")
            return {"handled": True, "text": "Left the client message as a local draft. Nothing was sent."}

        with engine.begin() as conn:
            claimed = conn.execute(text("""
                UPDATE jira_reply_reviews SET status='resolving', telegram_chat_id=:chat, updated_at=:now
                WHERE token=:token AND status='pending'
            """), {
                "chat": str(chat_id) if chat_id is not None else None,
                "now": time.time(),
                "token": token,
            }).rowcount
        if not claimed:
            return {"handled": True, "text": "That client draft decision is already being applied or has expired."}

        result = _send_client_message(row, comment_fn=comment_fn)
        if not result.get("success"):
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE jira_reply_reviews SET status='pending', error=:error, updated_at=:now
                    WHERE token=:token AND status='resolving'
                """), {
                    "error": str(result.get("error") or "send failed"),
                    "now": time.time(),
                    "token": token,
                })
            return {
                "handled": True,
                "text": f"Could not send to the client: {result.get('error') or 'failed'}. Draft is still saved.",
            }
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE jira_reply_reviews SET status='sent', resolved_action='send', updated_at=:now
                WHERE token=:token
            """), {"now": time.time(), "token": token})
            conn.execute(text("""
                UPDATE jira_work_lifecycles SET status='reply_sent', review_status='sent', updated_at=:now
                WHERE id=:id
            """), {"now": time.time(), "id": row["lifecycle_id"]})
        channel = result.get("channel") or row.get("outbound_channel") or "client"
        _audit(int(row["ticket_id"]), None, "client_message_sent", f"Sent via {channel}")
        return {"handled": True, "text": f"Sent to the client via {channel}."}

    # Capture revised wording (voice or text) for a single awaiting_revision review.
    with engine.connect() as conn:
        pending = conn.execute(text("""
            SELECT token FROM jira_reply_reviews
            WHERE status='awaiting_revision'
              AND (:chat IS NULL OR telegram_chat_id IS NULL OR telegram_chat_id=:chat)
            ORDER BY updated_at DESC LIMIT 2
        """), {"chat": str(chat_id) if chat_id is not None else None}).mappings().all()
    if len(pending) != 1 or not clean:
        return None
    row = _review_row(pending[0]["token"])
    if not row:
        return None
    revised = humanize_client_message(clean)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE jira_reply_reviews SET status='pending', updated_at=:now WHERE token=:token
        """), {"now": time.time(), "token": row["token"]})
        conn.execute(text("""
            UPDATE jira_work_lifecycles SET comment_draft=:draft, updated_at=:now WHERE id=:id
        """), {"draft": revised, "now": time.time(), "id": row["lifecycle_id"]})
    _audit(int(row["ticket_id"]), None, "client_draft_revised", "Client draft revised from Telegram")
    return {
        "handled": True,
        "text": f"Updated client draft:\n\n{revised}\n\nSend it to the client, revise it again, or leave it?",
        "reply_markup": review_markup(row["token"]),
        "token": row["token"],
    }
