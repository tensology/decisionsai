"""Thin work operator: intake → run → status → client draft → send/revise.

Chat/voice/Telegram dispatch here. Execution stays on project CLI or workflow.
No new chat-stream runtime.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


def _in_use_board_id() -> int:
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard

    with get_session() as db:
        board = (
            db.query(KanbanBoard)
            .filter(KanbanBoard.in_use.is_(True), KanbanBoard.archived.is_(False))
            .order_by(KanbanBoard.id.desc())
            .first()
        )
        if not board:
            board = (
                db.query(KanbanBoard)
                .filter(KanbanBoard.archived.is_(False), KanbanBoard.source == "database")
                .order_by(KanbanBoard.id.desc())
                .first()
            )
        return int(board.id) if board else 0


def work_intake(
    *,
    board_id: int | None = None,
    messages: list[dict[str, Any]] | None = None,
    keys: list[str] | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    """Batch Jira/email intake onto the board and Telegram-digest it."""
    from distr.core.kanban.jira_intake import run_jira_morning_intake

    resolved = int(board_id or 0) or _in_use_board_id()
    if not resolved:
        return {
            "success": False,
            "action": "intake",
            "spoken_summary": "No Ticket Board is ready for intake yet.",
        }

    mail_source = None
    if messages is None and keys is None:
        from distr.core.kanban.jira_intake import load_intake_email_messages

        loaded = load_intake_email_messages()
        messages = list(loaded.get("messages") or [])
        mail_source = loaded.get("source")

    result = run_jira_morning_intake(
        board_id=resolved,
        messages=messages or [],
        keys=keys,
        notify=notify,
    )
    created = result.get("created") or []
    reason = str(result.get("reason") or "")
    source_bit = f" via {mail_source}" if mail_source else ""
    if reason == "ok" and created:
        notified = bool(result.get("notified"))
        spoken = (
            f"Staged {len(created)} item(s){source_bit} on your board"
            + (" and sent a Telegram digest." if notified else ".")
            + " Nothing sent to any client yet."
        )
    elif reason == "all_known":
        spoken = "Those issues are already on the board. Nothing new to stage."
    elif reason == "no_keys":
        spoken = "No new Jira notification items to intake."
        if mail_source is None and messages is None and keys is None:
            spoken = "No Mailshot or Gmail Jira notifications found for intake."
    else:
        spoken = f"Intake finished with nothing new ({reason})."
    return {
        "success": reason == "ok",
        "action": "intake",
        "spoken_summary": spoken,
        "board_id": resolved,
        "mail_source": mail_source,
        "result": result,
    }


def work_run(ticket_ids: Iterable[int]) -> dict[str, Any]:
    """Dispatch ticket work to project CLI (preferred) or workflow."""
    from distr.core.kanban.jira_intake import start_execution_for_tickets

    ids = [int(x) for x in ticket_ids if x]
    if not ids:
        return {
            "success": False,
            "action": "run",
            "spoken_summary": "Tell me which ticket number to run.",
        }
    result = start_execution_for_tickets(ids)
    started = result.get("started") or []
    cli = sum(1 for row in started if row.get("dispatched") == "cli")
    wf = sum(1 for row in started if row.get("dispatched") == "workflow")
    none = sum(1 for row in started if row.get("dispatched") == "none")
    bits = []
    if cli:
        bits.append(f"{cli} via project CLI")
    if wf:
        bits.append(f"{wf} via workflow")
    if none:
        bits.append(f"{none} waiting for a linked project or workflow")
    spoken = "Started " + (", ".join(bits) if bits else "nothing") + ". I'll ask on Telegram before any client send."
    return {
        "success": not result.get("error"),
        "action": "run",
        "spoken_summary": spoken,
        "started": started,
        "error": result.get("error"),
    }


def work_status(ticket_id: int | None = None) -> dict[str, Any]:
    """Summarize lifecycle status for one ticket or recent Jira-sourced work."""
    from sqlalchemy import text

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket
    from distr.core.kanban import jira_work_lifecycle as jlife

    jlife.ensure_tables()
    lines: list[str] = []
    with jlife.engine.connect() as conn:
        if ticket_id:
            rows = conn.execute(text(
                "SELECT ticket_id, issue_key, status, execution_kind, review_status, time_spent_snapshot "
                "FROM jira_work_lifecycles WHERE ticket_id=:id"
            ), {"id": int(ticket_id)}).mappings().all()
        else:
            rows = conn.execute(text(
                "SELECT ticket_id, issue_key, status, execution_kind, review_status, time_spent_snapshot "
                "FROM jira_work_lifecycles ORDER BY updated_at DESC LIMIT 8"
            )).mappings().all()
    if not rows:
        return {
            "success": True,
            "action": "status",
            "spoken_summary": "No tracked work lifecycles yet.",
            "rows": [],
        }
    with get_session() as db:
        for row in rows:
            ticket = db.get(KanbanTicket, int(row["ticket_id"]))
            title = (ticket.title if ticket else "") or row["issue_key"] or f"#{row['ticket_id']}"
            time_bit = f", time {row['time_spent_snapshot']}" if row.get("time_spent_snapshot") else ""
            lines.append(
                f"#{row['ticket_id']} {title}: {row['status']}"
                f" ({row.get('execution_kind') or 'pending'}{time_bit})"
            )
    spoken = "Work status:\n" + "\n".join(lines)
    return {"success": True, "action": "status", "spoken_summary": spoken, "rows": [dict(r) for r in rows]}


def work_draft(ticket_id: int) -> dict[str, Any]:
    """Show the pending client draft for a ticket, if any."""
    from sqlalchemy import text

    from distr.core.kanban import jira_work_lifecycle as jlife

    jlife.ensure_tables()
    with jlife.engine.connect() as conn:
        row = conn.execute(text(
            "SELECT comment_draft, status, issue_key, outbound_channel FROM jira_work_lifecycles "
            "WHERE ticket_id=:id"
        ), {"id": int(ticket_id)}).mappings().first()
    if not row or not row.get("comment_draft"):
        return {
            "success": False,
            "action": "draft",
            "spoken_summary": f"No client draft waiting on ticket #{ticket_id}.",
        }
    return {
        "success": True,
        "action": "draft",
        "spoken_summary": (
            f"Draft for {row.get('issue_key') or ticket_id} "
            f"({row.get('outbound_channel') or 'client'}, {row.get('status')}):\n\n{row['comment_draft']}"
        ),
        "draft": row["comment_draft"],
    }


def work_send(ticket_id: int, *, chat_id: int | str | None = None) -> dict[str, Any]:
    """Approve send for the pending client draft (same gate as Telegram Send)."""
    from sqlalchemy import text

    from distr.core.kanban import jira_work_lifecycle as jlife

    jlife.ensure_tables()
    with jlife.engine.connect() as conn:
        token = conn.execute(text("""
            SELECT r.token FROM jira_reply_reviews r
            JOIN jira_work_lifecycles l ON l.id=r.lifecycle_id
            WHERE l.ticket_id=:id AND r.status='pending'
            ORDER BY r.created_at DESC LIMIT 1
        """), {"id": int(ticket_id)}).scalar()
    if not token:
        return {
            "success": False,
            "action": "send",
            "spoken_summary": f"No pending client send for ticket #{ticket_id}.",
        }
    result = jlife.handle_telegram_jira_reply(f"jr:{token}:send", chat_id=chat_id)
    return {
        "success": bool(result and "Sent" in str(result.get("text") or "")),
        "action": "send",
        "spoken_summary": (result or {}).get("text") or "Send failed.",
        "result": result,
    }


def work_complete_simulated(
    *,
    ticket_id: int,
    run_id: int = 1,
    result_summary: str = "",
    notify: bool = True,
) -> dict[str, Any]:
    """Mark completion and prepare client review (used by tests and CLI completion hooks)."""
    from distr.core.kanban.jira_work_lifecycle import (
        notify_telegram_jira_review,
        prepare_completed_jira_review,
    )

    review = prepare_completed_jira_review(
        ticket_id=int(ticket_id),
        run_id=int(run_id),
        status="completed",
        result_summary=result_summary,
    )
    if not review:
        return {
            "success": False,
            "action": "complete",
            "spoken_summary": f"No lifecycle for ticket #{ticket_id}, or run was not completed.",
        }
    if notify:
        notify_telegram_jira_review(review)
    return {
        "success": True,
        "action": "complete",
        "spoken_summary": "Work finished. Client draft is ready for Telegram review.",
        "review": review,
    }
