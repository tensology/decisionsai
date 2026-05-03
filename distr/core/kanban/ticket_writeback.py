"""Append bounded Pi / CLI completion notes onto Kanban ticket descriptions."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def apply_pi_cli_note_to_ticket(
    ticket: Any,
    summary: str,
    outcome_status: str = "completed",
    max_desc_len: int = 12000,
    max_summary_chars: int = 4000,
) -> None:
    """Update ticket.description and workflow_status with a Pi CLI completion block."""
    status_label = (outcome_status or "completed").strip().lower()
    if hasattr(ticket, "workflow_status"):
        ticket.workflow_status = status_label

    body = (summary or "").strip()
    if len(body) > max_summary_chars:
        body = body[:max_summary_chars] + "..."

    lines = [f"[Pi CLI] Status: {status_label}"]
    if body:
        lines.append(body)
    note = "\n".join(lines).strip()

    existing = (getattr(ticket, "description", None) or "").strip()
    if existing:
        ticket.description = f"{existing}\n\n{note}"
    else:
        ticket.description = note

    if len(ticket.description) > max_desc_len:
        ticket.description = ticket.description[-max_desc_len:]


def append_pi_cli_summary_to_ticket(
    ticket_id: int,
    summary: str,
    outcome_status: str = "completed",
) -> None:
    """Load ticket by id, apply Pi CLI note, commit. Safe no-op if missing."""
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket

    try:
        with get_session() as db:
            ticket = db.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
            if not ticket:
                logger.debug("append_pi_cli_summary_to_ticket: ticket %s not found", ticket_id)
                return
            apply_pi_cli_note_to_ticket(ticket, summary, outcome_status=outcome_status)
            db.commit()
    except Exception:
        logger.debug("append_pi_cli_summary_to_ticket failed for ticket %s", ticket_id, exc_info=True)
