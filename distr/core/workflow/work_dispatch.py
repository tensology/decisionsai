"""Canonical thread-first dispatch for executable Development work."""

from __future__ import annotations

import logging
from typing import Any

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import AutoWorkflow

logger = logging.getLogger(__name__)


class DevelopmentThreadPreparationError(RuntimeError):
    """Execution was stopped because its durable Development thread failed."""


def _board_scope(board: KanbanBoard | None) -> tuple[str | None, str | None]:
    if board is None:
        return None, None
    source = str(board.source or "database").strip().lower()
    if source in {"jira", "trello"} and str(board.external_board_id or "").strip():
        return source, f"{source}:{str(board.external_board_id).strip()}"
    return "local", f"decisions:{int(board.id)}"


def _resolve_scope(
    *,
    workflow_id: int,
    ticket_id: int | None,
    board_id: int | None,
    title: str | None,
    project_id: int | None,
    board_provider: str | None,
    board_key: str | None,
    board_ticket_key: str | None,
    board_ticket_title: str | None,
    board_ticket_lane: str | None,
    session_provider=None,
) -> dict[str, Any]:
    scope = {
        "title": str(title or "").strip(),
        "project_id": int(project_id) if project_id is not None else None,
        "board_id": int(board_id) if board_id is not None else None,
        "board_provider": str(board_provider or "").strip() or None,
        "board_key": str(board_key or "").strip() or None,
        "board_ticket_key": str(board_ticket_key or "").strip() or None,
        "board_ticket_title": str(board_ticket_title or "").strip() or None,
        "board_ticket_lane": str(board_ticket_lane or "").strip() or None,
    }
    with (session_provider or get_session)() as db:
        workflow = db.get(AutoWorkflow, int(workflow_id))
        if workflow is None:
            raise ValueError(f"Workflow #{workflow_id} was not found.")
        if not scope["title"] and workflow is not None:
            scope["title"] = str(workflow.name or "").strip()
        if ticket_id is not None:
            ticket = db.get(KanbanTicket, int(ticket_id))
            if ticket is None:
                raise ValueError(f"Ticket #{ticket_id} was not found.")
            lane = db.get(KanbanLane, int(ticket.lane_id)) if ticket.lane_id else None
            board = db.get(KanbanBoard, int(lane.board_id)) if lane is not None else None
            provider, resolved_key = _board_scope(board)
            scope["board_id"] = int(board.id) if board is not None else scope["board_id"]
            scope["board_provider"] = scope["board_provider"] or provider
            scope["board_key"] = scope["board_key"] or resolved_key
            scope["board_ticket_key"] = scope["board_ticket_key"] or (
                str(ticket.external_id or "").strip() or str(int(ticket.id))
            )
            scope["board_ticket_title"] = scope["board_ticket_title"] or str(ticket.title or "").strip()
            scope["board_ticket_lane"] = scope["board_ticket_lane"] or (
                str(lane.name or "").strip() if lane is not None else None
            )
            scope["project_id"] = scope["project_id"] or (
                int(ticket.linked_project_id)
                if ticket.linked_project_id is not None
                else int(board.default_project_id)
                if board is not None and board.default_project_id is not None
                else None
            )
            scope["title"] = scope["title"] or str(ticket.title or "").strip()
    scope["title"] = scope["title"] or "Development work"
    return scope


def dispatch_work_item(
    *,
    workflow_id: int,
    chat_id: int | None = None,
    context: str = "",
    title: str | None = None,
    project_id: int | None = None,
    board_id: int | None = None,
    ticket_id: int | None = None,
    source_type: str = "web",
    source_ref: str | None = None,
    board_provider: str | None = None,
    board_key: str | None = None,
    board_ticket_key: str | None = None,
    board_ticket_title: str | None = None,
    board_ticket_lane: str | None = None,
    run_metadata: dict[str, Any] | None = None,
    start_step_id: int | None = None,
    event_queue: Any = None,
    dispatch_async: bool = False,
    _session_provider=None,
) -> dict[str, Any]:
    """Prepare the durable thread, then and only then start its workflow run."""
    from distr.core.workflow.development_threads import (
        development_thread_metadata,
        development_identity_key,
        ensure_development_thread,
        mark_development_thread,
    )

    scope = _resolve_scope(
        workflow_id=int(workflow_id),
        ticket_id=int(ticket_id) if ticket_id is not None else None,
        board_id=board_id,
        title=title,
        project_id=project_id,
        board_provider=board_provider,
        board_key=board_key,
        board_ticket_key=board_ticket_key,
        board_ticket_title=board_ticket_title,
        board_ticket_lane=board_ticket_lane,
        session_provider=_session_provider,
    )
    # Ticket-triggered paths must reuse the ticket's durable Development
    # thread.  Older callers only supplied ticket_id, which allowed each
    # retry/spawn path to create a fresh ordinary Chat containing the brief.
    # Resolve the existing source link centrally before the identity gateway
    # runs, so every entry point has the same idempotent behavior.
    if chat_id is None and ticket_id is not None:
        with (_session_provider or get_session)() as db:
            ticket = db.get(KanbanTicket, int(ticket_id))
            if ticket is not None and ticket.source_chat_id is not None:
                chat_id = int(ticket.source_chat_id)
    try:
        if chat_id is not None:
            from distr.core.db import Chat

            with (_session_provider or get_session)() as db:
                chat = db.get(Chat, int(chat_id))
                if chat is None or chat.parent_id is not None:
                    raise ValueError(f"Development thread #{chat_id} was not found.")
                existing = development_thread_metadata(chat)
            mark_development_thread(
                int(chat_id),
                workflow_id=int(workflow_id),
                ticket_id=int(ticket_id) if ticket_id is not None else existing.get("ticket_id"),
                source_type=existing.get("source_type") or "prompt",
                source_ref=existing.get("source_ref"),
                board_key=scope["board_key"] or existing.get("board_key"),
                board_provider=scope["board_provider"] or existing.get("board_provider"),
                board_ticket_key=scope["board_ticket_key"] or existing.get("board_ticket_key"),
                board_ticket_title=scope["board_ticket_title"] or existing.get("board_ticket_title"),
                board_ticket_lane=scope["board_ticket_lane"] or existing.get("board_ticket_lane"),
                _session_provider=_session_provider,
            )
        else:
            chat_id = ensure_development_thread(
                workflow_id=int(workflow_id),
                title=scope["title"],
                project_id=scope["project_id"],
                ticket_id=int(ticket_id) if ticket_id is not None else None,
                starting_question=str(context or "").strip() or None,
                source_type=source_type,
                source_ref=source_ref,
                board_key=scope["board_key"],
                board_provider=scope["board_provider"],
                board_ticket_key=scope["board_ticket_key"],
                board_ticket_title=scope["board_ticket_title"],
                board_ticket_lane=scope["board_ticket_lane"],
                _session_provider=_session_provider,
            )
    except Exception as exc:
        raise DevelopmentThreadPreparationError(
            f"The Development thread could not be prepared before execution: {exc}"
        ) from exc
    identity_key = development_identity_key(
        chat_id=int(chat_id),
        source_type=source_type,
        source_ref=source_ref,
        ticket_id=int(ticket_id) if ticket_id is not None else None,
        board_provider=scope["board_provider"],
        board_key=scope["board_key"],
        board_ticket_key=scope["board_ticket_key"],
    )

    if ticket_id is not None:
        with (_session_provider or get_session)() as db:
            ticket = db.get(KanbanTicket, int(ticket_id))
            if ticket is None:
                raise ValueError(f"Ticket #{ticket_id} disappeared before execution.")
            ticket.source_chat_id = int(chat_id)
            ticket.linked_workflow_id = int(workflow_id)
            if scope["project_id"] is not None:
                ticket.linked_project_id = int(scope["project_id"])
            db.commit()

    metadata = dict(run_metadata or {})
    routing_assessment = (
        dict(metadata.get("routing_assessment") or {})
        if isinstance(metadata.get("routing_assessment"), dict)
        else {}
    )
    assessed_route = (
        dict(routing_assessment.get("route") or {})
        if isinstance(routing_assessment.get("route"), dict)
        else {}
    )
    if routing_assessment.get("complexity"):
        assessed_route.setdefault("complexity", str(routing_assessment["complexity"]))
    if assessed_route:
        current_route = (
            dict(metadata.get("execution_route") or {})
            if isinstance(metadata.get("execution_route"), dict)
            else {}
        )
        metadata["execution_route"] = {**assessed_route, **current_route}
    metadata.update({
        "chat_id": int(chat_id),
        "studio": True,
        "work_item_identity": identity_key,
        "source_type": str(source_type or metadata.get("source_type") or "web"),
        "source_ref": source_ref,
        "project_id": scope["project_id"],
        "board_key": scope["board_key"],
        "board_provider": scope["board_provider"],
        "board_ticket_key": scope["board_ticket_key"],
        "orchestration": {
            "owner_type": "development_thread",
            "owner_chat_id": int(chat_id),
            "agent_role": "workflow_orchestrator",
            "execution_kind": "deterministic_orchestrator_subagent",
            "workflow_id": int(workflow_id),
            "specialist_workers_are_children": True,
            "report_to_parent_thread": True,
        },
    })
    metadata = {key: value for key, value in metadata.items() if value not in (None, "")}

    from distr.core.workflow.service import start_workflow_run

    result = start_workflow_run(
        int(workflow_id),
        context=str(context or "").strip() or None,
        start_step_id=start_step_id,
        board_id=scope["board_id"],
        ticket_id=int(ticket_id) if ticket_id is not None else None,
        run_metadata=metadata,
        event_queue=event_queue,
        dispatch_async=dispatch_async,
    )
    if not isinstance(result, dict):
        result = {"status": "running"}
    from distr.core.workflow.development_control import resume_thread_time

    try:
        resume_thread_time(int(chat_id), _session_provider=_session_provider)
    except LookupError:
        # Time tracking is subordinate to execution. Legacy/custom thread
        # providers may return a valid chat before creating a work-item row.
        logger.warning(
            "Development run started without a timer row for chat_id=%s",
            chat_id,
        )
    result["chat_id"] = int(chat_id)
    result["development_url"] = f"/development/threads/{int(chat_id)}/"
    result["work_item_identity"] = identity_key
    return result
