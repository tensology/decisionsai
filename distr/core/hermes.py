"""Hermes orchestration ledger helpers.

Hermes is the normalized event stream connecting tickets, workflows, step
execution, CLI/IDE sessions, validation, and channel intake.
"""

from __future__ import annotations

from datetime import datetime
import json
import uuid
from typing import Any

from distr.core.db import Base, engine, get_session
from distr.core.db.hermes import (
    HermesCorrectionAttempt,
    HermesEvent,
    HermesValidationRecord,
    ProjectRuntimeSession,
)


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _json_loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value


def ensure_hermes_tables() -> None:
    Base.metadata.create_all(engine, tables=[
        HermesEvent.__table__,
        ProjectRuntimeSession.__table__,
        HermesValidationRecord.__table__,
        HermesCorrectionAttempt.__table__,
    ])


def is_hermes_enabled() -> bool:
    """Return whether Hermes event emission is enabled."""
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        return bool(settings.get("hermes_enabled", True))
    except Exception:
        return True


def get_hermes_role_model(role: str) -> tuple[str, str]:
    """Resolve provider/model for orchestrator, validator, or correction roles."""
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        provider = (settings.get(f"hermes_{role}_provider") or "").strip()
        model = (settings.get(f"hermes_{role}_model") or "").strip()
        if provider or model:
            return provider, model
        if role == "orchestrator":
            return (
                (settings.get("workflow_llm_provider") or "").strip(),
                (settings.get("workflow_llm_model") or "").strip(),
            )
    except Exception:
        pass
    return "", ""


def emit_event(
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
) -> int | None:
    """Append one normalized orchestration event.

    This intentionally swallows persistence failures at call sites through the
    caller's try/except pattern; the ledger should not break primary execution.
    """

    if not is_hermes_enabled():
        return None

    ensure_hermes_tables()
    with get_session() as session:
        row = HermesEvent(
            event_uid=uuid.uuid4().hex,
            source=(source or "system").strip() or "system",
            event_type=(event_type or "event").strip() or "event",
            status=(status or "").strip() or None,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            ticket_id=ticket_id,
            board_id=board_id,
            project_id=project_id,
            execution_session_id=execution_session_id,
            parent_event_id=parent_event_id,
            summary=summary or "",
            payload=_json_dumps(payload or {}),
            evidence=_json_dumps(evidence or {}),
        )
        session.add(row)
        session.commit()
        return int(row.id)


def list_events(
    *,
    workflow_id: int | None = None,
    run_id: int | None = None,
    ticket_id: int | None = None,
    execution_session_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_hermes_tables()
    with get_session() as session:
        query = session.query(HermesEvent)
        if workflow_id is not None:
            query = query.filter(HermesEvent.workflow_id == int(workflow_id))
        if run_id is not None:
            query = query.filter(HermesEvent.run_id == int(run_id))
        if ticket_id is not None:
            query = query.filter(HermesEvent.ticket_id == int(ticket_id))
        if execution_session_id is not None:
            query = query.filter(HermesEvent.execution_session_id == int(execution_session_id))
        rows = (
            query.order_by(HermesEvent.created_at.desc(), HermesEvent.id.desc())
            .limit(max(1, min(int(limit or 100), 500)))
            .all()
        )
        return [serialize_event(row) for row in rows]


def emit_channel_intake_event(
    *,
    channel: str,
    ticket_id: int,
    board_id: int | None = None,
    workflow_id: int | None = None,
    project_id: int | None = None,
    summary: str = "",
    payload: dict[str, Any] | None = None,
) -> int | None:
    """Record ticket creation from WhatsApp, Telegram, Gmail, or other channels."""
    channel_name = (channel or "channel").strip().lower() or "channel"
    return emit_event(
        source=channel_name,
        event_type="channel_intake_ticket_created",
        status="created",
        ticket_id=ticket_id,
        board_id=board_id,
        workflow_id=workflow_id,
        project_id=project_id,
        summary=summary or f"Ticket #{ticket_id} created from {channel_name} intake.",
        payload={"channel": channel_name, **(payload or {})},
    )


def emit_approval_event(
    *,
    event_type: str,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    summary: str = "",
    payload: dict[str, Any] | None = None,
) -> int | None:
    """Record human approval gate lifecycle events."""
    return emit_event(
        source="approval",
        event_type=event_type,
        status="waiting" if event_type == "approval_requested" else "granted",
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        board_id=board_id,
        summary=summary,
        payload=payload or {},
    )


def count_correction_attempts(
    *,
    run_id: int | None = None,
    step_id: int | None = None,
    validation_record_id: int | None = None,
) -> int:
    ensure_hermes_tables()
    with get_session() as session:
        query = session.query(HermesCorrectionAttempt)
        if run_id is not None:
            query = query.filter(HermesCorrectionAttempt.run_id == int(run_id))
        if step_id is not None:
            query = query.filter(HermesCorrectionAttempt.step_id == int(step_id))
        if validation_record_id is not None:
            query = query.filter(
                HermesCorrectionAttempt.validation_record_id == int(validation_record_id)
            )
        return int(query.count())


def mark_correction_dispatched(
    attempt_id: int,
    *,
    dispatch_result: dict[str, Any] | None = None,
) -> None:
    ensure_hermes_tables()
    now = datetime.utcnow()
    with get_session() as session:
        row = (
            session.query(HermesCorrectionAttempt)
            .filter(HermesCorrectionAttempt.id == int(attempt_id))
            .first()
        )
        if not row:
            return
        row.status = "dispatched"
        row.dispatched_at = now
        if dispatch_result is not None:
            row.dispatch_result = _json_dumps(dispatch_result)
        session.add(row)
        session.commit()
        attempt = row

    try:
        emit_event(
            source="correction",
            event_type="correction_dispatched",
            status="dispatched",
            workflow_id=attempt.workflow_id,
            run_id=attempt.run_id,
            step_id=attempt.step_id,
            ticket_id=attempt.ticket_id,
            board_id=attempt.board_id,
            project_id=attempt.project_id,
            execution_session_id=attempt.execution_session_id,
            summary=f"Correction attempt #{attempt_id} dispatched for step #{attempt.step_id}.",
            payload={
                "correction_attempt_id": attempt_id,
                "validation_record_id": attempt.validation_record_id,
                "attempt_number": attempt.attempt_number,
                "target_backend": attempt.target_backend or "",
                "target_model": attempt.target_model or "",
            },
            evidence={
                "correction_packet": _json_loads(attempt.correction_packet) or {},
                "dispatch_result": dispatch_result or {},
            },
        )
    except Exception:
        pass


def format_correction_instruction(packet: dict[str, Any] | None) -> str:
    """Turn a correction packet into step-prepended instruction text."""
    packet = packet or {}
    failed = packet.get("failed_validation") or {}
    parts = [
        "[CORRECTION REQUIRED]",
        str(packet.get("instruction") or "").strip(),
    ]
    if failed.get("expected"):
        parts.append(f"Expected: {failed['expected']}")
    if failed.get("observed"):
        parts.append(f"Observed: {failed['observed']}")
    if failed.get("correction_hint"):
        parts.append(f"Hint: {failed['correction_hint']}")
    runtime = packet.get("runtime") or {}
    urls = runtime.get("urls") or []
    if urls and isinstance(urls, list) and urls[0].get("url"):
        parts.append(f"Runtime URL: {urls[0]['url']}")
    executor_output = str(packet.get("executor_output") or "").strip()
    if executor_output:
        parts.append(f"Previous executor output:\n{executor_output[:4000]}")
    return "\n\n".join(part for part in parts if part).strip()


def serialize_event(row: HermesEvent) -> dict[str, Any]:
    created_at = row.created_at or datetime.utcnow()
    return {
        "id": row.id,
        "event_uid": row.event_uid,
        "source": row.source,
        "event_type": row.event_type,
        "status": row.status,
        "workflow_id": row.workflow_id,
        "run_id": row.run_id,
        "step_id": row.step_id,
        "ticket_id": row.ticket_id,
        "board_id": row.board_id,
        "project_id": row.project_id,
        "execution_session_id": row.execution_session_id,
        "parent_event_id": row.parent_event_id,
        "summary": row.summary or "",
        "payload": _json_loads(row.payload),
        "evidence": _json_loads(row.evidence),
        "created_at": created_at.isoformat(),
    }


def upsert_project_runtime_session(
    *,
    terminal_id: str,
    project_id: int,
    pid: int | None = None,
    command: str = "",
    cwd: str = "",
    purpose: str = "startup",
    owner: str = "decisions_project_runtime",
    status: str = "running",
    urls: list[dict[str, Any]] | None = None,
    buffer_preview: str = "",
    created_at_epoch: float | None = None,
) -> None:
    ensure_hermes_tables()
    now = datetime.utcnow()
    with get_session() as session:
        row = (
            session.query(ProjectRuntimeSession)
            .filter(ProjectRuntimeSession.terminal_id == str(terminal_id))
            .first()
        )
        if not row:
            row = ProjectRuntimeSession(
                terminal_id=str(terminal_id),
                project_id=int(project_id),
                started_at=now,
            )
        row.project_id = int(project_id)
        row.pid = pid
        row.command = command or ""
        row.cwd = cwd or ""
        row.purpose = purpose or "startup"
        row.owner = owner or "decisions_project_runtime"
        row.status = status or "running"
        row.urls = _json_dumps(urls or [])
        row.last_buffer_preview = (buffer_preview or "")[-4000:]
        row.safe_restart_policy = "Only restart Decisions-owned project runtime terminals; do not kill user-owned terminals without approval."
        row.last_seen_at = now
        row.created_at_epoch = created_at_epoch
        if status in ("stopped", "failed", "dead"):
            row.ended_at = now
        session.add(row)
        session.commit()


def mark_project_runtime_session_stopped(terminal_id: str, *, status: str = "stopped") -> None:
    if not terminal_id:
        return
    ensure_hermes_tables()
    with get_session() as session:
        row = (
            session.query(ProjectRuntimeSession)
            .filter(ProjectRuntimeSession.terminal_id == str(terminal_id))
            .first()
        )
        if not row:
            return
        row.status = status or "stopped"
        row.ended_at = datetime.utcnow()
        row.last_seen_at = datetime.utcnow()
        session.add(row)
        session.commit()


def list_project_runtime_sessions(
    *,
    project_id: int | None = None,
    active_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ensure_hermes_tables()
    with get_session() as session:
        query = session.query(ProjectRuntimeSession)
        if project_id is not None:
            query = query.filter(ProjectRuntimeSession.project_id == int(project_id))
        if active_only:
            query = query.filter(ProjectRuntimeSession.status == "running")
        rows = (
            query.order_by(ProjectRuntimeSession.last_seen_at.desc(), ProjectRuntimeSession.id.desc())
            .limit(max(1, min(int(limit or 50), 200)))
            .all()
        )
        return [serialize_project_runtime_session(row) for row in rows]


def serialize_project_runtime_session(row: ProjectRuntimeSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "terminal_id": row.terminal_id,
        "project_id": row.project_id,
        "pid": row.pid,
        "command": row.command or "",
        "cwd": row.cwd or "",
        "purpose": row.purpose or "",
        "owner": row.owner or "",
        "status": row.status or "",
        "urls": _json_loads(row.urls) or [],
        "last_buffer_preview": row.last_buffer_preview or "",
        "safe_restart_policy": row.safe_restart_policy or "",
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "created_at_epoch": row.created_at_epoch,
    }


def _validation_bool_text(value: bool | None) -> str | None:
    if value is None:
        return None
    return "true" if bool(value) else "false"


def build_validation_correction_hint(snapshot: dict[str, Any] | None) -> str:
    snapshot = snapshot or {}
    if str(snapshot.get("verdict") or "").lower() == "pass":
        return ""
    expected = str(snapshot.get("expected") or "").strip()
    observed = str(snapshot.get("observed") or "").strip()
    vtype = str(snapshot.get("validation_type") or "none").strip() or "none"
    parts = [f"Validation failed using {vtype}."]
    if expected:
        parts.append(f"Expected: {expected[:600]}")
    if observed:
        parts.append(f"Observed: {observed[:600]}")
    parts.append("Correction should return focused evidence that the expected outcome works, not just a claim that work was attempted.")
    return " ".join(parts)


def record_validation(
    *,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    step_result_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
    execution_session_id: int | None = None,
    validation_snapshot: dict[str, Any] | None = None,
    standards_context: str = "",
    correction_hint: str = "",
    payload: dict[str, Any] | None = None,
) -> int | None:
    ensure_hermes_tables()
    snapshot = validation_snapshot or {}
    verdict = str(snapshot.get("verdict") or "unknown").strip().lower() or "unknown"
    expected = str(snapshot.get("expected") or "").strip()
    observed = str(snapshot.get("observed") or "").strip()
    validation_type = str(snapshot.get("validation_type") or "none").strip() or "none"
    hint = correction_hint or build_validation_correction_hint(snapshot)

    with get_session() as session:
        row = HermesValidationRecord(
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            step_result_id=step_result_id,
            ticket_id=ticket_id,
            board_id=board_id,
            project_id=project_id,
            execution_session_id=execution_session_id,
            validation_type=validation_type,
            expected=expected,
            observed=observed,
            standards_context=(standards_context or "")[:12000],
            caller_passed=_validation_bool_text(snapshot.get("caller_passed")),
            verified_passed=_validation_bool_text(snapshot.get("verified_passed")),
            verdict=verdict,
            correction_hint=hint,
            payload=_json_dumps({"snapshot": snapshot, **(payload or {})}),
        )
        session.add(row)
        session.commit()
        record_id = int(row.id)

    try:
        emit_event(
            source="validation",
            event_type="validation_recorded",
            status="passed" if verdict == "pass" else "failed" if verdict == "fail" else verdict,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            ticket_id=ticket_id,
            board_id=board_id,
            project_id=project_id,
            execution_session_id=execution_session_id,
            summary=(
                f"Validation {verdict} for step #{step_id}."
                if step_id
                else f"Validation {verdict}."
            ),
            payload={
                "validation_record_id": record_id,
                "validation_type": validation_type,
                "expected": expected,
                "verdict": verdict,
                "correction_hint": hint,
            },
            evidence={
                "result_preview": observed[:3000],
                "validation": snapshot,
            },
        )
    except Exception:
        pass

    return record_id


def list_validation_records(
    *,
    workflow_id: int | None = None,
    run_id: int | None = None,
    ticket_id: int | None = None,
    verdict: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_hermes_tables()
    with get_session() as session:
        query = session.query(HermesValidationRecord)
        if workflow_id is not None:
            query = query.filter(HermesValidationRecord.workflow_id == int(workflow_id))
        if run_id is not None:
            query = query.filter(HermesValidationRecord.run_id == int(run_id))
        if ticket_id is not None:
            query = query.filter(HermesValidationRecord.ticket_id == int(ticket_id))
        if verdict:
            query = query.filter(HermesValidationRecord.verdict == str(verdict).lower())
        rows = (
            query.order_by(HermesValidationRecord.created_at.desc(), HermesValidationRecord.id.desc())
            .limit(max(1, min(int(limit or 100), 500)))
            .all()
        )
        return [serialize_validation_record(row) for row in rows]


def serialize_validation_record(row: HermesValidationRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "workflow_id": row.workflow_id,
        "run_id": row.run_id,
        "step_id": row.step_id,
        "step_result_id": row.step_result_id,
        "ticket_id": row.ticket_id,
        "board_id": row.board_id,
        "project_id": row.project_id,
        "execution_session_id": row.execution_session_id,
        "validation_type": row.validation_type,
        "expected": row.expected or "",
        "observed": row.observed or "",
        "standards_context": row.standards_context or "",
        "caller_passed": row.caller_passed,
        "verified_passed": row.verified_passed,
        "verdict": row.verdict,
        "correction_hint": row.correction_hint or "",
        "payload": _json_loads(row.payload) or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def build_correction_packet(
    *,
    validation_record: dict[str, Any],
    ticket_title: str = "",
    step_name: str = "",
    runtime_snapshot: dict[str, Any] | None = None,
    executor_output: str = "",
) -> dict[str, Any]:
    snapshot = (validation_record.get("payload") or {}).get("snapshot") or {}
    return {
        "ticket": {
            "id": validation_record.get("ticket_id"),
            "title": ticket_title or "",
        },
        "workflow": {
            "id": validation_record.get("workflow_id"),
            "run_id": validation_record.get("run_id"),
            "step_id": validation_record.get("step_id"),
            "step_name": step_name or snapshot.get("step_name") or "",
        },
        "failed_validation": {
            "record_id": validation_record.get("id"),
            "validation_type": validation_record.get("validation_type"),
            "expected": validation_record.get("expected"),
            "observed": validation_record.get("observed"),
            "verdict": validation_record.get("verdict"),
            "correction_hint": validation_record.get("correction_hint"),
        },
        "runtime": runtime_snapshot or {},
        "executor_output": executor_output or "",
        "instruction": (
            "Correct the previous work so the failed validation passes. "
            "Use the expected outcome, observed failure, project runtime context, and quality standards. "
            "Return concrete evidence: files changed, tests/checks run, runtime URL checked, and remaining blockers."
        ),
    }


def create_correction_attempt(
    *,
    validation_record_id: int,
    target_backend: str = "",
    target_model: str = "",
    correction_packet: dict[str, Any] | None = None,
    status: str = "queued",
) -> int | None:
    ensure_hermes_tables()
    with get_session() as session:
        validation = (
            session.query(HermesValidationRecord)
            .filter(HermesValidationRecord.id == int(validation_record_id))
            .first()
        )
        if not validation:
            return None
        attempt_count = (
            session.query(HermesCorrectionAttempt)
            .filter(HermesCorrectionAttempt.validation_record_id == int(validation_record_id))
            .count()
        )
        row = HermesCorrectionAttempt(
            validation_record_id=int(validation_record_id),
            workflow_id=validation.workflow_id,
            run_id=validation.run_id,
            step_id=validation.step_id,
            ticket_id=validation.ticket_id,
            board_id=validation.board_id,
            project_id=validation.project_id,
            execution_session_id=validation.execution_session_id,
            status=status or "queued",
            attempt_number=int(attempt_count) + 1,
            target_backend=target_backend or "",
            target_model=target_model or "",
            correction_packet=_json_dumps(correction_packet or {}),
        )
        session.add(row)
        session.commit()
        attempt_id = int(row.id)

    try:
        emit_event(
            source="correction",
            event_type="correction_attempt_created",
            status=status or "queued",
            workflow_id=validation.workflow_id,
            run_id=validation.run_id,
            step_id=validation.step_id,
            ticket_id=validation.ticket_id,
            board_id=validation.board_id,
            project_id=validation.project_id,
            execution_session_id=validation.execution_session_id,
            summary=f"Correction attempt #{attempt_id} created for failed validation #{validation_record_id}.",
            payload={
                "correction_attempt_id": attempt_id,
                "validation_record_id": validation_record_id,
                "attempt_number": int(attempt_count) + 1,
                "target_backend": target_backend,
                "target_model": target_model,
                "correction_packet": correction_packet or {},
            },
            evidence={
                "validation": serialize_validation_record(validation),
            },
        )
    except Exception:
        pass

    return attempt_id


def list_correction_attempts(
    *,
    workflow_id: int | None = None,
    run_id: int | None = None,
    ticket_id: int | None = None,
    validation_record_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_hermes_tables()
    with get_session() as session:
        query = session.query(HermesCorrectionAttempt)
        if workflow_id is not None:
            query = query.filter(HermesCorrectionAttempt.workflow_id == int(workflow_id))
        if run_id is not None:
            query = query.filter(HermesCorrectionAttempt.run_id == int(run_id))
        if ticket_id is not None:
            query = query.filter(HermesCorrectionAttempt.ticket_id == int(ticket_id))
        if validation_record_id is not None:
            query = query.filter(HermesCorrectionAttempt.validation_record_id == int(validation_record_id))
        rows = (
            query.order_by(HermesCorrectionAttempt.created_at.desc(), HermesCorrectionAttempt.id.desc())
            .limit(max(1, min(int(limit or 100), 500)))
            .all()
        )
        return [serialize_correction_attempt(row) for row in rows]


def serialize_correction_attempt(row: HermesCorrectionAttempt) -> dict[str, Any]:
    return {
        "id": row.id,
        "validation_record_id": row.validation_record_id,
        "workflow_id": row.workflow_id,
        "run_id": row.run_id,
        "step_id": row.step_id,
        "ticket_id": row.ticket_id,
        "board_id": row.board_id,
        "project_id": row.project_id,
        "execution_session_id": row.execution_session_id,
        "status": row.status,
        "attempt_number": row.attempt_number,
        "target_backend": row.target_backend or "",
        "target_model": row.target_model or "",
        "correction_packet": _json_loads(row.correction_packet) or {},
        "dispatch_result": _json_loads(row.dispatch_result) or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "dispatched_at": row.dispatched_at.isoformat() if row.dispatched_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }
