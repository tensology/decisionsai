"""IDE-first project chat bridge for Cursor/Codex plugin sessions."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from distr.core.chat import ChatService
from distr.core.db import get_session
from distr.core.db.kanban import ProjectExecutionSession
from distr.core.db.projects import Project
from distr.core.kanban.project_execution import (
    append_execution_event,
    create_execution_session,
    serialize_execution_session,
)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _canonical_folder(value: str) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except Exception:
        return str(Path(raw).expanduser())


def _event_status(event_type: str, status: str = "") -> str:
    low = _clean(event_type).lower().replace("-", "_").replace(" ", "_")
    explicit = _clean(status).lower()
    if explicit:
        return explicit
    if low.endswith("_completed") or low in {"completed", "response_completed"}:
        return "completed"
    if low.endswith("_failed") or low in {"failed", "response_failed"}:
        return "failed"
    if "needs_input" in low or low.endswith("_waiting"):
        return "waiting"
    return "running"


def find_project_for_folder(cwd: str, project_id: int | None = None) -> dict[str, Any] | None:
    """Resolve a cwd to the most specific Decisions project."""

    with get_session() as session:
        if project_id:
            project = session.query(Project).filter(Project.id == int(project_id)).first()
            return _project_dict(project) if project else None

        folder = _canonical_folder(cwd)
        if not folder:
            return None
        candidates: list[tuple[int, Project]] = []
        for project in session.query(Project).all():
            project_folder = _canonical_folder(project.folder_location or "")
            if not project_folder:
                continue
            if folder == project_folder or folder.startswith(project_folder.rstrip("/") + "/"):
                candidates.append((len(project_folder), project))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return _project_dict(candidates[0][1])


def _project_dict(project: Project | None) -> dict[str, Any] | None:
    if not project:
        return None
    return {
        "id": int(project.id),
        "name": project.name or "",
        "folder_location": project.folder_location or "",
        "coding_backend": project.coding_backend or "",
        "coding_backend_model": project.coding_backend_model or "",
    }


def _latest_open_session(project_id: int, source: str) -> dict[str, Any] | None:
    with get_session() as session:
        row = (
            session.query(ProjectExecutionSession)
            .filter(ProjectExecutionSession.project_id == int(project_id))
            .filter(ProjectExecutionSession.route_type == "ide_bridge")
            .filter(ProjectExecutionSession.route_backend == source)
            .filter(ProjectExecutionSession.status.notin_(TERMINAL_STATUSES))
            .order_by(ProjectExecutionSession.updated_at.desc(), ProjectExecutionSession.started_at.desc())
            .first()
        )
        return serialize_execution_session(row, include_events=True) if row else None


def _session_chat_id(session_data: dict[str, Any] | None) -> int | None:
    if not session_data:
        return None
    packet = session_data.get("input_packet") if isinstance(session_data.get("input_packet"), dict) else {}
    chat_id = packet.get("chat_id")
    return int(chat_id) if str(chat_id or "").isdigit() else None


def _loads_packet(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _assert_session_matches_request(
    row: ProjectExecutionSession,
    *,
    project: dict[str, Any],
    source: str,
    cwd: str = "",
) -> None:
    problems: list[str] = []
    if int(row.project_id or 0) != int(project["id"]):
        problems.append(f"project #{row.project_id} != #{project['id']}")
    if (row.route_type or "") != "ide_bridge":
        problems.append(f"route_type {row.route_type!r} is not ide_bridge")
    if (row.route_backend or "").strip().lower() != source:
        problems.append(f"source {(row.route_backend or '').strip().lower()!r} != {source!r}")

    packet = _loads_packet(row.input_packet)
    requested_cwd = _canonical_folder(cwd)
    session_folder = _canonical_folder(str(packet.get("folder") or project.get("folder_location") or ""))
    if requested_cwd and session_folder:
        if requested_cwd != session_folder and not requested_cwd.startswith(session_folder.rstrip("/") + "/"):
            problems.append(f"cwd {requested_cwd!r} is outside session folder {session_folder!r}")

    if problems:
        raise ValueError("IDE session does not match this request: " + "; ".join(problems))


def ensure_ide_session(
    *,
    source: str,
    cwd: str = "",
    project_id: int | None = None,
    session_id: int | None = None,
    chat_id: int | None = None,
    allow_chat_creation: bool = True,
) -> dict[str, Any]:
    """Create or resume a Decisions project session for an IDE conversation."""

    source = (_clean(source) or "ide").lower()
    project = find_project_for_folder(cwd, project_id=project_id)
    if not project:
        raise ValueError("No Decisions project matched this IDE folder.")

    if session_id:
        with get_session() as db:
            row = db.query(ProjectExecutionSession).filter(ProjectExecutionSession.id == int(session_id)).first()
            if row:
                _assert_session_matches_request(row, project=project, source=source, cwd=cwd)
                data = serialize_execution_session(row, include_events=True)
                return {"project": project, "session": data, "chat_id": _session_chat_id(data)}

    existing = _latest_open_session(int(project["id"]), source)
    if existing:
        return {"project": project, "session": existing, "chat_id": _session_chat_id(existing)}

    if not chat_id and not allow_chat_creation:
        chat_id = ChatService.get_current_chat_id()

    if not chat_id and allow_chat_creation:
        chat_id, _ = ChatService.create_new_chat(
            title=f"{source.title()} IDE: {project['name'] or 'Project'}",
        )

    input_chat_id = int(chat_id) if str(chat_id or "").isdigit() else None
    new_session_id = create_execution_session(
        project_id=int(project["id"]),
        route_type="ide_bridge",
        route_backend=source,
        selected_model="ide",
        selection_reason="IDE plugin project session",
        origin="ide",
        input_packet={
            "project_id": project["id"],
            "project_name": project["name"],
            "folder": project["folder_location"],
            "cwd": _canonical_folder(cwd),
            "chat_id": input_chat_id,
            "source": source,
        },
    )
    append_execution_event(
        new_session_id,
        "ide_session_started",
        status="running",
        message=f"{source.title()} IDE project chat started.",
        payload={
            "chat_id": input_chat_id,
            "project": project,
            "surface": source,
            "subtype": "ide_session_started",
            "thread_id": str(input_chat_id or ""),
            "is_workflow_attached": False,
        },
    )
    with get_session() as db:
        row = db.query(ProjectExecutionSession).filter(ProjectExecutionSession.id == int(new_session_id)).first()
        data = serialize_execution_session(row, include_events=True)
    return {"project": project, "session": data, "chat_id": input_chat_id}


def record_ide_event(
    *,
    source: str,
    event_type: str,
    message: str = "",
    input_text: str = "",
    output_text: str = "",
    status: str = "",
    cwd: str = "",
    project_id: int | None = None,
    session_id: int | None = None,
    chat_id: int | None = None,
    payload: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    allow_chat_creation: bool = True,
) -> dict[str, Any]:
    bridge = ensure_ide_session(
        source=source,
        cwd=cwd,
        project_id=project_id,
        session_id=session_id,
        chat_id=chat_id,
        allow_chat_creation=allow_chat_creation,
    )
    session_data = bridge["session"]
    session_id = int(session_data["id"])
    chat_id = bridge.get("chat_id")
    event_type = _clean(event_type) or f"{source}_event"
    resolved_status = _event_status(event_type, status)
    body = _clean(message or output_text or input_text)

    external_thread_id = ""
    if isinstance(payload, dict):
        nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        external_thread_id = str(
            payload.get("external_thread_id")
            or payload.get("thread_id")
            or nested.get("external_thread_id")
            or nested.get("thread_id")
            or ""
        ).strip()

    append_execution_event(
        session_id,
        event_type,
        status=resolved_status,
        message=body,
        payload={
            "source": source,
            "surface": source,
            "subtype": event_type,
            "correlation_id": (payload or {}).get("correlation_id") or f"ide:{source}:{session_id}",
            "thread_id": external_thread_id or str(chat_id or ""),
            "external_thread_id": external_thread_id,
            "is_workflow_attached": bool(
                session_data.get("workflow_id") or session_data.get("run_id") or session_data.get("step_id")
            ),
            "input": input_text,
            "output": output_text,
            "payload": payload or {},
            "evidence": evidence or {},
        },
    )

    low = event_type.lower().replace("-", "_").replace(" ", "_")
    if chat_id and (input_text or low.endswith("_prompt_submitted") or low == "user_steer"):
        ChatService.add_user_message(int(chat_id), f"[{source.title()} IDE] {input_text or message}")
    if chat_id and (output_text or low.endswith("_completed") or low.endswith("_failed")):
        ChatService.append_assistant_notice(int(chat_id), f"[{source.title()} IDE] {output_text or message}")

    with get_session() as db:
        row = db.query(ProjectExecutionSession).filter(ProjectExecutionSession.id == int(session_id)).first()
        if row:
            if external_thread_id:
                packet = _loads_packet(row.input_packet)
                packet["external_thread_id"] = external_thread_id
                row.input_packet = json.dumps(packet, ensure_ascii=False)
            if resolved_status in TERMINAL_STATUSES:
                row.status = resolved_status
            db.commit()
            db.refresh(row)
        latest = serialize_execution_session(row, include_events=True) if row else session_data

    try:
        from distr.core.workspace_memory.feedback_sync import persist_worker_feedback

        ticket_id = int(session_data.get("ticket_id")) if str(session_data.get("ticket_id") or "").isdigit() else None
        project_id_val = int(session_data.get("project_id")) if str(session_data.get("project_id") or "").isdigit() else None
        if not project_id_val and bridge.get("project"):
            project_id_val = int(bridge["project"].get("id") or 0) or None
        persist_worker_feedback(
            message=body,
            output=output_text,
            input_text=input_text,
            event_type=event_type,
            source=source,
            ticket_id=ticket_id,
            project_id=project_id_val,
            workflow_id=int(session_data.get("workflow_id")) if str(session_data.get("workflow_id") or "").isdigit() else None,
            run_id=int(session_data.get("run_id")) if str(session_data.get("run_id") or "").isdigit() else None,
            step_id=int(session_data.get("step_id")) if str(session_data.get("step_id") or "").isdigit() else None,
            execution_session_id=session_id,
            skip_steering_log=bool(session_data.get("run_id")),
        )
    except Exception:
        pass

    return {"project": bridge["project"], "session": latest, "chat_id": chat_id}


def get_ide_progress(*, source: str = "", cwd: str = "", project_id: int | None = None, session_id: int | None = None) -> dict[str, Any]:
    source = (_clean(source) or "").lower()
    if session_id:
        with get_session() as db:
            row = db.query(ProjectExecutionSession).filter(ProjectExecutionSession.id == int(session_id)).first()
            if not row:
                raise ValueError("IDE session not found.")
            return {"project": find_project_for_folder("", project_id=row.project_id), "session": serialize_execution_session(row, include_events=True)}
    project = find_project_for_folder(cwd, project_id=project_id)
    if not project:
        raise ValueError("No Decisions project matched this IDE folder.")
    with get_session() as db:
        query = (
            db.query(ProjectExecutionSession)
            .filter(ProjectExecutionSession.project_id == int(project["id"]))
            .filter(ProjectExecutionSession.route_type == "ide_bridge")
        )
        if source:
            query = query.filter(ProjectExecutionSession.route_backend == source)
        row = query.order_by(ProjectExecutionSession.updated_at.desc(), ProjectExecutionSession.started_at.desc()).first()
        return {"project": project, "session": serialize_execution_session(row, include_events=True) if row else None}


def list_ide_sessions(
    *,
    source: str = "",
    project_id: int | None = None,
    cwd: str = "",
    limit: int = 12,
    include_terminal: bool = True,
) -> list[dict[str, Any]]:
    """List Decisions-owned IDE bridge sessions for Codex/Cursor."""
    from distr.core.db.kanban import ProjectExecutionSession
    from distr.core.db.projects import Project

    source = (_clean(source) or "").lower()
    limit = max(1, min(int(limit or 12), 30))
    project = find_project_for_folder(cwd, project_id=project_id)

    with get_session() as db:
        query = db.query(ProjectExecutionSession, Project).outerjoin(
            Project, Project.id == ProjectExecutionSession.project_id
        ).filter(ProjectExecutionSession.route_type == "ide_bridge")
        if source:
            query = query.filter(ProjectExecutionSession.route_backend == source)
        if project:
            query = query.filter(ProjectExecutionSession.project_id == int(project["id"]))
        if not include_terminal:
            query = query.filter(ProjectExecutionSession.status.notin_(TERMINAL_STATUSES))
        rows = (
            query.order_by(ProjectExecutionSession.updated_at.desc(), ProjectExecutionSession.started_at.desc())
            .limit(limit)
            .all()
        )

        items: list[dict[str, Any]] = []
        for row, proj in rows:
            packet = _loads_packet(row.input_packet)
            items.append(
                {
                    "id": row.id,
                    "status": row.status or "",
                    "backend": row.route_backend or "",
                    "project_id": row.project_id,
                    "project_name": getattr(proj, "name", "") if proj else "",
                    "folder": packet.get("folder") or getattr(proj, "folder_location", "") if proj else "",
                    "external_thread_id": packet.get("external_thread_id") or "",
                    "title": packet.get("title") or f"{(row.route_backend or 'ide').title()} session #{row.id}",
                    "updated_at": row.updated_at.isoformat() if row.updated_at else "",
                    "started_at": row.started_at.isoformat() if row.started_at else "",
                }
            )
        return items
