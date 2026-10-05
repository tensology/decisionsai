"""Project-linked WhatsApp batch → one Telegram approval → execute ticket.

Pre-ticket gate for WhatsApp-sourced work. Classified CREATE_TICKET /
RUN_WORKFLOW requests are staged until the operator confirms on Telegram.

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

# Exact readiness phrases the operator (or voice) can say to create the ticket.
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
        columns = {
            str(row[1])
            for row in conn.execute(text("PRAGMA table_info(whatsapp_intake_approvals)"))
        }
        if "source_fingerprint" not in columns:
            conn.execute(text(
                "ALTER TABLE whatsapp_intake_approvals ADD COLUMN source_fingerprint VARCHAR"
            ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_whatsapp_intake_approvals_source "
            "ON whatsapp_intake_approvals(source_fingerprint, status)"
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
        "**Linked inbound work ready for your call**",
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
        "Reply **Yes** / **ready** / **build it** to snapshot the incoming WhatsApp messages "
        "into a ticket and run that ticket as a thread. This is the only pre-work approval.",
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
    source_fingerprint: str = "",
) -> dict[str, Any]:
    """Persist a pending WA intake approval and return token + prompt fields."""
    ensure_tables()
    token = secrets.token_urlsafe(12)
    now = time.time()
    draft = (draft_text or str(intake_payload.get("user_text") or intake_payload.get("transcript") or "")).strip()
    fingerprint = str(source_fingerprint or "").strip()
    with engine.begin() as conn:
        if fingerprint:
            existing = conn.execute(text("""
                SELECT * FROM whatsapp_intake_approvals
                WHERE source_fingerprint=:fingerprint
                  AND status IN ('pending', 'awaiting_add', 'creating', 'created')
                ORDER BY id DESC LIMIT 1
            """), {"fingerprint": fingerprint}).mappings().first()
            if existing:
                row = dict(existing)
                extras = _parse_added(row)
                return {
                    "token": row["token"],
                    "status": row["status"],
                    "draft_text": row.get("draft_text") or draft,
                    "classified_action": row.get("classified_action") or classified_action,
                    "classified_reason": row.get("classified_reason") or classified_reason,
                    "added_instructions": extras,
                    "reply_markup": intake_markup(str(row["token"])),
                    "text": format_approval_prompt(
                        draft_text=str(row.get("draft_text") or draft),
                        classified_action=str(row.get("classified_action") or classified_action),
                        classified_reason=str(row.get("classified_reason") or classified_reason),
                        added_instructions=extras,
                    ),
                    "deduplicated": True,
                }
        conn.execute(text("""
            INSERT INTO whatsapp_intake_approvals (
                token, status, intake_json, classified_action, classified_reason,
                draft_text, added_instructions, created_at, updated_at, source_fingerprint
            ) VALUES (
                :token, 'pending', :intake_json, :action, :reason,
                :draft, '[]', :now, :now, :fingerprint
            )
        """), {
            "token": token,
            "intake_json": json.dumps(intake_payload),
            "action": str(classified_action or "create_ticket"),
            "reason": str(classified_reason or "")[:500],
            "draft": draft,
            "now": now,
            "fingerprint": fingerprint or None,
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
        "deduplicated": False,
    }


def stage_linked_whatsapp_batch(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a scanner batch and stage exactly one create-and-execute approval."""
    from distr.core.db import WhatsAppMessage, get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project
    from distr.core.kanban.whatsapp_intake_rules import resolve_whatsapp_link

    message_ids = sorted({int(value) for value in payload.get("message_ids") or [] if str(value).isdigit()})
    board_id = int(payload.get("linked_board_id") or 0)
    project_id = int(payload.get("linked_project_id") or 0)
    jid_phone = str(payload.get("jid_phone") or "").strip()
    jid = str(payload.get("jid") or "").strip()
    if not message_ids or not board_id or not project_id or not (jid_phone or jid):
        raise ValueError("WhatsApp batch is missing its message, board, project, or group identity")

    link = resolve_whatsapp_link(jid, jid_phone=jid_phone)
    if link.board_id != board_id or link.project_id != project_id:
        raise ValueError("WhatsApp group is no longer linked to the approved board and project")

    with get_session() as session:
        board = session.query(KanbanBoard).filter(KanbanBoard.id == board_id).first()
        project = session.query(Project).filter(Project.id == project_id).first()
        if (
            not board
            or bool(getattr(board, "archived", False))
            or not project
            or int(board.default_project_id or 0) != project_id
        ):
            raise ValueError("WhatsApp board does not resolve to a valid project")
        messages = (
            session.query(WhatsAppMessage)
            .filter(WhatsAppMessage.id.in_(message_ids))
            .order_by(WhatsAppMessage.whatsapp_timestamp.asc(), WhatsAppMessage.id.asc())
            .all()
        )
        if len(messages) != len(message_ids):
            raise ValueError("One or more WhatsApp batch messages no longer exist")
        if any(
            bool(getattr(message, "processed", False))
            or bool(getattr(message, "snapshot_group", None))
            for message in messages
        ):
            raise ValueError("One or more WhatsApp batch messages were already consumed")
        expected_phone = jid_phone or jid.split("@", 1)[0]
        if any(
            bool(getattr(message, "from_me", False))
            or (str(getattr(message, "jid_phone", "") or "") != expected_phone
                and str(getattr(message, "jid", "") or "") != jid)
            for message in messages
        ):
            raise ValueError("WhatsApp batch contains a message outside the linked group")
        contact = str(
            payload.get("latest_sender")
            or getattr(messages[-1], "sender_push_name", "")
            or expected_phone
        ).strip()
        transcript = []
        for message in messages:
            body = str(
                getattr(message, "text", None)
                or getattr(message, "caption", None)
                or f"[{getattr(message, 'media_type', None) or 'message'}]"
            ).strip()
            transcript.append(f"{contact}: {body}")
        board_name = str(getattr(board, "name", "") or f"Board {board_id}")
        project_name = str(getattr(project, "name", "") or f"Project {project_id}")

    fingerprint = "whatsapp:" + ",".join(str(value) for value in message_ids)
    draft = "\n".join(transcript)
    intake_payload = {
        "source": "whatsapp",
        "user_text": draft,
        "source_user_id": contact,
        "source_thread_id": jid or expected_phone,
        "source_message_id": fingerprint,
        "project_hint": str(project_id),
        "board_hint": str(board_id),
        "requested_outcome": "Compact the linked WhatsApp batch into one ticket and execute it end to end.",
        "intake_uid": fingerprint,
        "metadata": {
            "linked_intake_authorized": True,
            "linked_board_id": board_id,
            "linked_project_id": project_id,
            "linked_board_name": board_name,
            "linked_project_name": project_name,
            "jid": jid,
            "jid_phone": expected_phone,
            "message_ids": message_ids,
            "contact_name": contact,
            "skip_human_checkpoints": True,
        },
    }
    return stage_whatsapp_intake(
        intake_payload=intake_payload,
        classified_action="run_workflow",
        classified_reason=f"Project-linked WhatsApp batch for {project_name} on {board_name}",
        draft_text=draft,
        source_fingerprint=fingerprint,
    )


def stage_linked_email_batch(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate an email sender mapping and stage one create-and-execute approval."""
    from distr.core.kanban.email_intake_rules import normalize_email, resolve_email_link

    messages = [row for row in payload.get("messages") or [] if isinstance(row, dict)]
    message_ids = [str(value).strip() for value in payload.get("message_ids") or [] if str(value).strip()]
    sender = normalize_email(str(payload.get("sender_email") or ""))
    board_id = int(payload.get("linked_board_id") or 0)
    project_id = int(payload.get("linked_project_id") or 0)
    if not messages or not message_ids or not sender or not board_id or not project_id:
        raise ValueError("Email batch is missing its message, sender, board, or project identity")
    link = resolve_email_link(sender)
    if link.board_id != board_id or link.project_id != project_id:
        raise ValueError("Email sender is no longer linked to the approved board and project")
    if any(normalize_email(str(row.get("from") or "")) != sender for row in messages):
        raise ValueError("Email batch contains a message outside the linked sender")

    transcript = []
    for row in messages:
        subject = str(row.get("subject") or "(no subject)").strip()
        body = str(row.get("body") or row.get("snippet") or "").strip()
        transcript.append(f"From: {sender}\nSubject: {subject}\n{body}")
    provider = str(messages[-1].get("source") or "gmail").strip().lower()
    source = "gmail"
    thread_id = str(messages[-1].get("thread_id") or "").strip()
    latest_message_id = str(messages[-1].get("id") or message_ids[-1]).strip()
    fingerprint = f"email:{provider}:" + ",".join(sorted(message_ids))
    draft = "\n\n---\n\n".join(transcript)
    intake_payload = {
        "source": source,
        "user_text": draft,
        "source_user_id": sender,
        "source_thread_id": thread_id,
        "source_message_id": latest_message_id,
        "project_hint": str(project_id),
        "board_hint": str(board_id),
        "requested_outcome": "Compact the linked email batch into one ticket and execute it end to end.",
        "intake_uid": fingerprint,
        "metadata": {
            "linked_intake_authorized": True,
            "linked_intake_source": "email",
            "linked_board_id": board_id,
            "linked_project_id": project_id,
            "linked_board_name": link.board_name,
            "linked_project_name": link.project_name,
            "email_sender": sender,
            "email_provider": provider,
            "message_ids": message_ids,
            "thread_ids": [str(value) for value in payload.get("thread_ids") or []],
            "skip_human_checkpoints": True,
        },
    }
    return stage_whatsapp_intake(
        intake_payload=intake_payload,
        classified_action="run_workflow",
        classified_reason=f"Project-linked email batch for {link.project_name} on {link.board_name}",
        draft_text=draft,
        source_fingerprint=fingerprint,
    )


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
    """Send the yes/no/add card to the operator's Telegram DM."""
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

    try:
        payload = json.loads(row.get("intake_json") or "{}") or {}
        validation_error = _linked_scope_error(payload)
    except Exception as exc:
        validation_error = str(exc)
    if validation_error:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE whatsapp_intake_approvals
                SET status='rejected_scope', error=:error, updated_at=:now
                WHERE token=:token AND status IN ('pending', 'awaiting_add')
            """), {"error": validation_error[:1000], "now": time.time(), "token": token})
        return {
            "handled": True,
            "text": f"I did not create or execute anything: {validation_error}.",
            "token": token,
            "action": "scope_rejected",
        }

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
        if meta.get("linked_intake_authorized"):
            meta["skip_human_checkpoints"] = True
        payload["user_text"] = draft
        payload["metadata"] = meta
        # Prefer durable create+workflow path after human readiness.
        if str(row.get("classified_action") or "") == "run_workflow":
            payload["user_text"] = (
                "Execute this work by creating one ticket and running its linked workflow:\n\n"
                f"{draft}"
            )
        elif not str(payload.get("user_text") or "").lower().startswith(("create a ticket", "create ticket")):
            payload["user_text"] = f"Create a ticket: {draft}"

        intake = WorkIntake.from_payload(payload)
        decision = get_work_intake_service().ingest(intake, execute=True)
        ticket_id = decision.ticket_id
        run_id = decision.workflow_run_id
        dev_chat = None
        if isinstance(decision.diagnostics, dict):
            dev_chat = decision.diagnostics.get("development_chat_id")
        if ticket_id:
            _mark_batch_snapshot(payload, int(ticket_id))
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


def _linked_scope_error(payload: dict[str, Any]) -> str:
    """Return a reason when a pending intake no longer has the exact live link."""
    meta = dict(payload.get("metadata") or {})
    if not meta.get("linked_intake_authorized"):
        return "the WhatsApp intake has no verified linked-group authorization"
    board_id = int(meta.get("linked_board_id") or 0)
    project_id = int(meta.get("linked_project_id") or 0)
    if meta.get("linked_intake_source") == "email":
        from distr.core.kanban.email_intake_rules import resolve_email_link

        link = resolve_email_link(str(meta.get("email_sender") or payload.get("source_user_id") or ""))
        if not board_id or not project_id or link.board_id != board_id or link.project_id != project_id:
            return "the originating email sender is not linked to the same valid board and project"
        return ""
    from distr.core.kanban.whatsapp_intake_rules import resolve_whatsapp_link

    jid = str(meta.get("jid") or payload.get("source_thread_id") or "").strip()
    phone = str(meta.get("jid_phone") or "").strip()
    link = resolve_whatsapp_link(jid, jid_phone=phone)
    if not board_id or not project_id or link.board_id != board_id or link.project_id != project_id:
        return "the originating WhatsApp group is not linked to the same valid board and project"
    return ""


def _mark_batch_snapshot(payload: dict[str, Any], ticket_id: int) -> None:
    if str(payload.get("source") or "").lower() != "whatsapp":
        return
    from distr.core.db import WhatsAppMessage, get_session

    meta = dict(payload.get("metadata") or {})
    ids = [int(value) for value in meta.get("message_ids") or [] if str(value).isdigit()]
    if not ids:
        return
    with get_session() as session:
        rows = session.query(WhatsAppMessage).filter(WhatsAppMessage.id.in_(ids)).all()
        for row in rows:
            row.processed = True
            row.snapshot_group = f"ticket:{ticket_id}"


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
