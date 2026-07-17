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

_DEFAULT_PRESET = "development-ticket-to-implementation"


def infer_preset_slug_for_ticket(
    *,
    title: str = "",
    description: str = "",
    project_folder: str = "",
) -> str:
    """Pick a loop preset from ticket + project context. ponytail: keyword heuristic; upgrade to archetype classifier."""
    # Development is deliberately the one canonical software-work workflow.
    # Its internal steps branch between planning, implementation, review,
    # correction and reporting; ticket keywords must not create more tabs.
    return _DEFAULT_PRESET


def _ticket_workflow_name(ticket_title: str, *, stamp: str = "") -> str:
    base = re.sub(r"\s+", " ", (ticket_title or "Ticket").strip())[:72]
    suffix = f" ({stamp})" if stamp else ""
    return f"{base} loop{suffix}".strip()


def spawn_workflow_for_ticket(
    ticket_id: int,
    *,
    preset_slug: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    workflow_name: str | None = None,
    workflow_input: dict[str, Any] | None = None,
    link_board_default: bool = False,
    start_run: bool = True,
    skip_human_checkpoints: bool = True,
    force: bool = False,
    run_metadata: dict[str, Any] | None = None,
    dispatch_async: bool = True,
) -> dict[str, Any]:
    """Create workflow + steps for a ticket, link it, optionally start the run.

    Returns workflow_id, run_id (when started), preset_slug, step_count.
    Does not require a pre-existing workflow on the board.
    """
    from distr.core.workflow.import_export import import_workflow
    from distr.core.workflow.loop_preset_loader import load_bundle_by_slug
    from distr.core.workflow.loop_presets import apply_loop_preset
    from distr.core.workflow.service import create_workflow, get_workflow

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
            from distr.core.workflow.developer_workflow import get_or_create_development_workflow

            workflow_id = get_or_create_development_workflow()
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
            "preset_slug": _DEFAULT_PRESET,
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
        return result

    slug = (preset_slug or "").strip() or infer_preset_slug_for_ticket(
        title=ticket_title,
        description="",
        project_folder=project_folder,
    )
    name = (workflow_name or "").strip() or _ticket_workflow_name(ticket_title)

    workflow_id: int
    step_count = 0
    applied_slug = slug

    if steps:
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
    else:
        workflow_id = int(create_workflow(name=name, description=f"Spawned for ticket #{ticket_id}"))
        bundle = load_bundle_by_slug(slug)
        if not bundle:
            return {"success": False, "error": f"Unknown preset slug: {slug}"}
        preset_name = str(bundle.get("name") or slug)
        applied_slug = str(bundle.get("slug") or slug)
        applied = apply_loop_preset(workflow_id, preset_name, mode="replace")
        if not applied.get("success"):
            return {"success": False, "error": applied.get("error") or "Failed to apply preset"}
        step_count = int(applied.get("step_count") or 0)
        if workflow_input or skip_human_checkpoints:
            with get_session() as db:
                from distr.core.db.workflow import AutoWorkflow

                wf_row = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
                if wf_row:
                    try:
                        merged = json.loads(wf_row.workflow_input or "{}") or {}
                    except Exception:
                        merged = {}
                    merged.update(workflow_input or {})
                    merged["spawned_for_ticket_id"] = int(ticket_id)
                    merged["preset_slug"] = applied_slug
                    if skip_human_checkpoints:
                        merged["skip_human_checkpoints"] = True
                    wf_row.workflow_input = json.dumps(merged)
                    db.commit()

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
        "preset_slug": applied_slug,
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
    from distr.core.workflow.service import start_workflow_run

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

    return start_workflow_run(
        workflow_id,
        context=context,
        board_id=board_id,
        ticket_id=ticket_id,
        run_metadata=meta,
        dispatch_async=dispatch_async,
    )
