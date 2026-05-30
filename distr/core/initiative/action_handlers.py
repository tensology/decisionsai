"""Shared Initiative action execution.

Used both by the live Initiative service and by approved draft payloads so an
approval from the settings UI runs the same code path as an auto-executed action.
"""

from __future__ import annotations

import asyncio
from typing import Any


def execute_initiative_action(
    *,
    action_type: str,
    description: str,
    payload: dict[str, Any] | None,
    draft: str = "",
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    settings = settings or {}
    if action_type == "ticket_lane_move":
        return move_tickets(payload)
    if action_type == "workflow_start":
        _require(settings, "initiative_allow_routine_tasks", "routine task execution is disabled")
        _require(settings, "initiative_allow_workflow_start", "workflow starts are disabled")
        return start_ticket_workflows(payload)
    if action_type == "project_cli_task":
        _require(settings, "initiative_allow_routine_tasks", "routine task execution is disabled")
        _require(settings, "initiative_allow_project_cli", "project CLI execution is disabled")
        return run_project_cli_tasks(payload)
    return {
        "success": True,
        "message": description or f"{action_type} acknowledged",
        "details": {"action_type": action_type, "draft": draft},
    }


def move_tickets(payload: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import func

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanLane, KanbanTicket
    from distr.core.kanban.ticket_audit import append_ticket_audit_entry

    board_id = int(payload.get("board_id") or 0)
    target_lane_name = (payload.get("target_lane") or payload.get("lane") or "Current").strip()
    ticket_ids = _ticket_ids(payload)
    if not board_id or not target_lane_name or not ticket_ids:
        raise ValueError("ticket lane move requires board_id, target_lane, and ticket_ids")

    moved: list[int] = []
    with get_session() as session:
        target_lane = (
            session.query(KanbanLane)
            .filter(KanbanLane.board_id == board_id)
            .filter(func.lower(KanbanLane.name) == target_lane_name.lower())
            .first()
        )
        if not target_lane:
            raise ValueError(f"target lane not found: {target_lane_name}")
        max_pos = (
            session.query(func.max(KanbanTicket.position))
            .filter(KanbanTicket.lane_id == target_lane.id)
            .scalar()
        )
        pos = int(max_pos if max_pos is not None else -1)
        for ticket_id in ticket_ids:
            ticket = session.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
            if not ticket:
                continue
            pos += 1
            ticket.lane_id = target_lane.id
            ticket.position = pos
            moved.append(ticket.id)
            append_ticket_audit_entry(
                session,
                ticket_id=ticket.id,
                run_id=None,
                step_id=None,
                step_result_id=None,
                execution_lane="initiative",
                status="completed",
                final_verdict="moved",
                summary=f"Initiative moved ticket to {target_lane.name}",
                details=f"Payload: {payload}",
            )
        session.commit()
    return {"success": True, "message": f"Moved {len(moved)} ticket(s) to {target_lane_name}", "ticket_ids": moved}


def start_ticket_workflows(payload: dict[str, Any]) -> dict[str, Any]:
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket
    from distr.core.workflow.service import start_workflow_run

    ticket_ids = _ticket_ids(payload)
    workflow_id = payload.get("workflow_id")
    started: list[dict[str, Any]] = []
    with get_session() as session:
        if not ticket_ids and workflow_id:
            result = start_workflow_run(int(workflow_id), context="Started by Initiative")
            return {"success": "error" not in result, "message": "Started workflow", "result": result}

        for ticket_id in ticket_ids:
            ticket = session.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
            if not ticket:
                continue
            board = ticket.lane.board if ticket.lane else None
            wid = int(ticket.linked_workflow_id or (board.default_workflow_id if board else 0) or 0)
            if not wid:
                continue
            context = _ticket_workflow_context(session, ticket_id, board)
            result = start_workflow_run(
                wid,
                context=context,
                board_id=board.id if board else None,
                ticket_id=ticket_id,
                run_metadata={
                    "source_type": "initiative",
                    "board_id": board.id if board else None,
                    "ticket_id": ticket_id,
                    "ticket_title": ticket.title or "",
                    "phase": "planning",
                },
            )
            started.append({"ticket_id": ticket_id, "workflow_id": wid, "result": result})
    return {"success": bool(started), "message": f"Started {len(started)} workflow run(s)", "started": started}


def run_project_cli_tasks(payload: dict[str, Any]) -> dict[str, Any]:
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.projects import Project
    from distr.core.kanban.ticket_audit import append_ticket_audit_entry
    from distr.core.kanban.ticket_cli_context import build_kanban_ticket_cli_instruction
    from distr.core.kanban.ticket_policy import resolve_ticket_cli_route
    from distr.core.project_cli_backends import run_project_task

    ticket_ids = _ticket_ids(payload)
    if not ticket_ids:
        raise ValueError("project CLI task requires ticket_ids")

    results: list[dict[str, Any]] = []
    with get_session() as session:
        for ticket_id in ticket_ids:
            ticket = session.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
            if not ticket:
                continue
            board = ticket.lane.board if ticket.lane else None
            project_id = int(ticket.linked_project_id or payload.get("project_id") or (board.default_project_id if board else 0) or 0)
            project = session.query(Project).filter(Project.id == project_id).first() if project_id else None
            if not project:
                results.append({"ticket_id": ticket_id, "success": False, "error": "No linked project"})
                continue
            instruction = build_kanban_ticket_cli_instruction(
                session,
                ticket_id,
                project_name=project.name or "",
                project_folder=project.folder_location or "",
                project_id=project.id,
            )
            from distr.core.hermes_orchestrator import resolve_execution_route

            decision = resolve_execution_route(
                project=project,
                ticket=ticket,
                board=board,
                emit_event=True,
            )
            route = decision.to_route_dict()
            result = _run_async(run_project_task(
                project,
                instruction,
                audit_id=None,
                origin="initiative",
                ticket_id=ticket_id,
                ticket_complexity=route["complexity"],
                backend_id_override=route["backend"],
                model_override=route["model"],
                codex_reasoning_effort_override=route.get("codex_reasoning_effort"),
                codex_service_tier_override=route.get("codex_service_tier"),
            ))
            result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
            append_ticket_audit_entry(
                session,
                ticket_id=ticket_id,
                run_id=None,
                step_id=None,
                step_result_id=None,
                execution_lane="cli",
                status="completed" if result_dict.get("success") else "failed",
                final_verdict="completed" if result_dict.get("success") else "failed",
                summary=f"Initiative sent ticket to {result_dict.get('engine') or result_dict.get('backend_id') or 'project CLI'}",
                details=(result_dict.get("output") or result_dict.get("error") or "")[:8000],
            )
            results.append({"ticket_id": ticket_id, **result_dict})
        session.commit()
    return {"success": any(r.get("success") for r in results), "message": f"Ran {len(results)} CLI task(s)", "results": results}


def _ticket_workflow_context(session: Any, ticket_id: int, board: Any) -> str:
    try:
        from distr.core.kanban.ticket_workflow_brief import (
            build_ticket_workflow_brief,
            render_ticket_workflow_brief,
        )

        project_id = None
        project_name = None
        project_folder = None
        ticket = session.query(__import__("distr.core.db.kanban", fromlist=["KanbanTicket"]).KanbanTicket).filter_by(id=ticket_id).first()
        if ticket:
            project_id = ticket.linked_project_id or (board.default_project_id if board else None)
        if project_id:
            from distr.core.db.projects import Project

            project = session.query(Project).filter(Project.id == project_id).first()
            if project:
                project_name = project.name
                project_folder = project.folder_location
        brief = build_ticket_workflow_brief(
            session,
            ticket_id,
            board_id=board.id if board else None,
            board_name=board.name if board else "",
            project_id=project_id,
            project_name=project_name,
            project_folder=project_folder,
        )
        return render_ticket_workflow_brief(brief)
    except Exception:
        ticket = session.query(__import__("distr.core.db.kanban", fromlist=["KanbanTicket"]).KanbanTicket).filter_by(id=ticket_id).first()
        if not ticket:
            return f"Ticket #{ticket_id}"
        description = (ticket.description or "").strip()
        return f"Ticket: {ticket.title}\n\nDescription: {description}" if description else f"Ticket: {ticket.title}"


def _ticket_ids(payload: dict[str, Any]) -> list[int]:
    raw = payload.get("ticket_ids")
    if raw is None and payload.get("ticket_id") is not None:
        raw = [payload.get("ticket_id")]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[int] = []
    for value in raw:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _require(settings: dict[str, Any], key: str, message: str) -> None:
    if not bool(settings.get(key, False)):
        raise PermissionError(message)


def _run_async(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("Cannot run project CLI task from inside an active event loop")
