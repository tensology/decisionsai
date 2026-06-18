"""Ticket CLI dispatch: memory enrichment and workflow run wiring for IDE bridge callbacks."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session


def enrich_kanban_ticket_cli_instruction(
    session: Session,
    *,
    ticket_id: int,
    project_id: int,
    board_id: int | None,
    base_instruction: str,
    run_id: int | None = None,
    linked_workflow_id: int | None = None,
) -> str:
    """Prepend workspace pickup brief and quality standards to a ticket CLI instruction."""
    instruction = (base_instruction or "").strip()
    parts: list[str] = []

    try:
        from distr.core.workspace_memory.lifecycle import hook_ensure_workspace
        from distr.core.workspace_memory.pickup_handoff import build_pickup_brief, load_decisions_json

        hook_ensure_workspace("tickets", ticket_id, reason="send_to_cli")
        hook_ensure_workspace("projects", project_id, reason="send_to_cli")
        if linked_workflow_id:
            hook_ensure_workspace("workflows", int(linked_workflow_id), reason="send_to_cli")

        brief = build_pickup_brief(
            entity_type="tickets",
            entity_id=ticket_id,
            decisions=load_decisions_json("tickets", ticket_id),
        )
        if brief.strip():
            parts.append(brief.strip())
    except Exception:
        pass

    try:
        from distr.core.workflow.standards_memory import build_standards_context

        standards = build_standards_context(board_id=board_id)
        if standards.strip():
            parts.append(standards.strip())
    except Exception:
        pass

    if run_id:
        try:
            from distr.core.workflow.steering_memory import build_steering_context_for_run_id

            steering = build_steering_context_for_run_id(run_id)
            if steering.strip():
                parts.append(steering.strip())
        except Exception:
            pass

    if not parts:
        return instruction
    prefix = "\n\n---\n\n".join(parts)
    if not instruction:
        return prefix
    return f"{prefix}\n\n---\n\n{instruction}"


def create_ticket_cli_dispatch_run(
    session: Session,
    *,
    audit_workflow_id: int,
    step_id: int,
    ticket_id: int,
    project_id: int,
    board_id: int | None,
    ide_mode: bool,
    backend_id: str,
) -> int:
    """Create a workflow run so IDE plugin bridge URLs resolve for kanban ticket pushes."""
    from distr.core.db.workflow import AutoWorkflowRun

    run_data: dict[str, Any] = {
        "project_id": project_id,
        "ticket_dispatch": True,
        "dispatch_backend": backend_id,
    }
    if ide_mode:
        run_data["waiting_kind"] = "ide_handoff"
        run_data["ide_handoff_pending"] = True

    run = AutoWorkflowRun(
        workflow_id=int(audit_workflow_id),
        status="waiting" if ide_mode else "running",
        current_step_id=int(step_id),
        ticket_id=int(ticket_id),
        board_id=int(board_id) if board_id else None,
        run_data=json.dumps(run_data),
    )
    session.add(run)
    session.flush()
    return int(run.id)
