"""Durable project execution session helpers.

These rows are the Decisions-owned control loop around Codex/Cursor/Pi/CLI work:
what was sent, where it went, what came back, and what the orchestrator can
validate or retry.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from distr.core.db import Base, engine, get_session
from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def ensure_project_execution_tables() -> None:
    # Import workflow models so ForeignKey targets are registered in metadata
    # when this helper is used before the full app startup path has run.
    from distr.core.db import workflow as _workflow  # noqa: F401

    Base.metadata.create_all(engine, tables=[
        ProjectExecutionSession.__table__,
        ProjectExecutionEvent.__table__,
    ])


def create_execution_session(
    *,
    project_id: int,
    ticket_id: int | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    audit_id: int | None = None,
    route_type: str = "project_cli",
    route_backend: str = "",
    selected_model: str = "",
    selection_reason: str = "",
    complexity: str = "",
    origin: str = "",
    input_packet: dict[str, Any] | None = None,
) -> int:
    ensure_project_execution_tables()
    with get_session() as session:
        row = ProjectExecutionSession(
            ticket_id=ticket_id,
            project_id=int(project_id),
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            audit_id=audit_id,
            route_type=route_type,
            route_backend=route_backend,
            selected_model=selected_model or "",
            selection_reason=selection_reason or "",
            complexity=complexity or "",
            origin=origin or "",
            status="queued",
            input_packet=_json_dumps(input_packet or {}),
        )
        session.add(row)
        session.flush()
        event = ProjectExecutionEvent(
            session_id=row.id,
            event_type="session_created",
            status="queued",
            message="Project execution session created.",
            payload=_json_dumps(input_packet or {}),
        )
        session.add(event)
        session.commit()
        try:
            from distr.core.orchestrator import resolve_board_id_for_ticket
            from distr.core.orchestration_events import emit_orchestration_event

            emit_orchestration_event(
                source=route_backend or "executor",
                event_type="execution_session_created",
                status="queued",
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                ticket_id=ticket_id,
                board_id=resolve_board_id_for_ticket(ticket_id),
                project_id=int(project_id),
                execution_session_id=int(row.id),
                summary="Project execution session created.",
                payload={
                    "route_type": route_type,
                    "route_backend": route_backend,
                    "model": selected_model,
                    "complexity": complexity,
                    "origin": origin,
                    "selection_reason": selection_reason,
                    "input_packet": input_packet or {},
                },
            )
        except Exception:
            pass
        return int(row.id)


def append_execution_event(
    session_id: int | None,
    event_type: str,
    *,
    status: str | None = None,
    message: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    if not session_id:
        return
    ensure_project_execution_tables()
    with get_session() as session:
        row = session.query(ProjectExecutionSession).filter(ProjectExecutionSession.id == int(session_id)).first()
        if not row:
            return
        if row and status:
            row.status = status
            row.updated_at = datetime.utcnow()
        session.add(ProjectExecutionEvent(
            session_id=int(session_id),
            event_type=event_type,
            status=status,
            message=message or "",
            payload=_json_dumps(payload or {}),
        ))
        session.commit()
        try:
            from distr.core.orchestration_events import emit_orchestration_event

            emit_orchestration_event(
                source=row.route_backend or "executor",
                event_type=f"execution_{event_type or 'event'}",
                status=status,
                workflow_id=row.workflow_id,
                run_id=row.run_id,
                step_id=row.step_id,
                ticket_id=row.ticket_id,
                project_id=row.project_id,
                execution_session_id=row.id,
                summary=message or "",
                payload=payload or {},
            )
        except Exception:
            pass


def complete_execution_session(
    session_id: int | None,
    *,
    success: bool,
    output_packet: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    if not session_id:
        return
    ensure_project_execution_tables()
    status = "completed" if success else "failed"
    with get_session() as session:
        row = session.query(ProjectExecutionSession).filter(ProjectExecutionSession.id == int(session_id)).first()
        if not row:
            return
        row.status = status
        row.output_packet = _json_dumps(output_packet or {})
        row.error = error or ""
        row.completed_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        session.add(ProjectExecutionEvent(
            session_id=row.id,
            event_type="session_completed",
            status=status,
            message="Project execution session completed." if success else "Project execution session failed.",
            payload=_json_dumps(output_packet or {}),
        ))
        session.commit()
        try:
            from distr.core.orchestration_events import emit_orchestration_event

            emit_orchestration_event(
                source=row.route_backend or "executor",
                event_type="execution_session_completed",
                status=status,
                workflow_id=row.workflow_id,
                run_id=row.run_id,
                step_id=row.step_id,
                ticket_id=row.ticket_id,
                project_id=row.project_id,
                execution_session_id=row.id,
                summary="Project execution session completed." if success else "Project execution session failed.",
                payload=output_packet or {},
                evidence={"error": error} if error else {},
            )
        except Exception:
            pass


def list_execution_sessions_for_ticket(ticket_id: int, limit: int = 20) -> list[dict[str, Any]]:
    ensure_project_execution_tables()
    with get_session() as session:
        rows = (
            session.query(ProjectExecutionSession)
            .filter(ProjectExecutionSession.ticket_id == int(ticket_id))
            .order_by(ProjectExecutionSession.started_at.desc())
            .limit(limit)
            .all()
        )
        return [serialize_execution_session(row, include_events=True) for row in rows]


def list_execution_sessions_for_workflow(
    workflow_id: int,
    *,
    limit: int = 50,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    """Return project CLI/Codex sessions tied to a workflow, enriched for the UI."""
    ensure_project_execution_tables()
    from distr.core.db.kanban import KanbanBoard, KanbanTicket
    from distr.core.db.projects import Project

    with get_session() as session:
        query = (
            session.query(ProjectExecutionSession)
            .filter(ProjectExecutionSession.workflow_id == int(workflow_id))
            .order_by(ProjectExecutionSession.started_at.desc())
        )
        if active_only:
            query = query.filter(ProjectExecutionSession.status.in_(["queued", "running"]))
        rows = query.limit(max(1, min(int(limit or 50), 200))).all()
        tickets = {}
        projects = {}
        boards = {}
        ticket_ids = [row.ticket_id for row in rows if row.ticket_id]
        project_ids = [row.project_id for row in rows if row.project_id]
        if ticket_ids:
            ticket_rows = session.query(KanbanTicket).filter(KanbanTicket.id.in_(ticket_ids)).all()
            tickets = {row.id: row for row in ticket_rows}
            board_ids = [row.lane.board_id for row in ticket_rows if getattr(row, "lane", None)]
            if board_ids:
                boards = {
                    row.id: row
                    for row in session.query(KanbanBoard).filter(KanbanBoard.id.in_(board_ids)).all()
                }
        if project_ids:
            projects = {
                row.id: row
                for row in session.query(Project).filter(Project.id.in_(project_ids)).all()
            }
        return [
            _enrich_execution_session_for_ui(
                row,
                ticket=tickets.get(row.ticket_id),
                project=projects.get(row.project_id),
                boards=boards,
                include_events=True,
            )
            for row in rows
        ]


def _loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def _seconds_between(start: datetime | None, end: datetime | None = None) -> int:
    if not start:
        return 0
    finish = end or datetime.utcnow()
    try:
        return max(0, int((finish - start).total_seconds()))
    except Exception:
        return 0


def _enrich_execution_session_for_ui(
    row: ProjectExecutionSession,
    *,
    ticket: Any = None,
    project: Any = None,
    boards: dict[int, Any] | None = None,
    include_events: bool = False,
) -> dict[str, Any]:
    data = serialize_execution_session(row, include_events=include_events)
    input_packet = data.get("input_packet") if isinstance(data.get("input_packet"), dict) else {}
    output_packet = data.get("output_packet") if isinstance(data.get("output_packet"), dict) else {}
    board = None
    if ticket and getattr(ticket, "lane", None):
        board = (boards or {}).get(ticket.lane.board_id)
    data.update({
        "ticket_title": getattr(ticket, "title", None) or input_packet.get("ticket_title") or (f"Ticket #{row.ticket_id}" if row.ticket_id else ""),
        "board_id": getattr(board, "id", None),
        "board_name": getattr(board, "name", None) or input_packet.get("board_name") or "",
        "project_name": getattr(project, "name", None) or input_packet.get("project_name") or "",
        "project_folder": getattr(project, "folder_location", None) or input_packet.get("folder") or "",
        "backend_id": row.route_backend or output_packet.get("backend_id") or "",
        "engine": output_packet.get("engine") or row.route_backend or "",
        "model": row.selected_model or input_packet.get("model") or output_packet.get("model") or "",
        "instruction": input_packet.get("instruction") or "",
        "elapsed_seconds": _seconds_between(row.started_at),
        "duration_seconds": _seconds_between(row.started_at, row.completed_at) if row.completed_at else None,
    })
    return data


def serialize_execution_session(row: ProjectExecutionSession, *, include_events: bool = False) -> dict[str, Any]:
    elapsed_seconds = None
    if row.started_at:
        end = row.completed_at or row.updated_at or datetime.utcnow()
        elapsed_seconds = max(0, int((end - row.started_at).total_seconds()))
    data = {
        "id": row.id,
        "ticket_id": row.ticket_id,
        "project_id": row.project_id,
        "workflow_id": row.workflow_id,
        "run_id": row.run_id,
        "step_id": row.step_id,
        "audit_id": row.audit_id,
        "route_type": row.route_type,
        "route_backend": row.route_backend,
        "selected_model": row.selected_model,
        "selection_reason": row.selection_reason,
        "complexity": row.complexity,
        "origin": row.origin,
        "status": row.status,
        "input_packet": _loads(row.input_packet),
        "output_packet": _loads(row.output_packet),
        "error": row.error,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "elapsed_seconds": elapsed_seconds,
    }
    if include_events:
        data["events"] = [
            {
                "id": event.id,
                "event_type": event.event_type,
                "status": event.status,
                "message": event.message,
                "payload": _loads(event.payload),
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in (row.events or [])
        ]
    return data
