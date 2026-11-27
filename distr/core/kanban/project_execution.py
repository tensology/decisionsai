"""Durable project execution session helpers.

These rows are the Decisions-owned control loop around Codex/Cursor/Pi/CLI work:
what was sent, where it went, what came back, and what the orchestrator can
validate or retry.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from typing import Any

from distr.core.db import Base, engine, get_session
from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
from distr.core.db.time import utc_now_naive

logger = logging.getLogger(__name__)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _redact_persisted_execution_value(value: Any) -> Any:
    """Redact worker-visible secrets before durable storage or event emission."""
    from distr.core.orchestrator import redact_handoff_payload

    return redact_handoff_payload(value)


def _execution_session_for_write(session, session_id):
    connection = session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    row = session.get(ProjectExecutionSession, int(session_id), with_for_update=True)
    return row if row and row.status not in {"cancelled", "canceled"} else None


def execution_session_is_cancelled(session_id: int | None) -> bool:
    if session_id is None:
        return False
    with get_session() as session:
        row = session.get(ProjectExecutionSession, session_id)
        return bool(row and row.status in {"cancelled", "canceled"})


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
    safe_input_packet = _redact_persisted_execution_value(input_packet or {})
    with get_session() as session:
        from distr.core.db.projects import Project
        project = session.get(Project, int(project_id))
        if project is not None and project.deleted_at is not None:
            raise ValueError("Project has been removed from active work")
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
            input_packet=_json_dumps(safe_input_packet),
        )
        session.add(row)
        session.flush()
        event = ProjectExecutionEvent(
            session_id=row.id,
            event_type="session_created",
            status="queued",
            message="Project execution session created.",
            payload=_json_dumps(safe_input_packet),
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
                    "input_packet": safe_input_packet,
                },
            )
        except Exception:
            pass
        try:
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
        except Exception:
            pass
        _start_thread_timer(int(row.id))
        return int(row.id)


def append_execution_event(
    session_id: int | None,
    event_type: str,
    *,
    status: str | None = None,
    message: str = "",
    payload: dict[str, Any] | None = None,
    update_session_status: bool = True,
) -> bool:
    if not session_id:
        return False
    ensure_project_execution_tables()
    safe_message = str(_redact_persisted_execution_value(message or ""))
    safe_payload = _redact_persisted_execution_value(payload or {})
    with get_session() as session:
        row = _execution_session_for_write(session, session_id)
        if not row:
            return False
        if event_type in {"ide_handoff_dispatched", "ide_handoff_waiting"} and (
            row.status in {"completed", "failed", "blocked"}
            or (row.origin == "proactive_orchestrator" and row.completed_at is not None)
        ):
            return False
        if status and update_session_status:
            row.status = status
            row.updated_at = utc_now_naive()
        session.add(ProjectExecutionEvent(
            session_id=int(session_id),
            event_type=event_type,
            status=status,
            message=safe_message,
            payload=_json_dumps(safe_payload),
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
                summary=safe_message,
                payload=safe_payload,
            )
        except Exception:
            pass
        try:
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
        except Exception:
            pass
    return True



def format_execution_session_outcome(status: str, error: str = "", *, provider: str = "", model: str = "") -> str:
    """Plain status line for the live feed: what happened, and what to do next."""
    clean = str(status or "").strip().lower()
    if clean == "canceled":
        clean = "cancelled"
    safe = str(error or "").strip()
    if clean == "cancelled":
        return safe or "The project worker was stopped. You can start this step again when you are ready."
    if clean == "completed":
        return "Project execution session completed."
    if clean == "blocked":
        return "The project worker is waiting for input. Answer the prompt in this thread to continue."

    lowered = safe.lower()
    folder_markers = ("folder is unavailable", "project folder is unavailable", "folder_location")
    if any(marker in lowered for marker in folder_markers):
        return "The linked project folder is unavailable. Relink the project folder in Settings, then retry this step."
    scope_markers = ("scope changed", "project, board or ticket changed", "project, board, or ticket changed")
    if any(marker in lowered for marker in scope_markers):
        return "The project, board, or ticket changed before the worker finished. Review the scope and retry this step."

    from distr.core.llm_errors import (
        classify_model_error,
        format_model_error,
        is_formatted_model_error_message,
    )

    if is_formatted_model_error_message(safe):
        return safe
    kind = classify_model_error(safe)
    modelish = kind != "model_error" or any(
        token in lowered for token in ("error code", "status code", "provider", "openai", "ollama", "anthropic")
    )
    if safe and modelish:
        return format_model_error(safe, provider=provider, model=model, operation="run this project step")
    if safe:
        detail = safe
        if len(detail) > 240:
            detail = detail[:237].rstrip() + "..."
        return (
            f"The project worker could not finish: {detail} "
            "Retry this step, or pick another model in Settings if the same error repeats."
        )
    return (
        "The project worker stopped before it could finish. "
        "Check this step, then retry it or pick another model in Settings."
    )


def execution_activity_message(event_message: str, status: str, error: str = "", *, provider: str = "", model: str = "") -> str:
    """Prefer a plain outcome when a stored event still has a generic session line."""
    message = str(event_message or "").strip()
    generic = {
        "",
        "Project execution session failed.",
        "Project execution session cancelled.",
        "Project execution session blocked; input is required.",
    }
    if message in generic or message.lower() == "project execution session failed.":
        return format_execution_session_outcome(status, error, provider=provider, model=model)
    return message


def complete_execution_session(
    session_id: int | None,
    *,
    success: bool,
    output_packet: dict[str, Any] | None = None,
    error: str = "",
    status: str | None = None,
    merge_output_packet: bool = False,
) -> None:
    if not session_id:
        return
    ensure_project_execution_tables()
    clean_status = str(status or "").strip().lower()
    if clean_status not in {"completed", "failed", "blocked", "cancelled", "canceled"}:
        clean_status = "completed" if success else "failed"
    if clean_status == "canceled":
        clean_status = "cancelled"
    safe_output_packet = _redact_persisted_execution_value(output_packet or {})
    safe_error = str(_redact_persisted_execution_value(error or ""))
    now = utc_now_naive()
    with get_session() as session:
        row = _execution_session_for_write(session, session_id)
        if not row:
            return
        row.status = clean_status
        if merge_output_packet:
            original = json.loads(row.output_packet or '{}')
            safe_output_packet = {**(original if isinstance(original, dict) else {}), **safe_output_packet}
        row.output_packet = _json_dumps(safe_output_packet)
        pending_review = safe_output_packet.get("proactive_result_pending_review") is True
        row.error = safe_error
        row.completed_at = now
        row.updated_at = now
        if clean_status == "cancelled":
            event_type = "session_cancelled"
        elif clean_status == "blocked":
            event_type = "session_blocked"
        elif clean_status == "failed":
            event_type = "session_failed"
        else:
            event_type = "session_completed"
        message = format_execution_session_outcome(
            clean_status, safe_error,
            provider=row.route_backend or "",
            model=row.selected_model or "",
        )
        session.add(ProjectExecutionEvent(
            session_id=row.id,
            event_type=event_type,
            status=clean_status,
            message=message,
            payload=_json_dumps(safe_output_packet),
        ))
        session.commit()
        try:
            from distr.core.orchestration_events import emit_orchestration_event

            emit_orchestration_event(
                source=row.route_backend or "executor",
                event_type="execution_session_completed",
                status="pending_review" if pending_review else clean_status,
                workflow_id=row.workflow_id,
                run_id=row.run_id,
                step_id=row.step_id,
                ticket_id=row.ticket_id,
                project_id=row.project_id,
                execution_session_id=row.id,
                summary="The worker returned. Checking the approved scope." if pending_review else message,
                payload={} if pending_review else safe_output_packet,
                evidence={"error": safe_error} if safe_error and not pending_review else {},
            )
        except Exception:
            pass
        try:
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
        except Exception:
            pass

    _settle_thread_timer(int(session_id), now)
    _record_ticket_outcome(int(session_id))
    if clean_status == "completed" and not pending_review:
        persist_execution_feedback(row, safe_output_packet)


def persist_execution_feedback(row: ProjectExecutionSession, packet: dict[str, Any]) -> None:
    try:
        summary = str(packet.get("output") or "").strip()
        if summary:
            from distr.core.workspace_memory.feedback_sync import persist_worker_feedback

            persist_worker_feedback(
                message=summary,
                event_type="cli_completed",
                source=str(packet.get("backend_id") or row.route_backend or "cli"),
                ticket_id=row.ticket_id,
                project_id=row.project_id,
                workflow_id=row.workflow_id,
                run_id=row.run_id,
                step_id=row.step_id,
                execution_session_id=row.id,
                skip_steering_log=bool(row.run_id),
            )
    except Exception:
        pass


def cancel_execution_session(session_id: int | None, *, reason: str = "Stopped by the user.") -> None:
    """Close a direct execution session without recording a provider failure."""
    if not session_id:
        return
    ensure_project_execution_tables()
    safe_reason = str(_redact_persisted_execution_value(reason or "Stopped by the user."))
    with get_session() as session:
        row = _execution_session_for_write(session, session_id)
        if not row:
            return
        row.status = "cancelled"
        row.error = safe_reason
        row.completed_at = utc_now_naive()
        row.updated_at = utc_now_naive()
        session.add(ProjectExecutionEvent(
            session_id=row.id,
            event_type="session_cancelled",
            status="cancelled",
            message=safe_reason,
            payload=_json_dumps({}),
        ))
        session.commit()
    _settle_thread_timer(int(session_id), utc_now_naive())
    try:
        from distr.gui.web.workflow_events import increment_workflow_updated

        increment_workflow_updated()
    except Exception:
        pass


# After this many runs in a row fail for the same reason, stop retrying and
# mark the ticket blocked with that reason instead of re-running it.
SAME_FAILURE_BLOCK_AFTER = 3


def _failure_reason_key(error: str) -> str:
    import re

    text = re.sub(r"\d+", "#", str(error or "").lower())
    return re.sub(r"\s+", " ", text).strip()[:90]


def _record_ticket_outcome(session_id: int) -> None:
    """Keep the ticket's status honest and stop retry loops.

    - a provider failure that failed over is not the run's outcome (the run's
      final result is), so it never marks the ticket failed;
    - a run blocked on Paul (needs_input) marks the ticket blocked with why;
    - N failures in a row with the same reason mark it blocked, so nothing keeps
      re-running it until someone says continue.
    """
    try:
        from distr.core.db.kanban import KanbanTicket
        from distr.core.kanban.ticket_audit import append_ticket_audit_entry

        with get_session() as session:
            row = session.get(ProjectExecutionSession, int(session_id))
            if row is None or not row.ticket_id:
                return
            ticket = session.get(KanbanTicket, int(row.ticket_id))
            if ticket is None:
                return
            output = _loads(row.output_packet) or {}
            if isinstance(output, dict) and (output.get("fallback_model") or output.get("fallback_backend")) and row.status != "completed":
                return
            reason = ""
            if row.status == "completed":
                if str(ticket.workflow_status or "") in {"failed", "blocked"}:
                    ticket.workflow_status = "completed"
                if _run_changed_files(output):
                    _advance_to_qa(session, row, ticket)
            elif row.status == "blocked":
                reason = str(row.error or "Waiting on your input.")
            elif row.status == "failed":
                recent = (
                    session.query(ProjectExecutionSession.status, ProjectExecutionSession.error)
                    .filter(ProjectExecutionSession.ticket_id == int(row.ticket_id))
                    .order_by(ProjectExecutionSession.id.desc())
                    .limit(SAME_FAILURE_BLOCK_AFTER)
                    .all()
                )
                keys = {_failure_reason_key(error) for status, error in recent if status == "failed"}
                if len(recent) >= SAME_FAILURE_BLOCK_AFTER and len(keys) == 1 and all(st == "failed" for st, _ in recent):
                    reason = (
                        f"Failed {SAME_FAILURE_BLOCK_AFTER} times in a row for the same reason: "
                        f"{str(row.error or '').strip()[:240]}"
                    )
            if reason and str(ticket.workflow_status or "") != "blocked":
                ticket.workflow_status = "blocked"
                append_ticket_audit_entry(
                    session,
                    ticket_id=int(row.ticket_id),
                    run_id=row.run_id,
                    step_id=row.step_id,
                    step_result_id=None,
                    execution_lane="cli",
                    status="blocked",
                    final_verdict="blocked",
                    summary=f"Blocked: {reason[:300]}",
                    details="Not retried automatically. Say continue on the ticket to try again.",
                )
            session.commit()
    except Exception:
        logger.debug("Ticket outcome update failed for execution %s", session_id, exc_info=True)


def _run_changed_files(output: dict) -> bool:
    """A finished write run that actually changed the workspace."""
    if not isinstance(output, dict) or output.get("waits_for_human"):
        return False
    if output.get("read_only_expected") or output.get("success") is False:
        return False
    delta = output.get("workspace_state_delta")
    if isinstance(delta, dict) and delta.get("changed"):
        return True
    before, after = output.get("git_status_before"), output.get("git_status_after")
    return bool(after) and before != after


def _advance_to_qa(session, row, ticket) -> bool:
    """In Progress -> QA once the work is done; Complete stays Paul's call."""
    try:
        from distr.core.kanban.lifecycle import move_ticket_to_delivery_lane
        from distr.core.kanban.ticket_audit import append_ticket_audit_entry

        lane = getattr(getattr(ticket, "lane", None), "name", "") or ""
        if lane.strip().lower() != "in progress":
            return False
        if not move_ticket_to_delivery_lane(session, int(ticket.id), "QA"):
            return False
        append_ticket_audit_entry(
            session,
            ticket_id=int(ticket.id),
            run_id=row.run_id,
            step_id=row.step_id,
            step_result_id=None,
            execution_lane="cli",
            status="completed",
            final_verdict="completed",
            summary="Moved to QA: the run finished and changed files.",
            details=str((_loads(row.output_packet) or {}).get("summary") or "")[:2000],
        )
        return True
    except ValueError:
        return False
    except Exception:
        logger.debug("QA move failed for ticket %s", getattr(ticket, "id", None), exc_info=True)
        return False


def _execution_chat_id(session_id: int) -> int | None:
    """The Development thread a Pi/Codex/workflow-step run belongs to."""
    try:
        with get_session() as session:
            row = session.get(ProjectExecutionSession, int(session_id))
            if row is None:
                return None
            packet = _loads(row.input_packet) or {}
            chat_id = packet.get("chat_id") if isinstance(packet, dict) else None
            if not chat_id and row.run_id:
                from distr.core.db.workflow import AutoWorkflowRun

                run = session.get(AutoWorkflowRun, int(row.run_id))
                chat_id = getattr(run, "chat_id", None)
            return int(chat_id) if chat_id else None
    except Exception:
        return None


def _start_thread_timer(session_id: int) -> None:
    """Real work started: run the thread (and its ticket) timer."""
    chat_id = _execution_chat_id(session_id)
    if not chat_id:
        return
    try:
        from distr.core.workflow.development_control import resume_thread_time

        resume_thread_time(int(chat_id))
    except Exception:
        pass  # no Development thread for this run, or the timer is unavailable


def _settle_thread_timer(session_id: int, finished_at) -> None:
    """Real work stopped: close the timer at the run's end unless other work holds it."""
    chat_id = _execution_chat_id(session_id)
    if not chat_id:
        return
    try:
        from distr.core.workflow.development_control import settle_finished_thread_time

        settle_finished_thread_time(int(chat_id), finished_at=finished_at)
    except Exception:
        pass


def list_execution_sessions_for_ticket(ticket_id: int, limit: int = 20) -> list[dict[str, Any]]:
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket

    if limit <= 0:
        return []
    ensure_project_execution_tables()
    with get_session() as session:
        ticket = session.get(KanbanTicket, int(ticket_id))
        lane = session.get(KanbanLane, ticket.lane_id) if ticket else None
        board = session.get(KanbanBoard, lane.board_id) if lane else None
        project_id = (ticket.linked_project_id if ticket and ticket.linked_project_id is not None
                      else board.default_project_id if board else None)
        rows = (
            session.query(ProjectExecutionSession)
            .filter(ProjectExecutionSession.ticket_id == int(ticket_id))
            .order_by(ProjectExecutionSession.started_at.desc())
            .yield_per(max(20, limit))
        )
        results = []
        for row in rows:
            if row.origin == "proactive_orchestrator":
                inputs = _loads(row.input_packet)
                if (board is None or row.project_id != project_id or not isinstance(inputs, dict)
                        or inputs.get("board_id") != board.id):
                    continue
            results.append(serialize_execution_session(row, include_events=True))
            if len(results) >= limit:
                break
        return results


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
    finish = end or utc_now_naive()
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
    from distr.core.project_cli_backends.live_sessions import (
        live_session_connected,
        live_session_external_id,
        live_session_is_alive,
    )

    data = serialize_execution_session(row, include_events=include_events)
    input_packet = data.get("input_packet") if isinstance(data.get("input_packet"), dict) else {}
    output_packet = data.get("output_packet") if isinstance(data.get("output_packet"), dict) else {}
    board = None
    if ticket and getattr(ticket, "lane", None):
        board = (boards or {}).get(ticket.lane.board_id)
    board_id = getattr(board, "id", None) or input_packet.get("board_id")
    data.update({
        "ticket_title": getattr(ticket, "title", None) or input_packet.get("ticket_title") or (f"Ticket #{row.ticket_id}" if row.ticket_id else ""),
        "board_id": board_id,
        "board_name": getattr(board, "name", None) or input_packet.get("board_name") or "",
        "project_name": getattr(project, "name", None) or input_packet.get("project_name") or "",
        "project_folder": getattr(project, "folder_location", None) or input_packet.get("folder") or "",
        "backend_id": row.route_backend or output_packet.get("backend_id") or "",
        "engine": output_packet.get("engine") or row.route_backend or "",
        "model": row.selected_model or input_packet.get("model") or output_packet.get("model") or "",
        "instruction": input_packet.get("instruction") or "",
        "elapsed_seconds": _seconds_between(row.started_at),
        "duration_seconds": _seconds_between(row.started_at, row.completed_at) if row.completed_at else None,
        "live_running": live_session_is_alive(row.project_id, row.route_backend or "", board_id=board_id),
        "live_connected": live_session_connected(row.project_id, row.route_backend or "", board_id=board_id),
        "external_thread_id": input_packet.get("external_thread_id") or live_session_external_id(row.project_id, row.route_backend or "", board_id=board_id),
    })
    return data


def serialize_execution_session(row: ProjectExecutionSession, *, include_events: bool = False) -> dict[str, Any]:
    elapsed_seconds = None
    if row.started_at:
        end = row.completed_at or row.updated_at or utc_now_naive()
        elapsed_seconds = max(0, int((end - row.started_at).total_seconds()))
    output = _loads(row.output_packet)
    held = isinstance(output, dict) and output.get("proactive_result_held") is True
    pending = isinstance(output, dict) and output.get("proactive_result_pending_review") is True
    if held or pending:
        from distr.core.orchestrator_proactive import PROACTIVE_RESULT_HELD

        output = {"summary": PROACTIVE_RESULT_HELD if held else "The worker returned. Checking the approved scope."}
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
        "status": "held" if held else "pending_review" if pending else row.status,
        "input_packet": {} if held or pending else _loads(row.input_packet),
        "output_packet": output,
        "error": output["summary"] if held else "" if pending else row.error,
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
            for event in (() if held or pending else row.events or [])
        ]
    return data
