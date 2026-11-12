"""Durable control-plane services for conversation-first Development work."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import and_, case, func, or_

from distr.core.chat import _chat_params
from distr.core.chat_turns import redact_metadata, redact_text
from distr.core.db import Chat, Settings, get_session
from distr.core.db.projects import Project
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import DevelopmentCommand, DevelopmentTimeEntry, DevelopmentWorkItem, StudioArtifact
from distr.core.workflow.development_threads import (
    DevelopmentThreadConflict, development_scope_identity, development_thread_metadata,
    development_thread_receipt_identity, is_development_thread,
)


COMMAND_STATUSES = {"queued", "editing", "dispatching", "delivered", "completed", "cancelled", "failed"}


class FollowupThreadIdentityConflict(DevelopmentThreadConflict):
    """The desktop no longer has the thread reviewed by the App View submission."""


# Reject syncing absurd thread totals into tickets (e.g. cancelled workflow ghosts).
MAX_TICKET_TIME_SYNC_SECONDS = 12 * 60 * 60
DEVELOPMENT_TIMER_MENU = {
    "heading": "Timer",
    "items": (
        {
            "key": "toggle",
            "element_id": "thread-context-toggle-time",
            "running_label": "Stop timer",
            "stopped_label": "Start timer",
            "running_action": "pause",
            "stopped_action": "play",
        },
        {
            "key": "reset",
            "element_id": "thread-context-reset-time",
            "running_label": "Stop and reset timer",
            "stopped_label": "Stop and reset timer",
            "running_action": "reset",
            "stopped_action": "reset",
        },
    ),
}


def development_timer_menu_items(running: bool) -> list[dict[str, str]]:
    state = "running" if running else "stopped"
    return [
        {
            "key": str(item["key"]),
            "label": str(item[f"{state}_label"]),
            "action": str(item[f"{state}_action"]),
        }
        for item in DEVELOPMENT_TIMER_MENU["items"]
    ]


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


def _command_payload(row: DevelopmentCommand, *, root: Chat | None = None) -> dict[str, Any]:
    metadata = _json(row.metadata_json, {})
    if isinstance(metadata, dict):
        metadata.pop("app_view_submission_digest", None)
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
        "metadata": metadata,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "modified_at": row.modified_at.isoformat() if row.modified_at else None,
        "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
        "cancelled_at": row.cancelled_at.isoformat() if row.cancelled_at else None,
        **({"scope_status": command_scope_status(row, root)}
           if root is not None and row.status in {"queued", "editing", "dispatching"} else {}),
    }


def _next_command_position(db, chat_id: int) -> int:
    return int(db.query(func.coalesce(func.max(DevelopmentCommand.position), 0))
               .filter(DevelopmentCommand.chat_id == chat_id).scalar() or 0) + 1


def _queue_scope(root: Chat) -> tuple:
    return development_scope_identity(root.project_id, development_thread_metadata(root), chat_id=root.id)


def command_scope_status(command: DevelopmentCommand, root: Chat) -> str:
    metadata = _json(command.metadata_json, {})
    if not isinstance(metadata, dict):
        return "unknown"
    saved = metadata.get("queued_scope", metadata.get("app_view_scope") if command.source == "app_view" else None)
    if (not isinstance(saved, list) or len(saved) != 5 or type(saved[0]) is not int
            or any(type(saved[index]) is not str for index in (1, 2, 4))
            or (saved[3] is not None and type(saved[3]) is not int)):
        return "unknown"
    return "current" if saved == list(_queue_scope(root)) else "earlier"


def _ordered_pending_commands(db, root: Chat, *, include_held: bool = False):
    scope = _queue_scope(root)
    metadata = case(
        (func.json_valid(DevelopmentCommand.metadata_json) == 1, DevelopmentCommand.metadata_json),
        else_="{}",
    )
    saved_scope = case(
        (func.json_type(metadata, "$.queued_scope").is_not(None), "$.queued_scope"),
        (DevelopmentCommand.source == "app_view", "$.app_view_scope"),
        else_="$.queued_scope",
    )
    scope_types = ("integer", "text", "text", "null" if scope[3] is None else "integer", "text")
    matches = [func.json_array_length(metadata, saved_scope) == len(scope)]
    for index, (value, kind) in enumerate(zip(scope, scope_types)):
        path = saved_scope + f"[{index}]"
        matches.append(func.json_type(metadata, path) == kind)
        matches.append(func.json_extract(metadata, path).is_not_distinct_from(value))
    eligible = and_(*matches)
    query = db.query(DevelopmentCommand).filter(
        DevelopmentCommand.chat_id == root.id,
        DevelopmentCommand.status.in_(["queued", "editing", "dispatching"]),
        or_(DevelopmentCommand.source.is_(None), DevelopmentCommand.source != "app_view_steer"),
    )
    if include_held:
        query = query.order_by(case((eligible, 0), else_=1))
    else:
        query = query.filter(eligible)
    return query.order_by(DevelopmentCommand.position.asc(), DevelopmentCommand.id.asc())


def list_commands(chat_id: int, *, include_terminal: bool = True, limit: int = 50) -> list[dict[str, Any]]:
    from .queued_submissions import recover_manual_starts

    with get_session() as db:
        root = _require_development_root(db, int(chat_id))
        recover_manual_starts(db, root.id)
        if include_terminal:
            query = db.query(DevelopmentCommand).filter(DevelopmentCommand.chat_id == root.id).order_by(
                DevelopmentCommand.position.asc(), DevelopmentCommand.id.asc(),
            )
        else:
            query = _ordered_pending_commands(db, root, include_held=True)
        rows = query.limit(max(1, min(limit, 200))).all()
        return [_command_payload(row, root=root) for row in rows]


def app_view_followup_receipt(chat_id: int, submission_id: str) -> dict[str, Any] | None:
    with get_session() as db:
        root = _require_development_root(db, chat_id)
        row = db.query(DevelopmentCommand).filter_by(
            chat_id=root.id, source="app_view", source_ref=submission_id, command_type="followup",
        ).first()
        if row is None:
            return None
        return {"id": row.id, "chat_id": row.chat_id, "source_ref": row.source_ref, "status": row.status}


def is_duplicate_instruction(chat_id: int, content: str, expected_scope: tuple | None = None,
                             *, window_sec: float | None = None) -> bool:
    """Identical instruction already pending for this thread within a few seconds:
    either the latest unanswered user row, or a queued command just created."""
    from datetime import datetime, timezone

    from distr.core.chat import DUPLICATE_INPUT_WINDOW_SEC, _as_naive_utc, is_duplicate_pending_input

    window = DUPLICATE_INPUT_WINDOW_SEC if window_sec is None else float(window_sec)
    cleaned = str(content or "").strip()
    if not cleaned:
        return False
    if expected_scope is not None:
        with get_session() as db:
            if _queue_scope(_require_development_root(db, int(chat_id))) != expected_scope:
                return False
    if is_duplicate_pending_input(int(chat_id), cleaned, window_sec=window):
        return True
    instruction = redact_text(cleaned, limit=12000, preserve_paths=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with get_session() as db:
        root = _require_development_root(db, int(chat_id))
        rows = (
            _ordered_pending_commands(db, root)
            .filter(DevelopmentCommand.status == "queued").order_by(None)
            .order_by(DevelopmentCommand.id.desc())
            .limit(5)
            .all()
        )
        for row in rows:
            created = _as_naive_utc(row.created_at)
            if (row.content or "").strip() == (instruction or "").strip() and created is not None and 0 <= (now - created).total_seconds() <= window:
                return True
    return False


def enqueue_command(
    chat_id: int,
    content: str,
    *,
    source: str = "web",
    source_ref: str = "",
    command_type: str = "instruction",
    metadata: dict[str, Any] | None = None,
    idempotent: bool = False,
    expected_project_id: int | None = None,
    expected_scope: tuple[int, str, str, int | None, str] | None = None,
    expected_thread_identity: str | None = None,
) -> dict[str, Any]:
    instruction = redact_text(content, limit=64000 if command_type == 'followup' else 12000, preserve_paths=True)
    if not instruction:
        raise ValueError("A development instruction is required.")
    submission_digest = sha256(instruction.encode()).hexdigest() if idempotent else None
    reviewed_scope = expected_scope
    if command_type == 'followup':
        from .queued_submissions import submission_metadata
        if expected_scope is None:
            with get_session() as db:
                expected_scope = _queue_scope(_require_development_root(db, int(chat_id)))
        metadata = submission_metadata(int(chat_id), metadata)
    source = str(source or "web").strip().lower()[:40]
    with get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        root = _require_development_root(db, int(chat_id))
        if expected_thread_identity is not None and development_thread_receipt_identity(root) != expected_thread_identity:
            raise FollowupThreadIdentityConflict("This is a different Development thread. Reopen the original thread before retrying.")
        if idempotent:
            existing = db.query(DevelopmentCommand).filter_by(
                chat_id=int(chat_id), source=source, source_ref=source_ref, command_type=command_type,
            ).first()
            if existing is not None:
                saved = _json(existing.metadata_json, {})
                saved = saved if isinstance(saved, dict) else {}
                original_digest = saved.get("app_view_submission_digest") or sha256(existing.content.encode()).hexdigest()
                if original_digest != submission_digest:
                    raise ValueError("This submission was already used for a different instruction.")
                if reviewed_scope is not None and saved.get("app_view_scope") != list(reviewed_scope):
                    raise ValueError("This submission belongs to a different Development scope.")
                return _command_payload(existing, root=root)
        if root.is_archived or (idempotent and root.project_id != expected_project_id):
            raise ValueError("The Development thread changed. Reopen it before queuing this instruction.")
        scope = _queue_scope(root)
        if expected_scope is not None and scope != expected_scope:
            raise DevelopmentThreadConflict("The Development project, board or ticket changed. Review the draft before queuing it.")
        metadata = dict(metadata or {}) if command_type == "followup" else redact_metadata(metadata or {})
        metadata.pop("app_view_scope", None)
        metadata.pop("app_view_submission_digest", None)
        metadata["queued_scope"] = list(scope)
        if idempotent:
            metadata["app_view_scope"] = list(scope)
            metadata["app_view_submission_digest"] = submission_digest
        row = DevelopmentCommand(
            chat_id=int(chat_id),
            workflow_id=None,
            source=source,
            source_ref=str(source_ref or "").strip()[:240] or None,
            command_type=str(command_type or "instruction").strip().lower()[:40],
            content=instruction,
            status="queued",
            position=_next_command_position(db, int(chat_id)),
            metadata_json=json.dumps(metadata, ensure_ascii=False, default=str),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _command_payload(row, root=root)


def update_command(command_id: int, *, content: str | None = None, cancel: bool = False, metadata: dict | None = None, editing: bool | None = None, expected_modified_at: str | None = None,
                   followup_chat_id: int | None = None) -> dict[str, Any] | None:
    with get_session() as db:
        row = db.get(DevelopmentCommand, int(command_id))
        if row is None or (followup_chat_id is not None
                           and (row.chat_id != followup_chat_id or row.command_type != "followup")):
            return None
        if followup_chat_id is not None and cancel and row.status == "cancelled":
            return _command_payload(row)
        if row.status not in {"queued", "editing"}:
            raise ValueError("Only queued instructions can be changed.")
        if expected_modified_at and row.modified_at.isoformat() != expected_modified_at:
            raise ValueError('This queued item changed. Reload it before editing.')
        previous_status = row.status
        claimed = db.query(DevelopmentCommand).filter(DevelopmentCommand.id == command_id,
            DevelopmentCommand.status == previous_status, DevelopmentCommand.modified_at == row.modified_at).update({'status': 'editing'}, synchronize_session=False)
        if not claimed:
            raise ValueError('This queued item changed. Refresh the queue.')
        db.refresh(row)
        previous_metadata = _json(row.metadata_json, {})
        previous_metadata = previous_metadata if isinstance(previous_metadata, dict) else {}
        if cancel:
            row.status = "cancelled"
            row.cancelled_at = utc_now_naive()
            row.result_summary = "Cancelled before delivery."
        elif content is not None:
            clean = redact_text(content, limit=64000 if row.command_type == 'followup' else 12000, preserve_paths=True)
            if not clean:
                raise ValueError("Instruction cannot be empty.")
            if row.source == "app_view" and row.command_type == "followup" and row.source_ref:
                previous_metadata.setdefault("app_view_submission_digest", sha256(row.content.encode()).hexdigest())
                row.metadata_json = json.dumps(previous_metadata, ensure_ascii=False, default=str)
            row.content = clean
        if metadata is not None and not cancel:
            from .queued_submissions import submission_metadata
            updated_metadata = submission_metadata(row.chat_id, metadata)
            for key in ("queued_scope", "app_view_scope", "app_view_submission_digest"):
                updated_metadata.pop(key, None)
                if key in previous_metadata:
                    updated_metadata[key] = previous_metadata[key]
            row.metadata_json = json.dumps(updated_metadata, ensure_ascii=False, default=str)
        if editing is not None and not cancel:
            row.status = 'editing' if editing else 'queued'
        elif not cancel:
            row.status = previous_status
        db.commit()
        db.refresh(row)
        return _command_payload(row, root=_require_development_root(db, row.chat_id))


def _steering_turn_id(chat) -> int | None:
    params = _chat_params(chat.params)
    execution = (params.get("development") or {}).get("execution")
    turn_id = execution.get("turn_id") if isinstance(execution, dict) else None
    turn_id = turn_id or params.get("active_turn_chat_row_id")
    return turn_id if type(turn_id) is int and turn_id > 0 else None


def _exact_steering_command(db, chat_id: int, submission_id: str):
    return db.query(DevelopmentCommand).filter_by(
        chat_id=chat_id, source="app_view_steer", source_ref=submission_id,
    ).first()


def exact_steering_receipt(chat_id: int, turn_id: int, project_id: int, submission_id: str) -> dict[str, Any]:
    """Read the receipt without exposing the instruction or writing to the queue."""
    with get_session() as db:
        chat = _require_development_root(db, chat_id)
        if chat.project_id != project_id:
            raise LookupError("This steering request is unavailable on the selected project.")
        command = _exact_steering_command(db, chat_id, submission_id)
        if command is None:
            status, summary = "missing", ""
        else:
            metadata = _json(command.metadata_json, {})
            if metadata.get("project_id") != project_id or metadata.get("steering_turn_id") != turn_id:
                raise LookupError("This steering request is unavailable on the selected turn.")
            status, summary = command.status, command.result_summary or ""
        return {"chat_id": chat_id, "turn_id": turn_id, "submission_id": submission_id,
                "status": status, "summary": summary}


def steer_exact_turn(chat_id: int, turn_id: int, project_id: int, submission_id: str, guidance: str) -> dict[str, Any]:
    """Accept or recover one App View steering request for a specific native turn."""
    from distr.core.db.orm_compat import retry_sqlite_locked
    from distr.core.turn_runtime import steer_active_turn
    from distr.core.turn_runtime.service import native_steering_available

    instruction = redact_text(guidance, limit=12000, preserve_paths=True)
    if not instruction:
        raise ValueError("Steering guidance is required.")
    scope = {"steering_turn_id": turn_id, "project_id": project_id}
    with get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        chat = _require_development_root(db, chat_id)
        if chat.is_archived or chat.project_id != project_id:
            raise ValueError("The Development thread changed. Reopen it before steering.")
        prior = _exact_steering_command(db, chat_id, submission_id)
        if prior is not None:
            metadata = _json(prior.metadata_json, {})
            if prior.content != instruction or any(metadata.get(key) != value for key, value in scope.items()):
                raise ValueError("This submission belongs to a different steering request.")
            return _command_payload(prior)
        project = db.get(Project, project_id)
        development = development_thread_metadata(chat)
        execution = development.get("execution") or {}
        if (not project or project.deleted_at is not None or development.get("proactive_dispatch")
                or execution.get("runtime_id") != "native" or execution.get("turn_id") != turn_id
                or execution.get("completed_at") or not native_steering_available(chat_id, turn_id)):
            raise ValueError("The selected turn changed or can no longer accept guidance.")
        row = DevelopmentCommand(
            chat_id=chat_id, source="app_view_steer", source_ref=submission_id, command_type="steer",
            content=instruction, status="dispatching", position=_next_command_position(db, chat_id),
            metadata_json=json.dumps(scope),
        )
        db.add(row)
        db.flush()
        command_id = row.id
        db.commit()

    result = steer_active_turn(chat_id, instruction, expected_turn_id=turn_id)
    delivery = retry_sqlite_locked(lambda: _finish_command_steering(command_id, result, exact=True))
    return delivery["command"]


def dispatch_command(command_id: int) -> dict[str, Any]:
    """Steer an active native Development turn, or keep guidance safely queued."""
    with get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = db.get(DevelopmentCommand, int(command_id))
        if row is None:
            return {"error": "Command not found", "status_code": 404}
        if row.status != "queued":
            return {"command": _command_payload(row), "dispatched": row.status == "delivered"}
        chat_id = int(row.chat_id)
        chat = _require_development_root(db, chat_id)
        if command_scope_status(row, chat) != "current":
            return {"command": _command_payload(row, root=chat), "dispatched": False, "reason": "scope_unverified"}
        params = _chat_params(chat.params)
        development = params.get("development") or {}
        if development.get("proactive_dispatch") or row.command_type == "followup":
            return {"command": _command_payload(row), "dispatched": False, "reason": "followup_only"}
        expected_turn_id = _steering_turn_id(chat)
        content = row.content
        metadata = _json(row.metadata_json, {})
        if not isinstance(metadata, dict):
            metadata = {}
        target_turn_id = metadata.get("steering_turn_id")
        if target_turn_id is not None and target_turn_id != expected_turn_id:
            return {"command": _command_payload(row), "dispatched": False, "reason": "active_turn_changed"}
        if expected_turn_id is not None:
            metadata["steering_turn_id"] = expected_turn_id
        claimed = db.query(DevelopmentCommand).filter_by(
            id=row.id, status="queued", modified_at=row.modified_at,
        ).update({"status": "dispatching", "metadata_json": json.dumps(metadata, ensure_ascii=False, default=str)},
                 synchronize_session=False)
        if not claimed:
            db.rollback()
            return {"command": list_commands_for_id(command_id), "dispatched": False}
        db.commit()

    skill_ids = metadata.get("skill_ids")
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

    options = {"expected_turn_id": expected_turn_id} if expected_turn_id is not None else {}
    result = steer_active_turn(chat_id, steering_content, **options)
    from distr.core.db.orm_compat import retry_sqlite_locked

    return retry_sqlite_locked(lambda: _finish_command_steering(command_id, result))


def _finish_command_steering(command_id: int, result: dict[str, Any], *, exact: bool = False) -> dict[str, Any]:
    with get_session() as db:
        row = db.get(DevelopmentCommand, int(command_id))
        if row is None:
            return {"error": "Command not found", "status_code": 404}
        if row.status != "dispatching":
            return {"command": _command_payload(row), "dispatched": row.status == "delivered"}
        if not result.get("accepted"):
            row.status = "failed" if exact else "queued"
            if exact:
                row.result_summary = "The selected turn ended before accepting this guidance."
            db.commit()
            db.refresh(row)
            return {"command": _command_payload(row), "dispatched": False,
                    "reason": result.get("reason") or "no_active_native_turn"}
        row.run_id = None
        row.status = "delivered"
        row.delivered_at = utc_now_naive()
        row.result_summary = ("Accepted by the selected native Development turn." if exact
                              else "Applied to the active native Development turn.")
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
        chat = _require_development_root(db, chat_id)
        turn_id = _steering_turn_id(chat)
        pending = _ordered_pending_commands(db, chat).filter(
            DevelopmentCommand.status == "queued",
            DevelopmentCommand.command_type != "followup",
        ).order_by(None).order_by(DevelopmentCommand.id)
        command_id = None
        for row in pending:
            metadata = _json(row.metadata_json, {})
            if not isinstance(metadata, dict) or metadata.get("steering_turn_id") in (None, turn_id):
                command_id = row.id
                break
    if command_id is None:
        return {"status": "idle", "summary": "No queued instructions.", "next_actions": [], "artifacts": []}
    result = dispatch_command(int(command_id))
    return {
        "status": "delivered" if result.get("dispatched") else "queued",
        "summary": "Instruction delivered to the active worker." if result.get("dispatched") else "Instruction is safely queued until a worker can accept it.",
        "next_actions": ["edit", "cancel"] if not result.get("dispatched") else [],
        "artifacts": [result.get("command") or {}],
    }


def update_thread_controls(chat_id: int, **updates: Any) -> dict[str, Any]:
    allowed = {"pinned", "permission_profile", "remote_continuation", "shared_with_telegram"}
    with get_session() as db:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        chat = _require_development_root(db, chat_id)
        if "project_id" in updates:
            from distr.core.workflow.development_threads import (
                _release_local_ticket_link, _require_idle_scope_change, _upsert_work_item,
            )

            project_id = int(updates["project_id"]) if updates["project_id"] is not None else None
            current = development_thread_metadata(chat)
            unlinked_scope = development_scope_identity(project_id, {}, chat_id=chat.id)
            current_scope = development_scope_identity(chat.project_id, current, chat_id=chat.id)
            if project_id != chat.project_id or (project_id is None and unlinked_scope != current_scope):
                _require_idle_scope_change(db, chat, current, unlinked_scope)
                if project_id is not None:
                    project = db.get(Project, project_id)
                    if project is None or project.deleted_at is not None:
                        raise ValueError("Selected project is unavailable.")
                chat.project_id = project_id
                _release_local_ticket_link(db, chat.id)
                _upsert_work_item(
                    db, chat, workflow_id=current.get("workflow_id"), ticket_id=None,
                    source_type=current.get("source_type") or "prompt", source_ref=current.get("source_ref"),
                    board_key=None, board_provider=None, board_ticket_key=None,
                    board_ticket_title=None, board_ticket_lane=None,
                )
        params = _chat_params(chat.params)
        metadata = dict(params.get("development") or {})
        for key, value in updates.items():
            if key in allowed and value is not None:
                metadata[key] = value
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
            from distr.core.workflow.development_threads import normalize_autonomy_level

            autonomy = normalize_autonomy_level(updates["autonomy_level"])
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


def _time_entry_payload(row: DevelopmentTimeEntry) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "chat_id": int(row.chat_id),
        "work_item_id": int(row.work_item_id),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "seconds": max(0, int(row.seconds or 0)),
        "source": str(row.source or "play"),
    }


def _list_time_entries_for_item(db, chat_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        db.query(DevelopmentTimeEntry)
        .filter(DevelopmentTimeEntry.chat_id == int(chat_id))
        .order_by(DevelopmentTimeEntry.started_at.desc(), DevelopmentTimeEntry.id.desc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )
    return [_time_entry_payload(row) for row in rows]


def _close_running_segment(db, item: DevelopmentWorkItem, now: datetime, source: str) -> int:
    """Close an open segment into DevelopmentTimeEntry; return new accumulated seconds."""
    prior = max(0, int(item.time_accumulated_seconds or 0))
    if item.time_paused or not item.time_started_at:
        return prior
    started = item.time_started_at
    segment = max(0, int((now - started).total_seconds()))
    total = prior + segment
    db.add(
        DevelopmentTimeEntry(
            work_item_id=int(item.id),
            chat_id=int(item.chat_id),
            started_at=started,
            ended_at=now,
            seconds=segment,
            source=str(source or "play"),
        )
    )
    return total


def _sync_ticket_time(db, item: DevelopmentWorkItem, seconds: int) -> None:
    if item.local_ticket_id is None:
        return
    from distr.core.db.kanban import KanbanTicket
    from distr.core.kanban.ticket_time_tracking import format_time_tracking_seconds

    ticket = db.get(KanbanTicket, int(item.local_ticket_id))
    if ticket is None:
        return
    clean = max(0, int(seconds or 0))
    if clean > MAX_TICKET_TIME_SYNC_SECONDS:
        # Keep existing ticket string rather than writing multi-day ghosts.
        return
    ticket.time_spent = format_time_tracking_seconds(clean) if clean else ""


def thread_time_state(chat_id: int, *, auto_pause_after_seconds: int = 2) -> dict[str, Any]:
    """Return thread-owned time and auto-pause shortly after the reply lands.

    Grace is short on purpose (time-lapse): once the harness is idle, keep the
    elapsed value visible but stop incrementing after a couple of seconds.
    """
    from distr.core.workflow.development_harness import development_execution_active

    agent_active = development_execution_active(int(chat_id))
    now = utc_now_naive()
    with get_session() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
        if item is None:
            raise LookupError("Development thread not found.")
        if not item.time_paused and item.time_started_at:
            from distr.core.workflow.thread_time_bounds import settled_segment_end, thread_turns

            # Close at the end of the first continuous stretch of work after the segment
            # started, not at the latest reply. An unpolled timer on a daily automation
            # thread otherwise swallowed a week of separate runs into one auto_pause row.
            # The timer still gets a deadline when a worker never produced a response.
            activity_at = settled_segment_end(
                item.time_started_at,
                thread_turns(db, int(chat_id), item.time_started_at),
                last_activity_at=item.time_last_activity_at,
                now=now,
                grace_seconds=0,
            )
            if not agent_active and (now - activity_at).total_seconds() >= auto_pause_after_seconds:
                pause_at = activity_at + timedelta(seconds=auto_pause_after_seconds)
                item.time_accumulated_seconds = _close_running_segment(db, item, pause_at, "auto_pause")
                item.time_started_at = None
                item.time_paused = True
        payload = _thread_time_payload(item, now)
        payload["entries"] = _list_time_entries_for_item(db, int(chat_id))
        _sync_ticket_time(db, item, payload["seconds"])
        db.commit()
        return payload


def clear_stale_execution_status(chat_id: int, *, _session_provider=None) -> bool:
    """Clear a cached execution status that no live run backs.

    The thread spinner reads ``params.development.execution.status``, which can
    outlive the run that set it. Stopping a thread therefore has to correct a
    non-terminal status that no run is actually holding open.
    """
    from distr.core.db.workflow import AutoWorkflowRun

    with (_session_provider or get_session)() as db:
        chat = db.get(Chat, int(chat_id))
        if chat is None:
            return False
        try:
            params = json.loads(chat.params or "{}")
        except (TypeError, ValueError):
            return False
        if not isinstance(params, dict):
            return False
        development = params.get("development")
        execution = development.get("execution") if isinstance(development, dict) else None
        if not isinstance(execution, dict):
            return False
        status = str(execution.get("status") or "").strip().lower()
        if status not in {"initializing", "queued", "running", "waiting"}:
            return False
        live = (
            db.query(AutoWorkflowRun)
            .filter(
                AutoWorkflowRun.chat_id == int(chat_id),
                AutoWorkflowRun.status.in_(
                    ("initializing", "queued", "running", "waiting")
                ),
            )
            .first()
        )
        if live is not None:
            return False
        execution["status"] = "stopped"
        execution["activity_status"] = "stopped"
        execution["activity"] = ""
        development["execution"] = execution
        params["development"] = development
        chat.params = json.dumps(params)
        db.commit()
        return True


def resume_thread_time(chat_id: int, *, _session_provider=None) -> dict[str, Any]:
    now = utc_now_naive()
    with (_session_provider or get_session)() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
        if item is None:
            raise LookupError("Development thread not found.")
        from distr.core.workflow.thread_time_bounds import stale_segment_end

        # A new run on a timer nobody paused starts its own segment; the old one
        # closes where its own work went idle instead of stretching to this run.
        stale_end = stale_segment_end(db, item, now)
        if stale_end is not None:
            item.time_accumulated_seconds = _close_running_segment(db, item, stale_end, "auto_pause")
            item.time_started_at = None
            item.time_paused = True
        if item.time_paused or not item.time_started_at:
            item.time_started_at = now
        item.time_paused = False
        item.time_last_activity_at = now
        payload = _thread_time_payload(item, now)
        payload["entries"] = _list_time_entries_for_item(db, int(chat_id))
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
            item.time_accumulated_seconds = _close_running_segment(db, item, now, "play")
        item.time_started_at = None
        item.time_paused = True
        payload = _thread_time_payload(item, now)
        payload["entries"] = _list_time_entries_for_item(db, int(chat_id))
        _sync_ticket_time(db, item, payload["seconds"])
        db.commit()
    # Stopping the timer must also stop a spinner no run is backing.
    clear_stale_execution_status(int(chat_id))
    return payload


def reset_thread_time(chat_id: int, *, seconds: int = 0) -> dict[str, Any]:
    now = utc_now_naive()
    with get_session() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
        if item is None:
            raise LookupError("Development thread not found.")
        if not item.time_paused and item.time_started_at:
            _close_running_segment(db, item, now, "play")
        clean = max(0, int(seconds or 0))
        db.add(
            DevelopmentTimeEntry(
                work_item_id=int(item.id),
                chat_id=int(item.chat_id),
                started_at=now,
                ended_at=now,
                seconds=clean,
                source="reset" if clean == 0 else "manual",
            )
        )
        item.time_accumulated_seconds = clean
        item.time_started_at = None
        item.time_paused = True
        item.time_last_activity_at = now
        payload = _thread_time_payload(item, now)
        payload["entries"] = _list_time_entries_for_item(db, int(chat_id))
        _sync_ticket_time(db, item, payload["seconds"])
        db.commit()
    # Stop and reset must also stop a spinner no run is backing.
    clear_stale_execution_status(int(chat_id))
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
