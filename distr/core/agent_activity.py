"""Shared agent activity contract for chat, Telegram, and workflow surfaces."""

from __future__ import annotations

from typing import Any


_ACTIVE_STATUSES = {"queued", "running", "waiting"}
_DONE_STATUSES = {"completed", "passed", "done", "success"}
_FAILED_STATUSES = {"failed", "error", "cancelled", "canceled"}


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _status(value: str | None) -> str:
    raw = str(value or "running").strip().lower().replace("-", "_")
    if raw in _ACTIVE_STATUSES:
        return raw
    if raw in _DONE_STATUSES:
        return "completed"
    if raw in _FAILED_STATUSES:
        return "cancelled" if raw in {"cancelled", "canceled"} else "failed"
    return "running"


def _event_type_for_status(status: str) -> str:
    if status == "completed":
        return "worker_completed"
    if status in {"failed", "cancelled"}:
        return "worker_failed"
    if status == "waiting":
        return "needs_input"
    return "worker_progress"


def _default_run_key(
    *,
    surface: str,
    chat_id: int | None,
    workflow_id: int | None,
    run_id: int | None,
    ticket_id: int | None,
    project_id: int | None,
) -> str:
    if workflow_id is not None and run_id is not None:
        return f"workflow:{int(workflow_id)}:run:{int(run_id)}"
    if chat_id is not None:
        return f"chat:{int(chat_id)}"
    if ticket_id is not None:
        return f"ticket:{int(ticket_id)}"
    if project_id is not None:
        return f"project:{int(project_id)}"
    return f"{surface or 'agent'}:adhoc"


def _owner(
    *,
    chat_id: int | None,
    workflow_id: int | None,
    run_id: int | None,
    step_id: int | None,
    ticket_id: int | None,
    board_id: int | None,
    project_id: int | None,
    execution_session_id: int | None,
) -> dict[str, int]:
    values = {
        "chat_id": chat_id,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "step_id": step_id,
        "ticket_id": ticket_id,
        "board_id": board_id,
        "project_id": project_id,
        "execution_session_id": execution_session_id,
    }
    return {key: int(value) for key, value in values.items() if value is not None}


def emit_agent_activity_step(
    *,
    source: str,
    surface: str,
    status: str = "running",
    title: str,
    summary: str = "",
    chat_id: int | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
    execution_session_id: int | None = None,
    parent_event_id: int | None = None,
    run_key: str = "",
    thread_key: str = "main",
    step_key: str = "",
    step_type: str = "agent_step",
    context: dict[str, Any] | None = None,
    question: str = "",
    spoken_text: str = "",
    payload: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit one durable agent step and mirror chat-owned work into chat activity."""
    normalized_status = _status(status)
    surface_name = (surface or source or "agent").strip().lower()
    activity_run_key = run_key or _default_run_key(
        surface=surface_name,
        chat_id=chat_id,
        workflow_id=workflow_id,
        run_id=run_id,
        ticket_id=ticket_id,
        project_id=project_id,
    )
    activity = {
        "run_key": activity_run_key,
        "thread_key": (thread_key or "main").strip() or "main",
        "step_key": (step_key or _event_type_for_status(normalized_status)).strip(),
        "step_type": (step_type or "agent_step").strip() or "agent_step",
        "surface": surface_name,
        "source": (source or surface_name or "agent").strip().lower(),
        "status": normalized_status,
        "title": _clean_text(title, limit=160),
        "summary": _clean_text(summary or title, limit=700),
        "owner": _owner(
            chat_id=chat_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            ticket_id=ticket_id,
            board_id=board_id,
            project_id=project_id,
            execution_session_id=execution_session_id,
        ),
    }
    if isinstance(context, dict) and context:
        activity["context"] = context
    if question:
        activity["question"] = _clean_text(question, limit=500)
    if spoken_text:
        activity["spoken_text"] = _clean_text(spoken_text, limit=900)
    event_payload = dict(payload or {})
    event_payload["agent_activity"] = activity
    event_payload.setdefault("surface", surface_name)
    event_payload.setdefault("subtype", "agent_activity_step")
    event_payload.setdefault("thread_id", activity["thread_key"])
    event_payload.setdefault("correlation_id", activity_run_key)

    from distr.core.orchestration_events import emit_orchestration_event

    event_id = emit_orchestration_event(
        source=activity["source"],
        event_type=_event_type_for_status(normalized_status),
        status=normalized_status,
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        board_id=board_id,
        project_id=project_id,
        execution_session_id=execution_session_id,
        parent_event_id=parent_event_id,
        summary=activity["summary"],
        payload=event_payload,
        evidence=evidence or {},
    )

    chat_event = None
    if chat_id is not None:
        from distr.core.workflow.chat_trace import record_chat_workflow_event

        chat_event = record_chat_workflow_event(
            int(chat_id),
            "agent_activity_step",
            status=normalized_status,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            step_name=activity["title"],
            summary=activity["summary"],
            phase=surface_name,
            agent_activity=activity,
        )

    if workflow_id is not None or run_id is not None:
        try:
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
        except Exception:
            pass

    return {"event_id": event_id, "agent_activity": activity, "chat_event": chat_event}


def list_agent_activity(
    *,
    workflow_id: int | None = None,
    run_id: int | None = None,
    project_id: int | None = None,
    execution_session_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return chronological agent activity entries from the orchestration ledger."""
    from distr.core.orchestration_events import list_orchestration_timeline

    entries = list_orchestration_timeline(
        workflow_id=workflow_id,
        run_id=run_id,
        project_id=project_id,
        execution_session_id=execution_session_id,
        limit=limit,
    )
    activity_entries: list[dict[str, Any]] = []
    for entry in entries:
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        activity = payload.get("agent_activity")
        if not isinstance(activity, dict):
            continue
        item = dict(entry)
        item["agent_activity"] = activity
        activity_entries.append(item)
    return activity_entries
