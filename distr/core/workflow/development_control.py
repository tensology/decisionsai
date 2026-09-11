"""Durable control-plane services for conversation-first Development work."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func

from distr.core.chat import _chat_params
from distr.core.chat_turns import redact_metadata, redact_text
from distr.core.db import Chat, Settings, get_session
from distr.core.db.projects import Project
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import DevelopmentCommand, DevelopmentWorkItem, StudioArtifact
from distr.core.workflow.development_threads import development_thread_metadata, is_development_thread


COMMAND_STATUSES = {"queued", "delivered", "completed", "cancelled", "failed"}


def _require_development_root(db, chat_id: int) -> Chat:
    root = db.get(Chat, int(chat_id))
    if root is None or root.parent_id is not None or not is_development_thread(db, root):
        raise LookupError("Development thread not found.")
    return root


def _json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _command_payload(row: DevelopmentCommand) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "chat_id": int(row.chat_id),
        "workflow_id": int(row.workflow_id) if row.workflow_id is not None else None,
        "run_id": int(row.run_id) if row.run_id is not None else None,
        "source": row.source or "web",
        "source_ref": row.source_ref or "",
        "command_type": row.command_type or "instruction",
        "content": row.content or "",
        "status": row.status or "queued",
        "position": int(row.position or 0),
        "result_summary": row.result_summary or "",
        "metadata": _json(row.metadata_json, {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "modified_at": row.modified_at.isoformat() if row.modified_at else None,
        "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
        "cancelled_at": row.cancelled_at.isoformat() if row.cancelled_at else None,
    }


def list_commands(chat_id: int, *, include_terminal: bool = True, limit: int = 50) -> list[dict[str, Any]]:
    with get_session() as db:
        _require_development_root(db, int(chat_id))
        query = db.query(DevelopmentCommand).filter(DevelopmentCommand.chat_id == int(chat_id))
        if not include_terminal:
            query = query.filter(DevelopmentCommand.status.in_(["queued", "delivered"]))
        rows = query.order_by(DevelopmentCommand.position.asc(), DevelopmentCommand.id.asc()).limit(max(1, min(limit, 200))).all()
        return [_command_payload(row) for row in rows]


def enqueue_command(
    chat_id: int,
    content: str,
    *,
    source: str = "web",
    source_ref: str = "",
    command_type: str = "instruction",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    instruction = redact_text(content, limit=12000, preserve_paths=True)
    if not instruction:
        raise ValueError("A development instruction is required.")
    with get_session() as db:
        _require_development_root(db, int(chat_id))
        position = int(
            db.query(func.coalesce(func.max(DevelopmentCommand.position), 0))
            .filter(DevelopmentCommand.chat_id == int(chat_id))
            .scalar()
            or 0
        ) + 1
        row = DevelopmentCommand(
            chat_id=int(chat_id),
            workflow_id=None,
            source=str(source or "web").strip().lower()[:40],
            source_ref=str(source_ref or "").strip()[:240] or None,
            command_type=str(command_type or "instruction").strip().lower()[:40],
            content=instruction,
            status="queued",
            position=position,
            metadata_json=json.dumps(redact_metadata(metadata or {}), ensure_ascii=False, default=str),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _command_payload(row)


def update_command(command_id: int, *, content: str | None = None, cancel: bool = False) -> dict[str, Any] | None:
    with get_session() as db:
        row = db.get(DevelopmentCommand, int(command_id))
        if row is None:
            return None
        if row.status != "queued":
            raise ValueError("Only queued instructions can be changed.")
        if cancel:
            row.status = "cancelled"
            row.cancelled_at = utc_now_naive()
            row.result_summary = "Cancelled before delivery."
        elif content is not None:
            clean = redact_text(content, limit=12000, preserve_paths=True)
            if not clean:
                raise ValueError("Instruction cannot be empty.")
            row.content = clean
        db.commit()
        db.refresh(row)
        return _command_payload(row)


def dispatch_command(command_id: int) -> dict[str, Any]:
    """Steer an active native Development turn, or keep guidance safely queued."""
    with get_session() as db:
        row = db.get(DevelopmentCommand, int(command_id))
        if row is None:
            return {"error": "Command not found", "status_code": 404}
        if row.status != "queued":
            return {"command": _command_payload(row), "dispatched": row.status == "delivered"}
        chat_id = int(row.chat_id)
        content = row.content
        metadata = _json(row.metadata_json, {})

    skill_ids = metadata.get("skill_ids") if isinstance(metadata, dict) else []
    clean_skill_ids = [str(skill_id).strip() for skill_id in skill_ids if str(skill_id).strip()] if isinstance(skill_ids, list) else []
    steering_content = content
    if clean_skill_ids:
        steering_content += (
            "\n\n[DECISIONS TURN SKILLS]\n"
            "The user selected these skills for this instruction: "
            + ", ".join(clean_skill_ids)
            + ". Read each exact skill with read_harness_skill before acting. Do not list or search the skill catalog first."
        )

    from distr.core.turn_runtime import steer_active_turn

    result = steer_active_turn(chat_id, steering_content)
    if not result.get("accepted"):
        return {
            "command": list_commands_for_id(command_id),
            "dispatched": False,
            "reason": result.get("reason") or "no_active_native_turn",
        }
    with get_session() as db:
        row = db.get(DevelopmentCommand, int(command_id))
        if row is None:
            return {"error": "Command not found", "status_code": 404}
        row.run_id = None
        row.status = "delivered"
        row.delivered_at = utc_now_naive()
        row.result_summary = "Applied to the active native Development turn."
        metadata = _json(row.metadata_json, {})
        metadata["delivery"] = redact_metadata(result)
        row.metadata_json = json.dumps(metadata, ensure_ascii=False, default=str)
        db.commit()
        db.refresh(row)
        return {"command": _command_payload(row), "dispatched": True, "delivery": redact_metadata(result)}


def list_commands_for_id(command_id: int) -> dict[str, Any] | None:
    with get_session() as db:
        row = db.get(DevelopmentCommand, int(command_id))
        return _command_payload(row) if row else None


def dispatch_pending(chat_id: int) -> dict[str, Any]:
    with get_session() as db:
        _require_development_root(db, chat_id)
        row = db.query(DevelopmentCommand).filter_by(chat_id=int(chat_id), status="queued").order_by(DevelopmentCommand.id).first()
        pending = [_command_payload(row)] if row else []
    if not pending:
        return {"status": "idle", "summary": "No queued instructions.", "next_actions": [], "artifacts": []}
    result = dispatch_command(int(pending[0]["id"]))
    return {
        "status": "delivered" if result.get("dispatched") else "queued",
        "summary": "Instruction delivered to the active worker." if result.get("dispatched") else "Instruction is safely queued until a worker can accept it.",
        "next_actions": ["edit", "cancel"] if not result.get("dispatched") else [],
        "artifacts": [result.get("command") or {}],
    }


def update_thread_controls(chat_id: int, **updates: Any) -> dict[str, Any]:
    allowed = {"pinned", "permission_profile", "remote_continuation", "shared_with_telegram"}
    with get_session() as db:
        chat = _require_development_root(db, chat_id)
        params = _chat_params(chat.params)
        metadata = dict(params.get("development") or {})
        for key, value in updates.items():
            if key in allowed and value is not None:
                metadata[key] = value
        if "project_id" in updates:
            project_id = updates.get("project_id")
            chat.project_id = int(project_id) if project_id is not None else None
            item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
            if item is not None:
                item.project_id = chat.project_id
        if "workflow_id" in updates:
            from distr.core.db.workflow import AutoWorkflow

            workflow_id = updates.get("workflow_id")
            if workflow_id is not None and db.get(AutoWorkflow, int(workflow_id)) is None:
                raise ValueError("Selected workflow does not exist.")
            item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
            if item is not None:
                item.workflow_id = int(workflow_id) if workflow_id is not None else None
            if workflow_id is None:
                metadata.pop("workflow_id", None)
            else:
                metadata["workflow_id"] = int(workflow_id)
        if updates.get("autonomy_level") is not None:
            autonomy = str(updates["autonomy_level"] or "full").strip().lower()
            if autonomy not in {"full", "approval", "plan", "goal"}:
                raise ValueError("Unknown autonomy level.")
            chat.autonomy_level = autonomy
        if updates.get("execution_profile") is not None:
            profile = str(updates["execution_profile"] or "code").strip().lower()
            if profile not in {"code", "design", "research", "operations"}:
                raise ValueError("Unknown execution profile.")
            chat.execution_profile = profile
        params["development"] = metadata
        chat.params = json.dumps(params, ensure_ascii=False, default=str)
        chat.modified_date = utc_now_naive()
        db.commit()
        return {
            **metadata,
            "chat_id": int(chat.id),
            "project_id": chat.project_id,
            "workflow_id": metadata.get("workflow_id"),
            "autonomy_level": chat.autonomy_level or "full",
            "execution_profile": chat.execution_profile or "code",
        }


def _thread_time_payload(item: DevelopmentWorkItem, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now_naive()
    seconds = max(0, int(item.time_accumulated_seconds or 0))
    if not item.time_paused and item.time_started_at:
        seconds += max(0, int((now - item.time_started_at).total_seconds()))
    return {
        "chat_id": int(item.chat_id),
        "seconds": seconds,
        "accumulated_seconds": max(0, int(item.time_accumulated_seconds or 0)),
        "paused": bool(item.time_paused),
        "running": not bool(item.time_paused),
        "started_at": item.time_started_at.isoformat() if item.time_started_at else None,
        "last_activity_at": item.time_last_activity_at.isoformat() if item.time_last_activity_at else None,
    }


def _sync_ticket_time(db, item: DevelopmentWorkItem, seconds: int) -> None:
    if item.local_ticket_id is None:
        return
    from distr.core.db.kanban import KanbanTicket
    from distr.core.kanban.ticket_time_tracking import format_time_tracking_seconds

    ticket = db.get(KanbanTicket, int(item.local_ticket_id))
    if ticket is not None:
        ticket.time_spent = format_time_tracking_seconds(max(0, int(seconds))) if seconds else ""


def thread_time_state(chat_id: int, *, auto_pause_after_seconds: int = 180) -> dict[str, Any]:
    """Return thread-owned time and apply the inactivity auto-pause rule."""
    from distr.core.workflow.development_harness import development_execution_active

    agent_active = development_execution_active(int(chat_id))
    now = utc_now_naive()
    with get_session() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
        if item is None:
            raise LookupError("Development thread not found.")
        if not item.time_paused and item.time_started_at:
            latest_response = db.query(Chat).filter(
                Chat.parent_id == int(chat_id),
                Chat.response.isnot(None),
            ).order_by(Chat.modified_date.desc(), Chat.id.desc()).first()
            response_at = latest_response.modified_date if latest_response else None
            # The timer needs a deadline even when a worker never produced a response.
            activity_at = max(value for value in (
                item.time_started_at, item.time_last_activity_at, response_at
            ) if value is not None)
            if not agent_active and (now - activity_at).total_seconds() >= auto_pause_after_seconds:
                pause_at = activity_at + timedelta(seconds=auto_pause_after_seconds)
                item.time_accumulated_seconds = _thread_time_payload(item, pause_at)["seconds"]
                item.time_started_at = None
                item.time_paused = True
        payload = _thread_time_payload(item, now)
        _sync_ticket_time(db, item, payload["seconds"])
        db.commit()
        return payload


def resume_thread_time(chat_id: int, *, _session_provider=None) -> dict[str, Any]:
    now = utc_now_naive()
    with (_session_provider or get_session)() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
        if item is None:
            raise LookupError("Development thread not found.")
        if item.time_paused or not item.time_started_at:
            item.time_started_at = now
        item.time_paused = False
        item.time_last_activity_at = now
        payload = _thread_time_payload(item, now)
        _sync_ticket_time(db, item, payload["seconds"])
        db.commit()
        return payload


def pause_thread_time(chat_id: int) -> dict[str, Any]:
    now = utc_now_naive()
    with get_session() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
        if item is None:
            raise LookupError("Development thread not found.")
        if not item.time_paused and item.time_started_at:
            item.time_accumulated_seconds = _thread_time_payload(item, now)["seconds"]
        item.time_started_at = None
        item.time_paused = True
        payload = _thread_time_payload(item, now)
        _sync_ticket_time(db, item, payload["seconds"])
        db.commit()
        return payload


def reset_thread_time(chat_id: int, *, seconds: int = 0) -> dict[str, Any]:
    now = utc_now_naive()
    with get_session() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
        if item is None:
            raise LookupError("Development thread not found.")
        item.time_accumulated_seconds = max(0, int(seconds or 0))
        item.time_started_at = now if not item.time_paused else None
        item.time_last_activity_at = now
        payload = _thread_time_payload(item, now)
        _sync_ticket_time(db, item, payload["seconds"])
        db.commit()
        return payload


def clear_thread_context(chat_id: int) -> dict[str, Any]:
    """Create a hidden context checkpoint without changing the transcript."""
    with get_session() as db:
        root = _require_development_root(db, chat_id)
        execution = development_thread_metadata(root).get("execution")
        if isinstance(execution, dict) and str(execution.get("status") or "").lower() in {
            "initializing",
            "queued",
            "running",
            "waiting",
        }:
            raise ValueError("Stop the active agent or wait for it to finish before clearing context.")
        rows = db.query(Chat).filter(Chat.parent_id == int(chat_id)).order_by(Chat.created_date, Chat.id).all()
        messages = []
        for row in rows:
            if row.input and not row.is_hidden:
                messages.append(("user", row.input))
            if row.response and not row.is_hidden:
                messages.append(("assistant", row.response))
        recent = messages[-16:]
        summary = "Conversation checkpoint:\n" + "\n".join(
            f"- {role}: {' '.join(str(content).split())[:500]}" for role, content in recent
        )
        if not recent:
            summary += "\n- No prior visible conversation content."
        try:
            context = json.loads(root.additional_context or "{}")
        except (TypeError, ValueError):
            context = {}
        boundary_id = max((int(row.id) for row in rows), default=int(root.id))
        context["compact_checkpoint"] = {
            "active": True,
            "summary": summary,
            "summary_source": "development_clear",
            "chat_row_id": boundary_id,
            "message_count": len(messages),
            "created_at": utc_now_naive().isoformat(),
            "reason": "development_clear",
        }
        root.additional_context = json.dumps(context, ensure_ascii=False)
        params = _chat_params(root.params)
        metadata = dict(params.get("development") or {})
        metadata["context_cleared_at"] = utc_now_naive().isoformat()
        metadata["context_boundary_chat_row_id"] = boundary_id
        params["development"] = metadata
        root.params = json.dumps(params, ensure_ascii=False)
        db.commit()
        return {"chat_id": int(chat_id), "cleared": True, "boundary_chat_row_id": boundary_id}


def fork_thread(chat_id: int, *, title: str | None = None) -> dict[str, Any]:
    """Fork a Development transcript without activating the Chat agent.

    The fork deliberately keeps project and execution preferences while
    dropping ticket ownership. A ticket is a single work identity and cannot
    silently become owned by two Development threads.
    """
    checkpoint = clear_thread_context(int(chat_id))
    now = utc_now_naive()
    with get_session() as db:
        source = _require_development_root(db, chat_id)
        source_item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
        source_title = str(source.title or f"Development #{chat_id}").strip()
        fork = Chat(
            parent_id=None,
            title=str(title or f"{source_title} fork").strip(),
            provider=source.provider,
            model_name=source.model_name,
            voice_provider=source.voice_provider,
            voice_model=source.voice_model,
            project_id=source.project_id,
            route_mode=source.route_mode,
            execution_profile=source.execution_profile,
            autonomy_level=source.autonomy_level,
            created_date=now,
            modified_date=now,
        )
        db.add(fork)
        db.flush()
        source_context = _json(source.additional_context, {})
        source_checkpoint = source_context.get("compact_checkpoint")
        fork.additional_context = json.dumps(
            {
                "compact_checkpoint": {
                    **(source_checkpoint if isinstance(source_checkpoint, dict) else {}),
                    "source_chat_id": int(chat_id),
                    "fork_seed": True,
                }
            },
            ensure_ascii=False,
            default=str,
        )
        source_metadata = development_thread_metadata(source)
        copied_metadata = {
            key: value
            for key, value in source_metadata.items()
            if key
            not in {
                "ticket_id",
                "board_key",
                "board_provider",
                "board_ticket_key",
                "board_ticket_title",
                "board_ticket_lane",
                "execution",
                "active_run_id",
            }
        }
        copied_metadata.update(
            {
                "source_type": "fork",
                "source_ref": str(chat_id),
                "forked_from_chat_id": int(chat_id),
                "pinned": False,
            }
        )
        fork.params = json.dumps({"development": copied_metadata}, ensure_ascii=False, default=str)
        db.add(
            DevelopmentWorkItem(
                chat_id=int(fork.id),
                identity_key=f"chat:{int(fork.id)}",
                source_type="fork",
                project_id=(
                    int(source_item.project_id)
                    if source_item is not None and source_item.project_id is not None
                    else source.project_id
                ),
                workflow_id=(
                    int(source_item.workflow_id)
                    if source_item is not None and source_item.workflow_id is not None
                    else None
                ),
                ticket_title=fork.title,
                time_paused=True,
                created_at=now,
                modified_at=now,
            )
        )
        db.commit()
        return {
            "ok": True,
            "id": int(fork.id),
            "source_chat_id": int(chat_id),
            "checkpoint": checkpoint,
        }


def delete_thread(
    chat_id: int,
    *,
    delete_linked_ticket: bool = True,
) -> dict[str, Any]:
    """Delete one Development aggregate through its authoritative lifecycle."""
    from distr.core.chat import cleanup_chat_dependencies, remove_chat_transcript_audit_events

    with get_session() as db:
        root = _require_development_root(db, chat_id)
        counts = cleanup_chat_dependencies(
            db,
            int(chat_id),
            delete_owned_ticket=bool(delete_linked_ticket),
            delete_owned_workflows=True,
        )
        settings = db.query(Settings).first()
        if settings is not None:
            if settings.last_chat_id == int(chat_id):
                settings.last_chat_id = None
            if settings.agent_current_chat_id == int(chat_id):
                settings.agent_current_chat_id = None
        db.delete(root)
        db.commit()
    remove_chat_transcript_audit_events(int(chat_id))
    return {
        "deleted": True,
        "chat_id": int(chat_id),
        "deleted_linked_ticket": bool(delete_linked_ticket and counts.get("tickets")),
        "counts": counts,
    }


def archive_thread(chat_id: int, *, archived: bool) -> dict[str, Any]:
    with get_session() as db:
        chat = _require_development_root(db, int(chat_id))
        chat.is_archived = bool(archived)
        chat.modified_date = utc_now_naive()
        db.commit()
        return {"chat_id": int(chat_id), "archived": bool(chat.is_archived)}


def thread_export(chat_id: int, *, redacted: bool = True) -> dict[str, Any]:
    from distr.core.workflow.studio_artifacts import list_studio_artifacts

    with get_session() as db:
        root = _require_development_root(db, int(chat_id))
        rows = [root] + db.query(Chat).filter(Chat.parent_id == int(chat_id)).order_by(Chat.created_date.asc(), Chat.id.asc()).all()
        messages = []
        for row in rows:
            if row.input:
                messages.append({"role": "user", "content": row.input, "at": row.created_date.isoformat() if row.created_date else None})
            if row.response:
                messages.append({"role": "assistant", "content": row.response, "at": row.modified_date.isoformat() if row.modified_date else None})
        payload = {
            "schema": "decisions-development-thread/v1",
            "thread": {
                "id": int(root.id),
                "title": root.title or "Development",
                "project_id": root.project_id,
                "route_mode": root.route_mode or "auto",
                "execution_profile": root.execution_profile or "code",
                "autonomy_level": root.autonomy_level or "full",
                "development": development_thread_metadata(root),
            },
            "messages": messages,
            "commands": list_commands(int(chat_id)),
            "artifacts": list_studio_artifacts(int(chat_id)),
            "exported_at": utc_now_naive().isoformat(),
        }
    return redact_metadata(payload) if redacted else payload


def create_shared_snapshot(chat_id: int) -> dict[str, Any]:
    payload = thread_export(chat_id, redacted=True)
    token = secrets.token_urlsafe(24)
    with get_session() as db:
        row = StudioArtifact(
            chat_id=int(chat_id),
            workflow_id=None,
            artifact_type="shared_snapshot",
            title="Redacted thread snapshot",
            summary="Read-only development context safe to share.",
            content=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            content_format="json",
            uri=f"snapshot:{token}",
            status="ready",
            sort_order=900,
            metadata_json=json.dumps({"share_token": token, "redacted": True}, ensure_ascii=False),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"token": token, "artifact_id": int(row.id), "snapshot": payload}


def get_shared_snapshot(token: str) -> dict[str, Any] | None:
    with get_session() as db:
        row = (
            db.query(StudioArtifact)
            .filter(
                StudioArtifact.artifact_type == "shared_snapshot",
                StudioArtifact.uri == f"snapshot:{token}",
            )
            .order_by(StudioArtifact.id.desc())
            .first()
        )
        if row:
            metadata = _json(row.metadata_json, {})
            if secrets.compare_digest(str(metadata.get("share_token") or ""), str(token or "")):
                return _json(row.content, {})
    return None


def capture_execution_skill(chat_id: int, execution_session_id: int, *, name: str = "") -> dict[str, Any]:
    """Save a successful direct Development turn as a project-local skill."""
    from distr.core.db.kanban import ProjectExecutionSession

    with get_session() as db:
        chat = _require_development_root(db, int(chat_id))
        execution = db.get(ProjectExecutionSession, int(execution_session_id))
        project = db.get(Project, int(chat.project_id)) if chat and chat.project_id else None
        if chat is None or project is None or execution is None:
            raise LookupError("A linked project and Development execution are required.")
        input_packet = _json(execution.input_packet, {})
        if (
            execution.route_type != "native_turn"
            or int(execution.project_id) != int(project.id)
            or int(input_packet.get("chat_id") or 0) != int(chat_id)
        ):
            raise LookupError("This Development execution does not belong to the thread.")
        if execution.status != "completed":
            raise ValueError("Only completed Development turns can become reusable skills.")
        root = Path(project.folder_location or "").expanduser().resolve()
        if not root.is_dir():
            raise ValueError("The linked project folder is unavailable.")
        output_packet = _json(execution.output_packet, {})
        title = name.strip() or f"{chat.title or 'Development'} approach"
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:64] or f"turn-{execution_session_id}"
        target = (root / ".decisions" / "skills" / slug).resolve()
        if root not in target.parents:
            raise ValueError("Skill path escaped the linked project.")
        target.mkdir(parents=True, exist_ok=True)
        route = {
            "runtime": output_packet.get("runtime_id") or "native",
            "provider": execution.route_backend,
            "model": execution.selected_model,
            "complexity": execution.complexity,
        }
        approach = {
            "request": input_packet.get("prompt") or "",
            "outcome": output_packet.get("output") or "",
            "artifacts": output_packet.get("artifacts") or [],
        }
        content = "\n".join([
            "---",
            f"name: {slug}",
            f"description: Reuse the verified approach from DecisionsAI Development turn {execution_session_id}.",
            "---",
            "",
            f"# {title}",
            "",
            "Use this approach when a task matches the outcome and constraints recorded below.",
            "",
            "## Proven approach",
            "",
            redact_text(json.dumps(approach, ensure_ascii=False, indent=2), limit=16000, preserve_paths=False),
            "",
            "## Route used",
            "",
            redact_text(json.dumps(route, ensure_ascii=False, indent=2), limit=4000, preserve_paths=False),
            "",
            "## Verification evidence",
            "",
            redact_text(json.dumps(output_packet.get("evidence") or {}, ensure_ascii=False, indent=2), limit=16000, preserve_paths=False),
            "",
        ])
        skill_file = target / "SKILL.md"
        skill_file.write_text(content, encoding="utf-8")
        return {"status": "created", "summary": f"Saved reusable skill {slug}.", "next_actions": ["review_skill"], "artifacts": [{"path": str(skill_file), "name": slug}]}
