"""Ambient harness event intake for Codex, Cursor, Claude, and Pi."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

from distr.core.orchestration_events import emit_orchestration_event, emit_user_notification
from distr.core.orchestrator import record_learning_signal


@dataclass
class HarnessEventPayload:
    harness: str = "worker"
    event_type: str = "worker_progress"
    status: str | None = None
    message: str = ""
    input: str = ""
    output: str = ""
    source: str = "ambient"
    project_folder: str = ""
    project_id: int | None = None
    workflow_id: int | None = None
    run_id: int | None = None
    step_id: int | None = None
    ticket_id: int | None = None
    board_id: int | None = None
    execution_session_id: int | None = None
    thread_id: str = ""
    session_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _normalize_harness(value: str) -> str:
    raw = (value or "worker").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "claude": "claude_code",
        "claude_code": "claude_code",
        "cursor_agent": "cursor",
        "cursor_ide": "cursor",
        "vscode": "vscode_ide",
    }
    return aliases.get(raw, raw or "worker")


def _resolve_project_by_folder(folder: str) -> dict[str, Any] | None:
    raw = (folder or "").strip()
    if not raw:
        return None
    try:
        target = Path(raw).expanduser().resolve()
    except Exception:
        return None
    try:
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as db:
            projects = db.query(Project).all()
            best: tuple[int, Project] | None = None
            for project in projects:
                folder_location = (project.folder_location or "").strip()
                if not folder_location:
                    continue
                try:
                    candidate = Path(folder_location).expanduser().resolve()
                except Exception:
                    continue
                if target == candidate or candidate in target.parents:
                    score = len(str(candidate))
                    if best is None or score > best[0]:
                        best = (score, project)
            if not best:
                return None
            project = best[1]
            return {
                "id": int(project.id),
                "name": project.name or "",
                "folder_location": project.folder_location or "",
            }
    except Exception:
        return None


def _attachment(payload: HarnessEventPayload, resolved_project: dict[str, Any] | None) -> str:
    if payload.workflow_id or payload.run_id or payload.step_id:
        return "workflow"
    if payload.project_id or resolved_project:
        return "project"
    return "ambient"


def _should_notify(payload: HarnessEventPayload, attachment: str) -> bool:
    event_type = (payload.event_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    source = (payload.source or "").strip().lower().replace("-", "_").replace(" ", "_")
    if attachment == "ambient" or source == "ambient":
        return True
    return event_type in {
        "needs_input",
        "codex_needs_input",
        "codex_waiting",
        "codex_interrupted",
        "worker_failed",
        "codex_failed",
        "user_steer",
    }


def _notification_text(payload: HarnessEventPayload, attachment: str, project_name: str = "") -> str:
    label = {
        "codex": "Codex",
        "cursor": "Cursor",
        "claude_code": "Claude",
        "pi": "Pi",
        "vscode_ide": "VS Code",
    }.get(_normalize_harness(payload.harness), payload.harness.title() or "A harness")
    message = (payload.message or payload.output or payload.input or payload.event_type or "reported activity").strip()
    if attachment == "ambient":
        return f"{label} reported activity outside a tracked workflow" + (f": {message}" if message else ".")
    if attachment == "project":
        suffix = f" on {project_name}" if project_name else ""
        return f"{label} needs attention{suffix}: {message}"
    return f"{label} needs attention on a workflow: {message}"


def _record_learning(payload: HarnessEventPayload, attachment: str, project_id: int | None) -> None:
    text = (payload.message or payload.output or payload.input or "").strip()
    if not text:
        return
    try:
        record_learning_signal(
            scope="project" if project_id else "global",
            scope_id=project_id,
            rule_type="harness_event",
            summary=text[:500],
            payload={
                "harness": _normalize_harness(payload.harness),
                "event_type": payload.event_type,
                "attachment": attachment,
                "thread_id": payload.thread_id,
                "session_id": payload.session_id,
            },
        )
    except Exception:
        pass


def record_harness_event(payload: HarnessEventPayload) -> dict[str, Any]:
    """Record a harness event without requiring workflow context.

    This is intentionally best-effort. If DecisionsAI or Hermes storage is not
    available, callers should still be able to continue their local harness work.
    """
    harness = _normalize_harness(payload.harness)
    resolved = _resolve_project_by_folder(payload.project_folder)
    project_id = _as_int(payload.project_id) or _as_int((resolved or {}).get("id"))
    attachment = _attachment(payload, resolved)
    message = (payload.message or payload.output or payload.input or "").strip()
    event_payload = {
        **(payload.payload or {}),
        "source": payload.source or attachment,
        "attachment": attachment,
        "harness": harness,
        "project_folder": payload.project_folder,
        "thread_id": payload.thread_id,
        "session_id": payload.session_id,
        "input": payload.input,
        "output": payload.output,
    }
    try:
        event_id = emit_orchestration_event(
            source=harness,
            event_type=payload.event_type,
            status=payload.status,
            workflow_id=_as_int(payload.workflow_id),
            run_id=_as_int(payload.run_id),
            step_id=_as_int(payload.step_id),
            ticket_id=_as_int(payload.ticket_id),
            board_id=_as_int(payload.board_id),
            project_id=project_id,
            execution_session_id=_as_int(payload.execution_session_id),
            summary=message or f"{harness} event: {payload.event_type}",
            payload=event_payload,
            evidence=payload.evidence or {},
        )
    except Exception:
        event_id = None

    _record_learning(payload, attachment, project_id)

    event_type = (payload.event_type or "").strip().lower().replace("-", "_")
    if event_type.endswith("_completed") or event_type in {"worker_completed", "codex_completed", "cursor_completed"}:
        try:
            from distr.core.workspace_memory.feedback_sync import persist_worker_feedback

            persist_worker_feedback(
                message=(payload.message or payload.output or "").strip(),
                output=payload.output or "",
                input_text=payload.input or "",
                event_type=payload.event_type or "",
                source=_normalize_harness(payload.harness),
                ticket_id=_as_int(payload.ticket_id),
                project_id=project_id,
                board_id=_as_int(payload.board_id),
                workflow_id=_as_int(payload.workflow_id),
                linked_workflow_id=None,
                run_id=_as_int(payload.run_id),
                step_id=_as_int(payload.step_id),
                execution_session_id=_as_int(payload.execution_session_id),
                skip_steering_log=bool(_as_int(payload.run_id)),
            )
        except Exception:
            pass
    elif event_type in {"user_steer", "codex_needs_input", "cursor_needs_input", "codex_interrupted", "cursor_interrupted"}:
        try:
            from distr.core.workspace_memory.feedback_sync import persist_worker_feedback

            persist_worker_feedback(
                message=(payload.message or payload.input or "").strip(),
                input_text=payload.input or "",
                event_type=payload.event_type or "",
                source=_normalize_harness(payload.harness),
                ticket_id=_as_int(payload.ticket_id),
                project_id=project_id,
                board_id=_as_int(payload.board_id),
                workflow_id=_as_int(payload.workflow_id),
                run_id=_as_int(payload.run_id),
                step_id=_as_int(payload.step_id),
                execution_session_id=_as_int(payload.execution_session_id),
                skip_steering_log=bool(_as_int(payload.run_id)),
            )
        except Exception:
            pass

    notification_id = None
    if _should_notify(payload, attachment):
        try:
            notification_id = emit_user_notification(
                channel="telegram",
                text=_notification_text(payload, attachment, str((resolved or {}).get("name") or "")),
                workflow_id=_as_int(payload.workflow_id),
                run_id=_as_int(payload.run_id),
                step_id=_as_int(payload.step_id),
                ticket_id=_as_int(payload.ticket_id),
                board_id=_as_int(payload.board_id),
                project_id=project_id,
                execution_session_id=_as_int(payload.execution_session_id),
                payload={"harness_event_id": event_id, "attachment": attachment, "harness": harness},
            )
        except Exception:
            notification_id = None

    return {
        "success": True,
        "silent": False,
        "event_id": event_id,
        "notification_id": notification_id,
        "attachment": attachment,
        "project_id": project_id,
        "project": resolved,
    }


def record_harness_event_silently(payload: HarnessEventPayload) -> dict[str, Any]:
    try:
        return record_harness_event(payload)
    except Exception as exc:
        return {"success": True, "silent": True, "error": str(exc) if os.environ.get("DEBUG") == "TRUE" else ""}
