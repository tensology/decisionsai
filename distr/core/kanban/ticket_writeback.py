"""Append bounded Pi / CLI completion notes onto Kanban ticket descriptions."""

import logging
from typing import Any

from distr.core.kanban.result_packet import build_result_packet, format_result_packet_note
from distr.core.kanban.ticket_audit import append_ticket_audit_entry
from distr.core.kanban.evidence import format_evidence_block
from distr.core.workflow.risk_and_audit import (
    infer_risk_profile,
    build_audit_gates,
    validation_rules_for_risk,
)

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

    risk = infer_risk_profile(f"{getattr(ticket, 'title', '')}\n{summary or ''}")
    validation_rules = validation_rules_for_risk(
        risk.get("level", "low"),
        risk.get("signals", []),
    )
    packet = build_result_packet(
        ticket_id=str(getattr(ticket, "id", "") or ""),
        board_id=str(getattr(ticket, "board_id", "") or "") if getattr(ticket, "board_id", None) is not None else None,
        execution_lane="cli",
        status=status_label,
        summary=body or "CLI execution completed.",
        commands_run=["pi rpc send_prompt", "lint (recommended)", "typecheck (recommended)", "build (recommended)", "tests (recommended)"],
        tests_run=[],
        test_results=[],
        assumptions=[
            f"risk_level={risk.get('level', 'low')}",
            f"risk_type={risk.get('risk_type', 'standard')}",
        ],
        limitations=["Summary-only writeback (raw CLI transcript remains in session logs)."],
        next_recommended=[
            "Review CLI changes and run project checks if not already executed.",
            "For high-risk tasks, run deterministic CLI validation: lint, typecheck, build, tests.",
        ] + validation_rules[:4],
        logs=["pi_rpc_session_buffer"],
        audits_run=build_audit_gates(
            status=status_label,
            risk_level=risk.get("level", "low"),
            tests_passed=status_label != "failed",
        ),
        final_verdict="needs_changes" if status_label == "failed" else "pass",
        audit_rationale="Inferred from latest Pi turn outcome and tool error signals.",
    )
    note = format_result_packet_note(packet, title="Pi CLI")
    note = f"{note}\n\n{format_evidence_block()}"

    existing = (getattr(ticket, "description", None) or "").strip()
    if existing:
        ticket.description = f"{existing}\n\n{note}"
    else:
        ticket.description = note

    if len(ticket.description) > max_desc_len:
        keep = max(0, max_desc_len - 3)
        ticket.description = "..." + ticket.description[-keep:]


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
            append_ticket_audit_entry(
                db,
                ticket_id=ticket.id,
                run_id=None,
                step_id=None,
                step_result_id=None,
                execution_lane="cli",
                status=(outcome_status or "completed").strip().lower(),
                final_verdict="needs_changes" if (outcome_status or "").strip().lower() == "failed" else "pass",
                summary="Pi CLI writeback",
                details=(summary or "")[:3000],
            )
            db.commit()
    except Exception:
        logger.debug("append_pi_cli_summary_to_ticket failed for ticket %s", ticket_id, exc_info=True)
