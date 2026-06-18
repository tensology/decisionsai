"""Persist harness / IDE / CLI feedback into workspace memory and learned rules."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_STEER_EVENT_TYPES = frozenset({
    "user_steer",
    "codex_interrupted",
    "codex_waiting",
    "codex_needs_input",
    "cursor_interrupted",
    "cursor_waiting",
    "cursor_needs_input",
    "manual_fix",
    "changes_requested",
})

_TERMINAL_SUFFIXES = ("_completed", "_failed")


def _normalize_event_type(event_type: str) -> str:
    return (event_type or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_steer_event(event_type: str) -> bool:
    low = _normalize_event_type(event_type)
    if low in _STEER_EVENT_TYPES:
        return True
    return any(token in low for token in ("steer", "interrupt", "needs_input", "waiting"))


def _is_terminal_event(event_type: str) -> bool:
    low = _normalize_event_type(event_type)
    if low in {"completed", "failed", "worker_completed", "worker_failed"}:
        return True
    return any(low.endswith(suffix) for suffix in _TERMINAL_SUFFIXES)


def _resolve_linked_workflow_id(ticket_id: int | None) -> int | None:
    if not ticket_id:
        return None
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanTicket

        with get_session() as session:
            row = session.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
            linked = getattr(row, "linked_workflow_id", None) if row else None
            return int(linked) if linked else None
    except Exception:
        return None


def persist_worker_feedback(
    *,
    message: str = "",
    output: str = "",
    input_text: str = "",
    event_type: str = "",
    source: str = "harness",
    ticket_id: int | None = None,
    project_id: int | None = None,
    board_id: int | None = None,
    workflow_id: int | None = None,
    linked_workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    execution_session_id: int | None = None,
    mistake_label: str = "",
    skip_steering_log: bool = False,
    skip_human_intervention: bool = False,
) -> dict[str, Any]:
    """Write human/IDE/CLI feedback to companion handoff, ledger, and learned rules."""
    text = (message or output or input_text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty"}

    low = _normalize_event_type(event_type)
    steer = _is_steer_event(low)
    terminal = _is_terminal_event(low)
    linked_wf = linked_workflow_id or _resolve_linked_workflow_id(ticket_id)
    result: dict[str, Any] = {"ok": True, "text_len": len(text)}

    if terminal or steer:
        try:
            from distr.core.workspace_memory.lifecycle import handoff_cli_session

            handoff_cli_session(
                ticket_id=ticket_id,
                project_id=project_id,
                summary=text[:2000],
                source=low or source,
            )
            result["handoff"] = True
        except Exception:
            logger.debug("persist_worker_feedback: handoff failed", exc_info=True)

    if ticket_id and (terminal or steer):
        try:
            from distr.core.workspace_memory.pickup_handoff import append_ledger

            append_ledger(
                "tickets",
                int(ticket_id),
                event_type=low or source,
                message=text[:500],
                extra={
                    "project_id": project_id,
                    "board_id": board_id,
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "source": source,
                },
            )
            result["ticket_ledger"] = True
        except Exception:
            logger.debug("persist_worker_feedback: ticket ledger failed", exc_info=True)

    if project_id and terminal:
        try:
            from distr.core.workspace_memory.pickup_handoff import append_ledger

            append_ledger(
                "projects",
                int(project_id),
                event_type=low or source,
                message=text[:500],
                extra={"ticket_id": ticket_id, "source": source},
            )
            result["project_ledger"] = True
        except Exception:
            logger.debug("persist_worker_feedback: project ledger failed", exc_info=True)

    if run_id and not skip_steering_log and (steer or terminal):
        try:
            from distr.core.workflow.steering_memory import append_run_steering_entry

            append_run_steering_entry(
                int(run_id),
                source=source,
                event_type=low or "feedback",
                message=text,
                step_id=step_id,
            )
            result["steering_log"] = True
        except Exception:
            logger.debug("persist_worker_feedback: steering log failed", exc_info=True)

    if steer and (mistake_label or low in _STEER_EVENT_TYPES) and not skip_human_intervention:
        try:
            from distr.core.orchestrator import record_human_intervention_memory

            record_human_intervention_memory(
                label=mistake_label or ("manual_fix_applied" if low == "manual_fix" else "ignored_instruction"),
                message=text,
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                ticket_id=ticket_id,
                board_id=board_id,
                project_id=project_id,
                execution_session_id=execution_session_id,
            )
            result["human_intervention"] = True
        except Exception:
            logger.debug("persist_worker_feedback: human intervention failed", exc_info=True)

    if steer or terminal:
        try:
            from distr.core.workflow.standards_memory import capture_feedback_as_memory

            captured = capture_feedback_as_memory(
                text,
                workflow_id=workflow_id,
                linked_workflow_id=linked_wf,
                board_id=board_id,
                project_id=project_id,
            )
            result["captured_standard"] = bool(captured)
        except Exception:
            logger.debug("persist_worker_feedback: standards capture failed", exc_info=True)

    if project_id and (terminal or steer):
        try:
            from distr.core.workspace_memory.sync import sync_projection_for_project

            sync_projection_for_project(int(project_id))
            result["projection_sync"] = True
        except Exception:
            logger.debug("persist_worker_feedback: projection sync failed", exc_info=True)

    return result
