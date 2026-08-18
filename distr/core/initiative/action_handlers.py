"""Shared Initiative action execution.

Used both by the live Initiative service and by approved draft payloads so an
approval from the settings UI runs the same code path as an auto-executed action.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _moved_tickets_message(count: int, target_lane_name: str) -> str:
    noun = "ticket" if int(count or 0) == 1 else "tickets"
    return f"Moved {int(count or 0)} {noun} to {target_lane_name}"


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
    if action_type == "jira_intake":
        return run_jira_intake_from_initiative(payload, settings=settings)
    return {
        "success": True,
        "message": description or f"{action_type} acknowledged",
        "details": {"action_type": action_type, "draft": draft},
    }


def run_jira_intake_from_initiative(
    payload: dict[str, Any],
    *,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage collated Jira notification keys onto the in-use board and Telegram-digest them."""
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.kanban.jira_intake import run_jira_morning_intake
    from distr.core.kanban.ticket_audit import append_ticket_audit_entry

    settings = settings or {}
    keys = payload.get("issue_keys") or []
    if not isinstance(keys, list):
        keys = []
    keys = [str(k).strip().upper() for k in keys if str(k).strip()]
    board_id = int(payload.get("board_id") or 0)
    if not board_id:
        with get_session() as session:
            board = (
                session.query(KanbanBoard)
                .filter(KanbanBoard.in_use.is_(True), KanbanBoard.archived.is_(False))
                .order_by(KanbanBoard.id.desc())
                .first()
            )
            if not board:
                board = (
                    session.query(KanbanBoard)
                    .filter(KanbanBoard.archived.is_(False), KanbanBoard.source == "database")
                    .order_by(KanbanBoard.id.desc())
                    .first()
                )
            board_id = int(board.id) if board else 0
    if not board_id:
        raise ValueError("No local Ticket Board is available for Jira intake")
    if not keys:
        return {"success": True, "message": "No new Jira keys to stage", "created": []}

    result = run_jira_morning_intake(
        board_id=board_id,
        keys=keys,
        notify=True,
    )
    created = result.get("created") or []
    if created:
        with get_session() as session:
            for row in created:
                append_ticket_audit_entry(
                    session,
                    ticket_id=int(row["id"]),
                    run_id=None,
                    step_id=None,
                    step_result_id=None,
                    execution_lane="initiative",
                    status="completed",
                    final_verdict="jira_intake_staged",
                    summary=f"Staged from Jira intake {row.get('external_id') or row.get('key')}",
                    details=f"board_id={board_id}; keys={keys}",
                )
            session.commit()
    count = len(created)
    message = (
        f"Staged {count} Jira ticket(s) and sent a Telegram digest."
        if count
        else f"Jira intake found nothing new to stage ({result.get('reason')})."
    )
    return {
        "success": True,
        "message": message,
        "board_id": board_id,
        "created": created,
        "result": result,
    }


def move_tickets(payload: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import func

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanLane, KanbanTicket
    from distr.core.kanban.lifecycle import require_automation_lane
    from distr.core.kanban.ticket_audit import append_ticket_audit_entry

    board_id = int(payload.get("board_id") or 0)
    target_lane_name = require_automation_lane(
        payload.get("target_lane") or payload.get("lane") or "Current"
    )
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
    if moved:
        try:
            from distr.gui.web.kanban_events import increment_kanban_updated

            increment_kanban_updated(
                board_id,
                event_type="ticket_lane_move",
                payload={
                    "board_id": board_id,
                    "ticket_ids": moved,
                    "target_lane": target_lane_name,
                },
            )
        except Exception:
            pass
    return {"success": True, "message": _moved_tickets_message(len(moved), target_lane_name), "ticket_ids": moved}


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
    from distr.core.kanban.lifecycle import move_ticket_to_delivery_lane
    from distr.core.kanban.ticket_audit import append_ticket_audit_entry
    from distr.core.kanban.ticket_cli_context import build_kanban_ticket_cli_instruction
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
            # Direct CLI work follows the same human-visible lifecycle as a
            # workflow run. Commit before invoking the worker so the web UI
            # can show that execution actually started while a slow model is
            # still loading or working. A failed worker deliberately remains
            # In Progress; only successful, evidenced work advances to QA.
            moved_to_progress = move_ticket_to_delivery_lane(
                session,
                ticket_id,
                "In Progress",
            )
            if moved_to_progress:
                append_ticket_audit_entry(
                    session,
                    ticket_id=ticket_id,
                    run_id=None,
                    step_id=None,
                    step_result_id=None,
                    execution_lane="cli",
                    status="running",
                    final_verdict="started",
                    summary="Project CLI execution started; ticket moved to In Progress",
                    details="The ticket remains visible in In Progress until the worker returns.",
                )
                session.commit()
                _publish_ticket_lane_change(board, ticket_id, "In Progress")
            instruction = build_kanban_ticket_cli_instruction(
                session,
                ticket_id,
                project_name=project.name or "",
                project_folder=project.folder_location or "",
                project_id=project.id,
            )
            from distr.core.orchestrator_routing import resolve_execution_route
            from distr.core.project_cli_backends.policy_manager import _counts_as_model_health_failure

            required_capabilities = ["code", "files"]
            attempts: list[dict[str, Any]] = []
            result_dict: dict[str, Any] = {}
            # Auto gets one bounded free/local recovery after a route or
            # completion-contract failure. The failed execution certification
            # is persisted synchronously, so resolving again selects a different
            # model without hard-coding provider order here.
            for attempt_number in range(1, 3):
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
                    adapter_options={
                        "model_provider": route.get("model_provider") or "",
                        "required_capabilities": required_capabilities,
                        "task_intent": (route.get("task_profile") or {}).get("intent") or "implementation",
                        "skills": list(route.get("skills") or []),
                        "mutation_expected": True,
                    },
                ))
                result_dict = _validated_direct_result(result)
                attempts.append({
                    "attempt": attempt_number,
                    "backend": route.get("backend"),
                    "provider": route.get("model_provider"),
                    "model": route.get("model"),
                    "source": route.get("source"),
                    "success": bool(result_dict.get("success")),
                    "error": str(result_dict.get("error") or "")[:1000],
                    "execution_session_id": result_dict.get("execution_session_id"),
                })
                if result_dict.get("success") or route.get("requires_approval"):
                    break
                failure = str(result_dict.get("error") or result_dict.get("output") or "")
                completion_failure = any(marker in failure.lower() for marker in (
                    "claimed success without",
                    "no assistant text",
                    "completion report",
                ))
                if not (_counts_as_model_health_failure(failure) or completion_failure):
                    break
            result_dict["attempts"] = attempts
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
            if result_dict.get("success"):
                try:
                    moved_to_qa = move_ticket_to_delivery_lane(session, ticket_id, "QA")
                except ValueError:
                    # A human may have moved the ticket while the worker was
                    # running. Preserve that decision and retain worker success.
                    moved_to_qa = False
                if moved_to_qa:
                    append_ticket_audit_entry(
                        session,
                        ticket_id=ticket_id,
                        run_id=None,
                        step_id=None,
                        step_result_id=None,
                        execution_lane="cli",
                        status="waiting",
                        final_verdict="awaiting_human_acceptance",
                        summary="Project CLI execution passed; ticket moved to QA",
                        details="A human must verify the result and move the ticket to Complete.",
                    )
                    session.commit()
                    _publish_ticket_lane_change(board, ticket_id, "QA")
            results.append({
                "ticket_id": ticket_id,
                "title": str(ticket.title or "").strip(),
                "lane": str(getattr(getattr(ticket, "lane", None), "name", "") or ""),
                **result_dict,
            })
        session.commit()
    return {
        "success": any(r.get("success") for r in results),
        "message": _direct_execution_message(results),
        "results": results,
    }


def _direct_execution_message(results: list[dict[str, Any]]) -> str:
    """Return a user-facing outcome suitable for chat, Telegram, and TTS."""
    if not results:
        return "I could not find a linked project ticket to work on."
    if len(results) > 1:
        completed = sum(1 for item in results if item.get("success"))
        if completed == len(results):
            return f"Completed all {completed} requested changes. They are waiting in QA for your review."
        return (
            f"Completed {completed} of {len(results)} requested changes. "
            "The unfinished work remains In Progress with its blocker attached."
        )
    item = results[0]
    title = str(item.get("title") or "the requested change").strip()
    if item.get("success"):
        return f"Completed “{title}”. It is waiting in QA for your review."
    blocker = str(item.get("error") or item.get("output") or "The worker did not return usable evidence.").strip()
    blocker = blocker.splitlines()[0][:240]
    return f"I could not complete “{title}”. It remains In Progress. {blocker}"


def _validated_direct_result(result: Any) -> dict[str, Any]:
    """Compatibility guard for adapters/tests not yet enforcing the registry contract."""
    result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    if not result_dict.get("success"):
        return result_dict
    workspace_delta = (
        result_dict.get("workspace_state_delta")
        if isinstance(result_dict.get("workspace_state_delta"), dict)
        else {}
    )
    memory_delta = (
        result_dict.get("memory_delta")
        if isinstance(result_dict.get("memory_delta"), dict)
        else {}
    )
    report_text = str(
        result_dict.get("output") or result_dict.get("summary") or ""
    ).strip()
    changed_files = list(memory_delta.get("changed_files") or [])
    artifacts = list(result_dict.get("artifacts") or [])
    mutation_observed = bool(
        workspace_delta.get("changed") or changed_files or artifacts
    )
    if report_text and mutation_observed:
        return result_dict
    missing = []
    if not report_text:
        missing.append("a non-empty completion report")
    if not mutation_observed:
        missing.append("a project workspace change or artifact")
    result_dict["success"] = False
    result_dict["error"] = "Worker claimed success without " + " and ".join(missing) + "."
    try:
        from distr.core.kanban.project_execution import complete_execution_session

        complete_execution_session(
            result_dict.get("execution_session_id"),
            success=False,
            output_packet=result_dict,
            error=result_dict["error"],
        )
    except Exception:
        pass
    return result_dict


def _publish_ticket_lane_change(board: Any, ticket_id: int, lane_name: str) -> None:
    """Best-effort refresh signal for lifecycle changes made outside workflows."""
    board_id = int(getattr(board, "id", 0) or 0)
    if not board_id:
        return
    try:
        from distr.gui.web.kanban_events import increment_kanban_updated

        increment_kanban_updated(
            board_id,
            event_type="ticket_lane_move",
            payload={
                "board_id": board_id,
                "ticket_ids": [int(ticket_id)],
                "target_lane": lane_name,
                "source": "project_cli",
            },
        )
    except Exception:
        pass


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
