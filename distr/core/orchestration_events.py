"""Canonical orchestration event contract over the Hermes ledger."""

from __future__ import annotations

import re
from typing import Any


STANDARD_EVENT_TYPES = {
    "run_started",
    "route_decided",
    "worker_dispatched",
    "worker_progress",
    "needs_input",
    "approval_requested",
    "worker_completed",
    "worker_failed",
    "memory_written",
    "user_notified",
}

WORKER_SOURCES = {"codex", "cursor", "cursor_ide", "vscode_ide", "pi", "executor", "ide", "claude_code"}

LEGACY_EVENT_TYPE_MAP = {
    "workflow_run_started": "run_started",
    "workflow_run_resumed": "run_started",
    "workflow_run_completed": "worker_completed",
    "workflow_run_cancelled": "worker_failed",
    "route_decided": "route_decided",
    "route_approval_requested": "approval_requested",
    "approval_requested": "approval_requested",
    "route_approval_granted": "worker_progress",
    "route_approval_rejected": "worker_progress",
    "execution_session_created": "worker_dispatched",
    "execution_executor_start": "worker_dispatched",
    "execution_session_completed": "worker_completed",
    "session_created": "worker_dispatched",
    "executor_start": "worker_dispatched",
    "session_completed": "worker_completed",
    "message_update": "worker_progress",
    "message_start": "worker_progress",
    "message_end": "worker_progress",
    "command_start": "worker_progress",
    "codex_started": "worker_dispatched",
    "codex_progress": "worker_progress",
    "user_steer": "needs_input",
    "codex_waiting": "needs_input",
    "codex_needs_input": "needs_input",
    "codex_interrupted": "needs_input",
    "codex_completed": "worker_completed",
    "codex_failed": "worker_failed",
    "ide_work_packet_created": "worker_dispatched",
    "ide_iteration_completed": "worker_completed",
    "human_intervention_recorded": "needs_input",
    "learned_rule_updated": "memory_written",
    "validation_recorded": "worker_progress",
}


def _clean_user_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"\bHermes\s+says\b", "I", text, flags=re.IGNORECASE)
    text = re.sub(r"\bHermes\s+said\b", "I", text, flags=re.IGNORECASE)
    text = re.sub(r"\bHermes\b", "the orchestrator", text, flags=re.IGNORECASE)
    return text.strip()


def normalize_orchestration_event_type(
    event_type: str,
    *,
    source: str = "",
    status: str | None = None,
) -> str:
    """Map legacy workflow/worker names onto the shared orchestration vocabulary."""
    raw = (event_type or "worker_progress").strip().lower() or "worker_progress"
    raw = raw.replace("-", "_").replace(" ", "_")
    if raw in STANDARD_EVENT_TYPES:
        return raw
    if raw == "execution_dispatched":
        normalized_status = str(status or "").strip().lower()
        if normalized_status in {"done", "completed", "success"}:
            return "worker_completed"
        if normalized_status in {"failed", "error", "cancelled", "canceled"}:
            return "worker_failed"
        return "worker_dispatched"
    if raw in LEGACY_EVENT_TYPE_MAP:
        mapped = LEGACY_EVENT_TYPE_MAP[raw]
    elif raw.startswith("execution_"):
        mapped = "worker_progress"
    elif (source or "").strip().lower() in WORKER_SOURCES:
        mapped = "worker_progress"
    else:
        mapped = "worker_progress"
    if mapped == "worker_completed" and str(status or "").lower() in {"failed", "error", "cancelled", "canceled"}:
        return "worker_failed"
    return mapped


def _source_label(source: Any) -> str:
    raw = str(source or "").strip().lower()
    labels = {
        "codex": "Codex",
        "cursor": "Cursor",
        "cursor_ide": "Cursor",
        "vscode_ide": "VS Code",
        "pi": "Pi",
        "executor": "The project worker",
        "workflow": "The workflow",
        "telegram": "Telegram",
        "chat": "Chat",
        "voice": "Voice",
        "notification": "I",
        "ide": "The editor",
        "claude_code": "Claude Code",
    }
    return labels.get(raw, raw.replace("_", " ").title() if raw else "The worker")


def _notification_text(entry: dict[str, Any]) -> tuple[bool, str]:
    event_type = str(entry.get("event_type") or "").strip()
    status = str(entry.get("status") or "").strip()
    source = str(entry.get("source") or "").strip()
    source_label = _source_label(source)
    summary = _clean_user_text(entry.get("summary") or "")
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}

    if event_type == "run_started":
        return True, summary or "Workflow started."
    if event_type == "route_decided":
        decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
        backend = _source_label(decision.get("backend") or payload.get("backend") or "")
        text = summary or f"I chose {backend} for this work."
        readiness = payload.get("visual_baseline_readiness")
        if isinstance(readiness, dict) and readiness and not readiness.get("ready"):
            missing_count = int(readiness.get("missing_screen_count") or 0)
            detail = (
                f"visual baseline not ready ({missing_count} missing reference screen"
                f"{'s' if missing_count != 1 else ''})"
                if missing_count
                else "visual baseline not ready"
            )
            if detail.lower() not in text.lower():
                text = f"{text}; {detail}"
        return False, text
    if event_type == "worker_dispatched":
        return True, summary or f"{source_label} started on the work."
    if event_type == "worker_progress":
        return False, summary or f"{source_label} sent a progress update."
    if event_type == "needs_input":
        detail = f": {summary}" if summary else "."
        return True, f"{source_label} needs input{detail}"
    if event_type == "approval_requested":
        detail = f": {summary}" if summary else "."
        return True, f"I need your approval{detail}"
    if event_type == "worker_completed":
        return True, summary or f"{source_label} finished the work."
    if event_type == "worker_failed":
        return True, summary or f"{source_label} hit a problem."
    if event_type == "memory_written":
        return False, summary or "I saved a useful learning from that run."
    if event_type == "user_notified":
        return False, summary or "I sent the update."
    return False, summary


def build_orchestration_notification(entry: dict[str, Any]) -> dict[str, Any]:
    """Return user-facing notification text for an event without internal branding."""
    should_notify, text = _notification_text(entry or {})
    return {
        "should_notify": bool(should_notify),
        "text": _clean_user_text(text),
        "channels": list((entry or {}).get("notify_channels") or []),
    }


def emit_orchestration_event(
    *,
    source: str,
    event_type: str,
    status: str | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
    execution_session_id: int | None = None,
    parent_event_id: int | None = None,
    summary: str = "",
    payload: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    notify_channels: list[str] | None = None,
) -> int | None:
    """Emit one canonical event into the Hermes ledger."""
    legacy_event_type = (event_type or "worker_progress").strip() or "worker_progress"
    standard_event_type = normalize_orchestration_event_type(
        legacy_event_type,
        source=source,
        status=status,
    )
    enriched_payload = dict(payload or {})
    enriched_payload["orchestration"] = {
        "event_type": standard_event_type,
        "legacy_event_type": "" if legacy_event_type == standard_event_type else legacy_event_type,
        "source": (source or "system").strip() or "system",
        "notify_channels": list(notify_channels or []),
    }

    from distr.core.hermes import emit_event

    event_id = emit_event(
        source=source,
        event_type=standard_event_type,
        status=status,
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        board_id=board_id,
        project_id=project_id,
        execution_session_id=execution_session_id,
        parent_event_id=parent_event_id,
        summary=_clean_user_text(summary),
        payload=enriched_payload,
        evidence=evidence or {},
    )
    return event_id


def emit_user_notification(
    *,
    channel: str,
    text: str,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
    execution_session_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> int | None:
    """Record that a user-facing channel was notified."""
    clean_text = _clean_user_text(text)
    data = {"channel": (channel or "chat").strip().lower(), **(payload or {})}
    return emit_orchestration_event(
        source="notification",
        event_type="user_notified",
        status="sent",
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        board_id=board_id,
        project_id=project_id,
        execution_session_id=execution_session_id,
        summary=clean_text,
        payload=data,
        notify_channels=[data["channel"]],
    )


def _timeline_entry(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    orchestration = payload.get("orchestration") if isinstance(payload.get("orchestration"), dict) else {}
    event_type = orchestration.get("event_type") or normalize_orchestration_event_type(
        str(event.get("event_type") or ""),
        source=str(event.get("source") or ""),
        status=event.get("status"),
    )
    legacy_event_type = orchestration.get("legacy_event_type") or (
        str(event.get("event_type") or "") if str(event.get("event_type") or "") != event_type else ""
    )
    entry = {
        "id": event.get("id"),
        "event_uid": event.get("event_uid"),
        "event_type": event_type,
        "legacy_event_type": legacy_event_type,
        "source": event.get("source") or "",
        "status": event.get("status") or "",
        "workflow_id": event.get("workflow_id"),
        "run_id": event.get("run_id"),
        "step_id": event.get("step_id"),
        "ticket_id": event.get("ticket_id"),
        "board_id": event.get("board_id"),
        "project_id": event.get("project_id"),
        "execution_session_id": event.get("execution_session_id"),
        "summary": _clean_user_text(event.get("summary") or ""),
        "payload": payload,
        "evidence": event.get("evidence") or {},
        "created_at": event.get("created_at"),
        "notify_channels": orchestration.get("notify_channels") or [],
    }
    entry["notification"] = build_orchestration_notification(entry)
    return entry


def list_orchestration_timeline(
    *,
    workflow_id: int | None = None,
    run_id: int | None = None,
    project_id: int | None = None,
    execution_session_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return a chronological conversation timeline from Hermes events."""
    from distr.core.hermes import list_events

    events = list_events(
        workflow_id=workflow_id,
        run_id=run_id,
        project_id=project_id,
        execution_session_id=execution_session_id,
        limit=limit,
    )
    return [_timeline_entry(event) for event in reversed(events)]
