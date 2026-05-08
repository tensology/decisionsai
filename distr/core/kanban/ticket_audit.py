"""Ticket audit table writers."""

from typing import Optional
from sqlalchemy import inspect

from distr.core.db.kanban import KanbanTicketAuditEntry


def append_ticket_audit_entry(
    db,
    *,
    ticket_id: int,
    run_id: Optional[int],
    step_id: Optional[int],
    step_result_id: Optional[int],
    execution_lane: str,
    status: str,
    final_verdict: Optional[str],
    summary: str,
    details: str = "",
) -> None:
    try:
        bind = getattr(db, "bind", None)
        if bind is not None:
            if not inspect(bind).has_table("kanban_ticket_audit_entries"):
                return
    except Exception:
        # Never block workflow/ticket execution on audit telemetry checks.
        return
    db.add(
        KanbanTicketAuditEntry(
            ticket_id=ticket_id,
            run_id=run_id,
            step_id=step_id,
            step_result_id=step_result_id,
            execution_lane=(execution_lane or "cursor").strip().lower(),
            status=(status or "pending").strip().lower(),
            final_verdict=(final_verdict or "").strip() or None,
            summary=(summary or "").strip()[:1000],
            details=(details or "").strip()[:8000] or None,
        ),
    )
