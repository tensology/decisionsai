"""Spawn a workflow for a ticket when none exists yet — preset or explicit steps, then run."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project

logger = logging.getLogger(__name__)

# The retired "Development" preset is no longer a default. Tickets route to a
# managed weight-class workflow, and an explicit `steps=` payload is the only way
# to build a bespoke definition here.


def _ticket_workflow_name(ticket_title: str, *, stamp: str = "") -> str:
    base = re.sub(r"\s+", " ", (ticket_title or "Ticket").strip())[:72]
    suffix = f" ({stamp})" if stamp else ""
    return f"{base} loop{suffix}".strip()


def spawn_workflow_for_ticket(
    ticket_id: int,
    *,
    # Accepted for API compatibility. The retired preset path is gone: pass an
    # explicit `steps=` payload, or let the ticket route to a managed workflow.
    preset_slug: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    workflow_name: str | None = None,
    workflow_input: dict[str, Any] | None = None,
    link_board_default: bool = False,
    start_run: bool = True,
    skip_human_checkpoints: bool = False,
    force: bool = False,
    run_metadata: dict[str, Any] | None = None,
    dispatch_async: bool = True,
) -> dict[str, Any]:
    """Create workflow + steps for a ticket, link it, optionally start the run.

    Returns workflow_id, run_id (when started), step_count.
    Does not require a pre-existing workflow on the board.
    """
    from distr.core.workflow.import_export import import_workflow
    from distr.core.workflow.service import get_workflow

    ticket_title = ""
    ticket_description = ""
    board_id: int | None = None
    project_id: int | None = None
    project_name = ""
    project_folder = ""
    existing_workflow_id: int | None = None

    with get_session() as db:
        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
        if not ticket:
            return {"success": False, "error": "Ticket not found"}
        ticket_title = (ticket.title or "").strip()
        ticket_description = (ticket.description or "").strip()
        existing_workflow_id = ticket.linked_workflow_id
        lane = db.query(KanbanLane).filter(KanbanLane.id == ticket.lane_id).first() if ticket.lane_id else None
        board = db.query(KanbanBoard).filter(KanbanBoard.id == lane.board_id).first() if lane else None
        board_id = lane.board_id if lane else None
        project_id = ticket.linked_project_id or (board.default_project_id if board else None)
        if project_id:
            project = db.query(Project).filter(Project.id == int(project_id)).first()
            if project:
                project_name = (project.name or "").strip()
                project_folder = (project.folder_location or "").strip()

        if existing_workflow_id and not force:
            wf = get_workflow(int(existing_workflow_id))
            from distr.core.workflow.selection import select_workflow_for_request

            linked_selection = select_workflow_for_request(
                "\n".join(part for part in (ticket_title, ticket_description, project_name) if part),
                candidates=[wf] if wf else [],
            )
            if wf and linked_selection.get("selected"):
                out: dict[str, Any] = {
                    "success": True,
                    "workflow_id": int(existing_workflow_id),
                    "reused": True,
                    "step_count": len(wf.get("steps") or []),
                    "ticket_id": int(ticket_id),
                    "selection_reason": linked_selection.get("reason"),
                }
                if start_run:
                    run_result = _start_ticket_run(
                        int(existing_workflow_id),
                        ticket_id=int(ticket_id),
                        board_id=board_id,
                        skip_human_checkpoints=skip_human_checkpoints,
                        run_metadata=run_metadata,
                        dispatch_async=dispatch_async,
                    )
                    if "error" in run_result:
                        out["error"] = run_result["error"]
                        out["success"] = False
                    else:
                        out["run_id"] = run_result.get("run_id")
                        out["chat_id"] = run_result.get("chat_id")
                        out["development_url"] = run_result.get("development_url")
                return out

    # Existing-first selection is mandatory. A missing ticket link is not a
    # reason to invent a new workflow; select the strongest complete contract.
    if not steps:
        from distr.core.workflow.selection import select_workflow_for_request

        selection = select_workflow_for_request(
            "\n".join(part for part in (ticket_title, ticket_description, project_name) if part)
        )
        selected = selection.get("selected") or {}
        workflow_id = int(selected.get("workflow_id") or 0)
        if not workflow_id and selection.get("request_profile", {}).get("software"):
            from distr.core.workflow.phases import route_ticket_workflow_id

            workflow_id = route_ticket_workflow_id(
                "\n".join(part for part in (ticket_title, ticket_description, project_name) if part),
                has_repo_target=True,
            )
        if not workflow_id:
            return {
                "success": False,
                "error": (
                    "No existing workflow safely covers this ticket. Workflow creation is a last resort; "
                    "generate and audit a complete specialized workflow before starting the run."
                ),
                "selection": selection,
            }
        with get_session() as db:
            ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
            if ticket:
                ticket.linked_workflow_id = workflow_id
                db.commit()
            if link_board_default and board_id:
                board = db.query(KanbanBoard).filter(KanbanBoard.id == int(board_id)).first()
                if board:
                    board.default_workflow_id = workflow_id
                    db.commit()
        wf = get_workflow(workflow_id) or {}
        result = {
            "success": True,
            "workflow_id": workflow_id,
            "ticket_id": int(ticket_id),
            "step_count": len(wf.get("steps") or []),
            "reused": True,
            "selection_reason": selection.get("reason"),
        }
        if start_run:
            run_result = _start_ticket_run(
                workflow_id,
                ticket_id=int(ticket_id),
                board_id=board_id,
                skip_human_checkpoints=skip_human_checkpoints,
                run_metadata=run_metadata,
                dispatch_async=dispatch_async,
            )
            if "error" in run_result:
                result["success"] = False
                result["error"] = run_result["error"]
            else:
                result["run_id"] = run_result.get("run_id")
                result["chat_id"] = run_result.get("chat_id")
                result["development_url"] = run_result.get("development_url")
        return result

    name = (workflow_name or "").strip() or _ticket_workflow_name(ticket_title)

    workflow_id: int
    step_count = 0

    payload: dict[str, Any] = {
        "name": name,
        "description": f"Spawned for ticket #{ticket_id}",
        "workflow_type": "manual",
        "steps": steps,
    }
    merged_input = dict(workflow_input or {})
    merged_input["spawned_for_ticket_id"] = int(ticket_id)
    if skip_human_checkpoints:
        merged_input["skip_human_checkpoints"] = True
    payload["workflow_input"] = json.dumps(merged_input)
    workflow_id = int(import_workflow(payload))
    wf = get_workflow(workflow_id) or {}
    step_count = len(wf.get("steps") or [])

    with get_session() as db:
        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
        if ticket:
            ticket.linked_workflow_id = workflow_id
            db.commit()
        if link_board_default and board_id:
            board = db.query(KanbanBoard).filter(KanbanBoard.id == int(board_id)).first()
            if board and not board.default_workflow_id:
                board.default_workflow_id = workflow_id
                db.commit()

    result: dict[str, Any] = {
        "success": True,
        "workflow_id": workflow_id,
        "ticket_id": int(ticket_id),
        "step_count": step_count,
        "reused": False,
    }

    if start_run:
        run_result = _start_ticket_run(
            workflow_id,
            ticket_id=int(ticket_id),
            board_id=board_id,
            skip_human_checkpoints=skip_human_checkpoints,
            run_metadata=run_metadata,
            dispatch_async=dispatch_async,
        )
        if "error" in run_result:
            result["success"] = False
            result["error"] = run_result["error"]
        else:
            result["run_id"] = run_result.get("run_id")
            result["chat_id"] = run_result.get("chat_id")
            result["development_url"] = run_result.get("development_url")

    return result


def _start_ticket_run(
    workflow_id: int,
    *,
    ticket_id: int,
    board_id: int | None,
    skip_human_checkpoints: bool,
    run_metadata: dict[str, Any] | None,
    dispatch_async: bool,
) -> dict[str, Any]:
    from distr.core.workflow.work_dispatch import dispatch_work_item

    context = ""
    meta = dict(run_metadata or {})
    meta.setdefault("source_type", "spawn_workflow_for_ticket")
    meta.setdefault("ticket_id", ticket_id)
    if skip_human_checkpoints:
        meta["skip_human_checkpoints"] = True
    try:
        from distr.core.kanban.ticket_workflow_brief import (
            build_ticket_workflow_brief,
            render_ticket_workflow_brief,
        )

        with get_session() as db:
            brief = build_ticket_workflow_brief(db, ticket_id, board_id=board_id)
            context = render_ticket_workflow_brief(brief)
            meta["ticket_workflow_brief"] = brief
    except Exception:
        logger.debug("spawn_workflow: ticket brief failed", exc_info=True)

    with get_session() as db:
        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
        title = ticket.title if ticket else f"Ticket #{ticket_id}"
        project_id = ticket.linked_project_id if ticket else None
    result = dispatch_work_item(
        workflow_id=int(workflow_id),
        context=context,
        board_id=board_id,
        ticket_id=int(ticket_id),
        title=title or f"Ticket #{ticket_id}",
        project_id=int(project_id) if project_id else None,
        source_type="spawn_workflow_for_ticket",
        source_ref=f"ticket:{int(ticket_id)}",
        run_metadata=meta,
        dispatch_async=dispatch_async,
        _session_provider=get_session,
    )
    return result
