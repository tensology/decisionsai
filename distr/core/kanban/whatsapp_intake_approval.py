"""WhatsApp WorkIntake → Telegram yes/no/add → readiness → create ticket.

Pre-ticket gate for WhatsApp-sourced work. Classified CREATE_TICKET /
RUN_WORKFLOW requests are staged until Paul confirms on Telegram.

Buttons: Yes / No / Add  (callback wi:<token>:yes|no|add)
Plain replies (exactly one pending):
  - add <instruction>  → fold into draft, stay pending
  - ready | build it | create ticket | yes | go ahead  → create ticket
  - no | stop | ignore | reject  → discard

Env: DECISIONSAI_WHATSAPP_INTAKE_APPROVAL=1 (default) enables the gate for
WhatsApp. Set 0|false|no|off to restore immediate ticket create. Bypass one
request with metadata intake_approval_confirmed=True (used after readiness).
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from typing import Any, Optional

from sqlalchemy import text

from distr.core.db import engine

logger = logging.getLogger(__name__)

# Exact readiness phrases Paul (or voice) can say to create the ticket.
READINESS_PHRASES = frozenset({
    "ready",
    "build it",
    "create ticket",
    "yes",
    "go ahead",
    "do it",
    "create it",
})
_NO_RE = re.compile(
    r"^\s*(no|nope|stop|cancel|ignore|reject|discard|not now)\s*[.!?]?\s*$",
    re.I,
)
_ADD_PREFIX_RE = re.compile(
    r"^\s*add(?:\s+context)?(?:\s*[:\-]|)\s+(.+)$",
    re.I | re.S,
)


def whatsapp_intake_approval_enabled() -> bool:
    """Default ON. Opt out with DECISIONSAI_WHATSAPP_INTAKE_APPROVAL=0|false|no|off."""
    raw = os.environ.get("DECISIONSAI_WHATSAPP_INTAKE_APPROVAL", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def ensure_tables() -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS whatsapp_intake_approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token VARCHAR NOT NULL UNIQUE,
                status VARCHAR NOT NULL DEFAULT 'pending',
                intake_json TEXT NOT NULL DEFAULT '{}',
                classified_action VARCHAR NOT NULL DEFAULT 'create_ticket',
                classified_reason TEXT NOT NULL DEFAULT '',
                draft_text TEXT NOT NULL DEFAULT '',
                added_instructions TEXT NOT NULL DEFAULT '[]',
                telegram_chat_id VARCHAR,
                ticket_id INTEGER,
                workflow_run_id INTEGER,
                development_chat_id INTEGER,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL,
                error TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_whatsapp_intake_approvals_pending "
            "ON whatsapp_intake_approvals(status, created_at)"
        ))


def intake_markup(token: str) -> dict[str, Any]:
    """Telegram inline keyboard: Yes / No / Add."""
    return {"inline_keyboard": [[
        {"text": "Yes", "callback_data": f"wi:{token}:yes"},
        {"text": "No", "callback_data": f"wi:{token}:no"},
        {"text": "Add", "callback_data": f"wi:{token}:add"},
    ]]}


def format_approval_prompt(
    *,
    draft_text: str,
    classified_action: str,
    classified_reason: str = "",
    added_instructions: list[str] | None = None,
) -> str:
    lines = [
        "**WhatsApp work ready for your call**",
        "",
        f"Classified as `{classified_action}`"
        + (f" — {classified_reason}" if classified_reason else "")
        + ".",
        "",
        "Draft:",
        draft_text.strip() or "(empty)",
    ]
    extras = [str(x).strip() for x in (added_instructions or []) if str(x).strip()]
    if extras:
        lines.append("")
        lines.append("Folded adds:")
        for item in extras:
            lines.append(f"- {item}")
    lines.extend([
        "",
        "Reply **Yes** / **ready** / **build it** / **create ticket** to create the ticket, "
        "Development thread, and start time tracking.",
        "Reply **No** to discard.",
        "Reply **Add** then an instruction, or `add <instruction>`, to fold into the ticket.",
    ])
    return "\n".join(lines)


def stage_whatsapp_intake(
    *,
    intake_payload: dict[str, Any],
    classified_action: str,
    classified_reason: str = "",
    draft_text: str = "",
) -> dict[str, Any]:
    """Persist a pending WA intake approval and return token + prompt fields."""
    ensure_tables()
    token = secrets.token_urlsafe(12)
    now = time.time()
    draft = (draft_text or str(intake_payload.get("user_text") or intake_payload.get("transcript") or "")).strip()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO whatsapp_intake_approvals (
                token, status, intake_json, classified_action, classified_reason,
                draft_text, added_instructions, created_at, updated_at
            ) VALUES (
                :token, 'pending', :intake_json, :action, :reason,
                :draft, '[]', :now, :now
            )
        """), {
            "token": token,
            "intake_json": json.dumps(intake_payload),
            "action": str(classified_action or "create_ticket"),
            "reason": str(classified_reason or "")[:500],
            "draft": draft,
            "now": now,
        })
    return {
        "token": token,
        "status": "pending",
        "draft_text": draft,
        "classified_action": str(classified_action or "create_ticket"),
        "classified_reason": str(classified_reason or ""),
        "added_instructions": [],
        "reply_markup": intake_markup(token),
        "text": format_approval_prompt(
            draft_text=draft,
            classified_action=str(classified_action or "create_ticket"),
            classified_reason=str(classified_reason or ""),
        ),
    }


def _row(token: str) -> dict[str, Any] | None:
    ensure_tables()
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM whatsapp_intake_approvals WHERE token=:token"
        ), {"token": token}).mappings().first()
    return dict(row) if row else None


def _pending_for_chat(chat_id: int | str | None) -> list[dict[str, Any]]:
    ensure_tables()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT * FROM whatsapp_intake_approvals
            WHERE status IN ('pending', 'awaiting_add')
              AND (:chat_id IS NULL OR telegram_chat_id IS NULL OR telegram_chat_id=:chat_id)
            ORDER BY created_at DESC, id DESC LIMIT 5
        """), {"chat_id": str(chat_id) if chat_id is not None else None}).mappings().all()
    return [dict(r) for r in rows]


def _parse_added(row: dict[str, Any]) -> list[str]:
    try:
        raw = json.loads(row.get("added_instructions") or "[]")
    except Exception:
        raw = []
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def notify_telegram_approval(pending: dict[str, Any]) -> bool:
    """Send the yes/no/add card to Paul's Telegram DM."""
    try:
        from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app

        manager = _telegram_manager_from_app()
        if not manager:
            return False
        body = pending.get("text") or format_approval_prompt(
            draft_text=str(pending.get("draft_text") or ""),
            classified_action=str(pending.get("classified_action") or "create_ticket"),
            classified_reason=str(pending.get("classified_reason") or ""),
            added_instructions=list(pending.get("added_instructions") or []),
        )
        ok = bool(manager.send_to_telegram(
            body,
            reply_markup=pending.get("reply_markup") or intake_markup(str(pending["token"])),
        ))
        return ok
    except Exception:
        logger.exception("Could not send WhatsApp intake approval to Telegram")
        return False


def _fold_add(token: str, instruction: str, *, chat_id: int | str | None) -> dict[str, Any]:
    clean = str(instruction or "").strip()
    if not clean:
        return {
            "handled": True,
            "text": "Send the instruction to fold, e.g. `add include the checkout screenshot`.",
            "reply_markup": intake_markup(token),
            "token": token,
            "action": "add_prompt",
        }
    row = _row(token)
    if not row:
        return {"handled": True, "text": "That WhatsApp intake approval no longer exists."}
    if row["status"] not in {"pending", "awaiting_add"}:
        return {"handled": True, "text": "That WhatsApp intake was already handled."}
    extras = _parse_added(row)
    extras.append(clean)
    now = time.time()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE whatsapp_intake_approvals
            SET added_instructions=:adds, status='pending',
                telegram_chat_id=COALESCE(:chat, telegram_chat_id), updated_at=:now
            WHERE token=:token AND status IN ('pending', 'awaiting_add')
        """), {
            "adds": json.dumps(extras),
            "chat": str(chat_id) if chat_id is not None else None,
            "now": now,
            "token": token,
        })
    prompt = format_approval_prompt(
        draft_text=str(row.get("draft_text") or ""),
        classified_action=str(row.get("classified_action") or "create_ticket"),
        classified_reason=str(row.get("classified_reason") or ""),
        added_instructions=extras,
    )
    return {
        "handled": True,
        "text": f"Folded into the draft:\n- {clean}\n\n{prompt}",
        "reply_markup": intake_markup(token),
        "token": token,
        "action": "add",
        "added_instructions": extras,
    }


def _discard(token: str, *, chat_id: int | str | None) -> dict[str, Any]:
    now = time.time()
    with engine.begin() as conn:
        changed = conn.execute(text("""
            UPDATE whatsapp_intake_approvals
            SET status='discarded', telegram_chat_id=COALESCE(:chat, telegram_chat_id),
                updated_at=:now
            WHERE token=:token AND status IN ('pending', 'awaiting_add')
        """), {
            "chat": str(chat_id) if chat_id is not None else None,
            "now": now,
            "token": token,
        }).rowcount
    if not changed:
        return {"handled": True, "text": "That WhatsApp intake was already handled."}
    return {"handled": True, "text": "Discarded that WhatsApp work. No ticket was created.", "action": "no", "token": token}


def _mark_awaiting_add(token: str, *, chat_id: int | str | None) -> dict[str, Any]:
    now = time.time()
    with engine.begin() as conn:
        changed = conn.execute(text("""
            UPDATE whatsapp_intake_approvals
            SET status='awaiting_add', telegram_chat_id=COALESCE(:chat, telegram_chat_id),
                updated_at=:now
            WHERE token=:token AND status IN ('pending', 'awaiting_add')
        """), {
            "chat": str(chat_id) if chat_id is not None else None,
            "now": now,
            "token": token,
        }).rowcount
    if not changed:
        return {"handled": True, "text": "That WhatsApp intake was already handled."}
    return {
        "handled": True,
        "text": "Send the instruction to fold into the ticket (plain text). When ready, say **ready** / **build it** / **create ticket**, or tap **Yes**.",
        "reply_markup": intake_markup(token),
        "token": token,
        "action": "add_prompt",
    }


def _execute_ready(token: str, *, chat_id: int | str | None) -> dict[str, Any]:
    """Readiness signal: create ticket, Development thread, start time via WorkIntake."""
    row = _row(token)
    if not row:
        return {"handled": True, "text": "That WhatsApp intake approval no longer exists."}
    if row["status"] not in {"pending", "awaiting_add"}:
        if row.get("ticket_id"):
            return {
                "handled": True,
                "text": f"Already created ticket #{row['ticket_id']}.",
                "ticket_id": row["ticket_id"],
                "workflow_run_id": row.get("workflow_run_id"),
                "development_chat_id": row.get("development_chat_id"),
                "action": "ready",
                "idempotent": True,
            }
        return {"handled": True, "text": "That WhatsApp intake was already handled."}

    now = time.time()
    with engine.begin() as conn:
        claimed = conn.execute(text("""
            UPDATE whatsapp_intake_approvals
            SET status='creating', telegram_chat_id=COALESCE(:chat, telegram_chat_id),
                updated_at=:now
            WHERE token=:token AND status IN ('pending', 'awaiting_add')
        """), {
            "chat": str(chat_id) if chat_id is not None else None,
            "now": now,
            "token": token,
        }).rowcount
    if not claimed:
        return {"handled": True, "text": "That WhatsApp intake is already being created."}

    try:
        from distr.core.work_intake import WorkIntake, get_work_intake_service

        payload = json.loads(row.get("intake_json") or "{}") or {}
        if not isinstance(payload, dict):
            payload = {}
        extras = _parse_added(row)
        draft = str(row.get("draft_text") or payload.get("user_text") or "").strip()
        if extras:
            draft = draft + "\n\nAdditional instructions:\n" + "\n".join(f"- {x}" for x in extras)
        meta = dict(payload.get("metadata") or {})
        meta["intake_approval_confirmed"] = True
        meta["intake_approval_token"] = token
        meta["intake_added_instructions"] = extras
        payload["user_text"] = draft
        payload["metadata"] = meta
        # Prefer durable create+workflow path after human readiness.
        if not str(payload.get("user_text") or "").lower().startswith(("create a ticket", "create ticket")):
            if str(row.get("classified_action") or "") in {"create_ticket", "run_workflow"}:
                payload["user_text"] = f"Create a ticket: {draft}"

        intake = WorkIntake.from_payload(payload)
        decision = get_work_intake_service().ingest(intake, execute=True)
        ticket_id = decision.ticket_id
        run_id = decision.workflow_run_id
        dev_chat = None
        if isinstance(decision.diagnostics, dict):
            dev_chat = decision.diagnostics.get("development_chat_id")
        if run_id and not dev_chat:
            try:
                from distr.core.db import get_session
                from distr.core.db.workflow import AutoWorkflowRun
                from distr.core.workflow.development_threads import development_thread_metadata

                with get_session() as db:
                    run = db.get(AutoWorkflowRun, int(run_id))
                    if run and run.chat_id:
                        dev_chat = int(run.chat_id)
            except Exception:
                logger.debug("Could not resolve Development chat for intake readiness", exc_info=True)

        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE whatsapp_intake_approvals
                SET status='created', ticket_id=:ticket_id, workflow_run_id=:run_id,
                    development_chat_id=:dev, updated_at=:now, error=NULL
                WHERE token=:token
            """), {
                "ticket_id": int(ticket_id) if ticket_id is not None else None,
                "run_id": int(run_id) if run_id is not None else None,
                "dev": int(dev_chat) if dev_chat is not None else None,
                "now": time.time(),
                "token": token,
            })
        bits = [f"Created ticket #{ticket_id}" if ticket_id else "Ticket create finished"]
        if run_id:
            bits.append(f"workflow run #{run_id}")
        if dev_chat:
            bits.append(f"Development thread #{dev_chat}")
        bits.append("time tracking starts with the Development thread.")
        return {
            "handled": True,
            "text": ". ".join(bits),
            "action": "ready",
            "token": token,
            "ticket_id": ticket_id,
            "workflow_run_id": run_id,
            "development_chat_id": dev_chat,
            "decision": decision.to_dict() if hasattr(decision, "to_dict") else None,
        }
    except Exception as exc:
        logger.exception("WhatsApp intake readiness create failed token=%s", token)
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE whatsapp_intake_approvals
                SET status='pending', error=:error, updated_at=:now
                WHERE token=:token AND status='creating'
            """), {"error": str(exc)[:1000], "now": time.time(), "token": token})
        return {
            "handled": True,
            "text": f"Could not create the ticket yet: {exc}. It is still pending — say ready again when fixed.",
            "reply_markup": intake_markup(token),
            "token": token,
            "action": "ready_failed",
            "error": str(exc),
        }


def handle_telegram_reply(
    value: str,
    *,
    chat_id: int | str | None = None,
) -> dict[str, Any] | None:
    """Resolve wi: callbacks and plain yes/no/add/ready replies for pending WA intake."""
    ensure_tables()
    clean = str(value or "").strip()
    callback = re.fullmatch(r"wi:([A-Za-z0-9_-]+):(yes|no|add|ready)", clean, re.I)
    if callback:
        token, action = callback.group(1), callback.group(2).lower()
        if action in {"yes", "ready"}:
            return _execute_ready(token, chat_id=chat_id)
        if action == "no":
            return _discard(token, chat_id=chat_id)
        return _mark_awaiting_add(token, chat_id=chat_id)

    pending = _pending_for_chat(chat_id)
    if len(pending) != 1:
        return None
    row = pending[0]
    token = str(row["token"])

    # After Add button, next non-empty plain text folds.
    if row["status"] == "awaiting_add" and clean:
        return _fold_add(token, clean, chat_id=chat_id)

    add_match = _ADD_PREFIX_RE.match(clean)
    if add_match:
        return _fold_add(token, add_match.group(1), chat_id=chat_id)

    low = re.sub(r"\s+", " ", clean.lower()).strip(" .!?")
    if low in READINESS_PHRASES:
        return _execute_ready(token, chat_id=chat_id)
    if _NO_RE.match(clean):
        return _discard(token, chat_id=chat_id)
    return None
