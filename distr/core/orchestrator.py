"""Orchestrator ledger helpers (DecisionsAI Orchestrator ledger).

The Orchestrator is DecisionsAI's internal event stream connecting tickets,
workflows, step execution, CLI/IDE sessions, validation, and channel intake.

This is **not** Nous Hermes Agent (``~/.hermes``, ``hermes`` CLI). See
``docs/orchestrator.md`` and ``docs/nous-hermes-agent.md``.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid
from typing import Any

from distr.core.db import Base, engine, get_session
from distr.core.db.orchestrator import (
    OrchestratorCorrectionAttempt,
    OrchestratorEvent,
    OrchestratorLearnedRule,
    OrchestratorMachineActivity,
    OrchestratorMaintenanceState,
    OrchestratorUserMemory,
    OrchestratorValidationRecord,
    OrchestratorVisualBaselineScreen,
    OrchestratorVisualBaselineSet,
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


_HANDOFF_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|bearer|credential|client[_-]?secret)",
    re.IGNORECASE,
)
_HANDOFF_SECRET_VALUE_RE = re.compile(
    r"(?i)((?:internal_token|api[_-]?key|token|secret|password)=)[^&\s\"']+|(bearer\s+)[a-z0-9._\-+/=]{12,}|(sk-[a-z0-9_\-]{12,})|(?<![/\w.-])([a-z0-9_\-]{32,})(?![/\w.-])"
)


def _redact_handoff_text(value: str) -> str:
    return _HANDOFF_SECRET_VALUE_RE.sub(lambda m: (m.group(1) or m.group(2) or "") + "[redacted]", value or "")


def redact_handoff_payload(value: Any) -> Any:
    """Return a JSON-safe handoff payload with obvious secrets removed."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _HANDOFF_SECRET_KEY_RE.search(key_text):
                redacted[key_text] = "[redacted]"
            else:
                redacted[key_text] = redact_handoff_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_handoff_payload(item) for item in value]
    if isinstance(value, tuple):
        return [redact_handoff_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_handoff_text(value)
    return value


def handoff_payload_hash(value: Any) -> str:
    raw = json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_backend_handoff_packet(
    *,
    backend_id: str,
    model: str = "",
    instruction: str = "",
    project_id: int | None = None,
    project_name: str = "",
    project_folder: str = "",
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    execution_session_id: int | None = None,
    route_rationale: str = "",
    selection_reason: str = "",
    origin: str = "",
    complexity: str = "",
    git_status_before: str = "",
    runtime_snapshot: dict[str, Any] | None = None,
    callback: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the durable Decisions-to-worker handoff packet stored by the orchestrator."""
    raw = {
        "backend_id": backend_id,
        "model": model or "auto",
        "instruction": instruction,
        "project_id": project_id,
        "project_name": project_name,
        "project_folder": project_folder,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "step_id": step_id,
        "ticket_id": ticket_id,
        "board_id": board_id,
        "execution_session_id": execution_session_id,
        "route_rationale": route_rationale,
        "selection_reason": selection_reason,
        "origin": origin,
        "complexity": complexity,
        "git_status_before": git_status_before,
        "runtime_snapshot": runtime_snapshot or {},
        "callback": callback or {},
        "human_intervention": {
            "state": "none",
            "latest_message": "",
            "latest_label": "",
        },
        **(extra or {}),
    }
    packet = redact_handoff_payload(raw)
    if isinstance(packet, dict):
        packet["payload_hash"] = handoff_payload_hash(raw)
    return packet


def record_backend_handoff(
    *,
    packet: dict[str, Any],
    status: str = "created",
    event_type: str = "backend_handoff_created",
    summary: str = "",
    evidence: dict[str, Any] | None = None,
) -> int | None:
    """Record a backend/IDE handoff as a first-class orchestrator event."""
    data = dict(packet or {})
    backend_id = str(data.get("backend_id") or "worker")
    message = summary or f"Backend handoff {status}: {backend_id}."
    return emit_event(
        source=backend_id,
        event_type=event_type,
        status=status,
        workflow_id=data.get("workflow_id"),
        run_id=data.get("run_id"),
        step_id=data.get("step_id"),
        ticket_id=data.get("ticket_id"),
        board_id=data.get("board_id"),
        project_id=data.get("project_id"),
        execution_session_id=data.get("execution_session_id"),
        summary=message,
        payload=data,
        evidence=evidence or {},
    )


MISTAKE_LABELS = {
    "missed_requirement",
    "wrong_scope",
    "bad_ui_flow",
    "insufficient_tests",
    "ignored_instruction",
    "wrong_backend",
    "unclear_requirement",
    "manual_fix_applied",
    "needs_visual_check",
    "rejected_other",
}


def normalize_mistake_label(label: str) -> str:
    raw = (label or "").strip().lower().replace("-", "_").replace(" ", "_")
    return raw if raw in MISTAKE_LABELS else "rejected_other"


def record_human_intervention_memory(
    *,
    label: str,
    message: str = "",
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
    execution_session_id: int | None = None,
    handoff_event_id: int | None = None,
) -> int | None:
    """Persist user steering or corrections as durable mistake memory."""
    normalized = normalize_mistake_label(label)
    summary = (
        f"Human intervention recorded: {normalized.replace('_', ' ')}"
        + (f". {message.strip()}" if message.strip() else ".")
    )
    payload = {
        "label": normalized,
        "message": message or "",
        "handoff_event_id": handoff_event_id,
    }
    event_id = emit_event(
        source="human_intervention",
        event_type="human_intervention_recorded",
        status="recorded",
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        board_id=board_id,
        project_id=project_id,
        execution_session_id=execution_session_id,
        summary=summary,
        payload=payload,
    )
    record_learning_signal(
        scope="board" if board_id else "project" if project_id else "global",
        scope_id=board_id or project_id,
        rule_type="human_intervention",
        summary=summary,
        payload=payload,
    )
    return event_id


def ensure_orchestrator_tables() -> None:
    Base.metadata.create_all(engine, tables=[
        OrchestratorEvent.__table__,
        OrchestratorUserMemory.__table__,
        OrchestratorMachineActivity.__table__,
        OrchestratorMaintenanceState.__table__,
        ProjectRuntimeSession.__table__,
        OrchestratorValidationRecord.__table__,
        OrchestratorCorrectionAttempt.__table__,
        OrchestratorLearnedRule.__table__,
        OrchestratorVisualBaselineSet.__table__,
        OrchestratorVisualBaselineScreen.__table__,
    ])


def resolve_board_id_for_ticket(ticket_id: int | None) -> int | None:
    if not ticket_id:
        return None
    try:
        from distr.core.db.kanban import KanbanLane, KanbanTicket

        with get_session() as session:
            row = (
                session.query(KanbanLane.board_id)
                .join(KanbanTicket, KanbanTicket.lane_id == KanbanLane.id)
                .filter(KanbanTicket.id == int(ticket_id))
                .first()
            )
            return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def resolve_board_id_for_run(run_id: int | None) -> int | None:
    if not run_id:
        return None
    try:
        from distr.core.db.workflow import AutoWorkflowRun

        with get_session() as session:
            run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
            if not run:
                return None
            board_id = getattr(run, "board_id", None)
            if board_id:
                return int(board_id)
            return resolve_board_id_for_ticket(getattr(run, "ticket_id", None))
    except Exception:
        return None


def _coalesce_board_id(
    board_id: int | None,
    *,
    ticket_id: int | None = None,
    run_id: int | None = None,
) -> int | None:
    if board_id is not None:
        return int(board_id)
    resolved = resolve_board_id_for_ticket(ticket_id)
    if resolved is not None:
        return resolved
    return resolve_board_id_for_run(run_id)


def is_orchestrator_enabled() -> bool:
    """Return whether orchestrator event emission is enabled."""
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        return bool(settings.get("orchestrator_enabled", True))
    except Exception:
        return True


ORCHESTRATOR_ROLE_SETTINGS_KEYS: dict[str, tuple[str, str]] = {
    "orchestrator": ("orchestrator_provider", "orchestrator_model"),
    "validator": ("orchestrator_validator_provider", "orchestrator_validator_model"),
    "correction": ("orchestrator_correction_provider", "orchestrator_correction_model"),
}


def get_orchestrator_role_model(role: str) -> tuple[str, str]:
    """Resolve provider/model for orchestrator, validator, or correction roles."""
    try:
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        provider_key, model_key = ORCHESTRATOR_ROLE_SETTINGS_KEYS.get(
            role, (f"orchestrator_{role}_provider", f"orchestrator_{role}_model")
        )
        provider = (settings.get(provider_key) or "").strip()
        model = (settings.get(model_key) or "").strip()
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

    if not is_orchestrator_enabled():
        return None

    board_id = _coalesce_board_id(board_id, ticket_id=ticket_id, run_id=run_id)

    ensure_orchestrator_tables()
    with get_session() as session:
        row = OrchestratorEvent(
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
        event_id = int(row.id)
        try:
            from distr.core.events import ORCHESTRATION_EVENT, get_event_bus

            get_event_bus().publish(ORCHESTRATION_EVENT, serialize_event(row))
        except Exception:
            logger.debug("Could not publish orchestration event to EventBus", exc_info=True)
        return event_id


def list_events(
    *,
    workflow_id: int | None = None,
    run_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
    execution_session_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_orchestrator_tables()
    with get_session() as session:
        query = session.query(OrchestratorEvent)
        if workflow_id is not None:
            query = query.filter(OrchestratorEvent.workflow_id == int(workflow_id))
        if run_id is not None:
            query = query.filter(OrchestratorEvent.run_id == int(run_id))
        if ticket_id is not None:
            query = query.filter(OrchestratorEvent.ticket_id == int(ticket_id))
        if board_id is not None:
            query = query.filter(OrchestratorEvent.board_id == int(board_id))
        if project_id is not None:
            query = query.filter(OrchestratorEvent.project_id == int(project_id))
        if execution_session_id is not None:
            query = query.filter(OrchestratorEvent.execution_session_id == int(execution_session_id))
        rows = (
            query.order_by(OrchestratorEvent.created_at.desc(), OrchestratorEvent.id.desc())
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
    """Count correction attempts (unused while auto-dispatch corrections are disabled)."""
    ensure_orchestrator_tables()
    with get_session() as session:
        query = session.query(OrchestratorCorrectionAttempt)
        if run_id is not None:
            query = query.filter(OrchestratorCorrectionAttempt.run_id == int(run_id))
        if step_id is not None:
            query = query.filter(OrchestratorCorrectionAttempt.step_id == int(step_id))
        if validation_record_id is not None:
            query = query.filter(
                OrchestratorCorrectionAttempt.validation_record_id == int(validation_record_id)
            )
        return int(query.count())


def mark_correction_dispatched(
    attempt_id: int,
    *,
    dispatch_result: dict[str, Any] | None = None,
) -> None:
    """Mark a correction attempt dispatched (reserved for future correction E2E)."""
    ensure_orchestrator_tables()
    now = datetime.utcnow()
    with get_session() as session:
        row = (
            session.query(OrchestratorCorrectionAttempt)
            .filter(OrchestratorCorrectionAttempt.id == int(attempt_id))
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


def serialize_event(row: OrchestratorEvent) -> dict[str, Any]:
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
    ensure_orchestrator_tables()
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
    ensure_orchestrator_tables()
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
    ensure_orchestrator_tables()
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
    ensure_orchestrator_tables()
    snapshot = validation_snapshot or {}
    verdict = str(snapshot.get("verdict") or "unknown").strip().lower() or "unknown"
    expected = str(snapshot.get("expected") or "").strip()
    observed = str(snapshot.get("observed") or "").strip()
    validation_type = str(snapshot.get("validation_type") or "none").strip() or "none"
    hint = correction_hint or build_validation_correction_hint(snapshot)

    with get_session() as session:
        row = OrchestratorValidationRecord(
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

    if verdict not in {"pass", "unknown", ""}:
        try:
            record_learning_signal(
                scope="board" if board_id else "global",
                scope_id=board_id,
                rule_type="validation_failure",
                summary=hint or f"Step failed {validation_type} validation.",
                payload={
                    "validation_record_id": record_id,
                    "validation_type": validation_type,
                    "expected": expected,
                    "observed": observed[:1000],
                    "verdict": verdict,
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
    board_id: int | None = None,
    project_id: int | None = None,
    verdict: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_orchestrator_tables()
    with get_session() as session:
        query = session.query(OrchestratorValidationRecord)
        if workflow_id is not None:
            query = query.filter(OrchestratorValidationRecord.workflow_id == int(workflow_id))
        if run_id is not None:
            query = query.filter(OrchestratorValidationRecord.run_id == int(run_id))
        if ticket_id is not None:
            query = query.filter(OrchestratorValidationRecord.ticket_id == int(ticket_id))
        if board_id is not None:
            query = query.filter(OrchestratorValidationRecord.board_id == int(board_id))
        if project_id is not None:
            query = query.filter(OrchestratorValidationRecord.project_id == int(project_id))
        if verdict:
            query = query.filter(OrchestratorValidationRecord.verdict == str(verdict).lower())
        rows = (
            query.order_by(OrchestratorValidationRecord.created_at.desc(), OrchestratorValidationRecord.id.desc())
            .limit(max(1, min(int(limit or 100), 500)))
            .all()
        )
        return [serialize_validation_record(row) for row in rows]


def serialize_validation_record(row: OrchestratorValidationRecord) -> dict[str, Any]:
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
    ensure_orchestrator_tables()
    with get_session() as session:
        validation = (
            session.query(OrchestratorValidationRecord)
            .filter(OrchestratorValidationRecord.id == int(validation_record_id))
            .first()
        )
        if not validation:
            return None
        attempt_count = (
            session.query(OrchestratorCorrectionAttempt)
            .filter(OrchestratorCorrectionAttempt.validation_record_id == int(validation_record_id))
            .count()
        )
        row = OrchestratorCorrectionAttempt(
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
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    ensure_orchestrator_tables()
    with get_session() as session:
        query = session.query(OrchestratorCorrectionAttempt)
        if workflow_id is not None:
            query = query.filter(OrchestratorCorrectionAttempt.workflow_id == int(workflow_id))
        if run_id is not None:
            query = query.filter(OrchestratorCorrectionAttempt.run_id == int(run_id))
        if ticket_id is not None:
            query = query.filter(OrchestratorCorrectionAttempt.ticket_id == int(ticket_id))
        if validation_record_id is not None:
            query = query.filter(OrchestratorCorrectionAttempt.validation_record_id == int(validation_record_id))
        if status:
            query = query.filter(OrchestratorCorrectionAttempt.status == str(status).strip().lower())
        rows = (
            query.order_by(OrchestratorCorrectionAttempt.created_at.desc(), OrchestratorCorrectionAttempt.id.desc())
            .limit(max(1, min(int(limit or 100), 500)))
            .all()
        )
        return [serialize_correction_attempt(row) for row in rows]


def serialize_correction_attempt(row: OrchestratorCorrectionAttempt) -> dict[str, Any]:
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


def record_learning_signal(
    *,
    scope: str = "board",
    scope_id: int | None = None,
    rule_type: str = "validation_failure",
    summary: str = "",
    payload: dict[str, Any] | None = None,
) -> int | None:
    """Upsert a lightweight learned rule from validation or IDE iteration."""
    text = (summary or "").strip()
    if not text:
        return None

    ensure_orchestrator_tables()
    normalized_scope = (scope or "global").strip().lower() or "global"
    if normalized_scope not in {"global", "board", "project"}:
        normalized_scope = "board"
    rule_key = text[:500]

    with get_session() as session:
        query = (
            session.query(OrchestratorLearnedRule)
            .filter(OrchestratorLearnedRule.scope == normalized_scope)
            .filter(OrchestratorLearnedRule.rule_type == (rule_type or "validation_failure"))
            .filter(OrchestratorLearnedRule.summary == rule_key)
        )
        if normalized_scope == "global":
            query = query.filter(OrchestratorLearnedRule.scope_id.is_(None))
        elif scope_id is not None:
            query = query.filter(OrchestratorLearnedRule.scope_id == int(scope_id))
        row = query.first()
        now = datetime.utcnow()
        if row:
            row.evidence_count = int(row.evidence_count or 0) + 1
            row.confidence = min(0.95, float(row.confidence or 0.5) + 0.05)
            row.payload = _json_dumps({"latest": payload or {}, "merged": _json_loads(row.payload) or {}})
            row.updated_at = now
        else:
            row = OrchestratorLearnedRule(
                scope=normalized_scope,
                scope_id=int(scope_id) if scope_id is not None else None,
                rule_type=(rule_type or "validation_failure").strip() or "validation_failure",
                summary=rule_key,
                payload=_json_dumps(payload or {}),
                confidence=0.5,
                evidence_count=1,
                enabled=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        session.commit()
        rule_id = int(row.id)

    try:
        emit_event(
            source="orchestrator_learning",
            event_type="learned_rule_updated",
            status="recorded",
            board_id=scope_id if normalized_scope == "board" else None,
            project_id=scope_id if normalized_scope == "project" else None,
            summary=f"Learned rule updated: {rule_key[:180]}",
            payload={
                "learned_rule_id": rule_id,
                "scope": normalized_scope,
                "scope_id": scope_id,
                "rule_type": rule_type,
                "evidence_count": row.evidence_count,
            },
        )
    except Exception:
        pass
    try:
        if normalized_scope == "board" and scope_id is not None:
            from distr.core.workspace_memory.references import sync_board_references

            sync_board_references(int(scope_id))
        elif normalized_scope == "project" and scope_id is not None:
            from distr.core.workspace_memory.references import sync_project_references

            sync_project_references(int(scope_id))
    except Exception:
        pass
    return rule_id


def record_ui_feedback_label(
    *,
    label: str,
    reason: str = "",
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
    execution_session_id: int | None = None,
    screenshot_paths: list[str] | None = None,
) -> int | None:
    """Record the user's UI approval/rejection label as orchestrator event and memory."""
    from distr.core.harness.ui_quality import build_feedback_summary, normalize_feedback_label

    normalized = normalize_feedback_label(label)
    approved = normalized == "approved"
    summary = build_feedback_summary(normalized, reason)
    payload = {
        "label": normalized,
        "approved": approved,
        "reason": reason or "",
        "screenshot_paths": screenshot_paths or [],
    }
    event_id = emit_event(
        source="ui_harness",
        event_type="ui_feedback_labeled",
        status="approved" if approved else "rejected",
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        board_id=board_id,
        project_id=project_id,
        execution_session_id=execution_session_id,
        summary=summary,
        payload=payload,
        evidence={"screenshots": screenshot_paths or []},
    )
    record_learning_signal(
        scope="board" if board_id else "project" if project_id else "global",
        scope_id=board_id or project_id,
        rule_type="ui_feedback",
        summary=summary,
        payload=payload,
    )
    try:
        from distr.core.workflow.standards_memory import capture_feedback_as_global_standard

        capture_feedback_as_global_standard(
            f"UI feedback {normalized}: {reason or summary}",
            category="ui_design_standard",
            source_type="ui_feedback",
            source_id=str(event_id or ""),
            project_id=project_id,
        )
    except Exception:
        pass
    return event_id


def record_routing_override(
    *,
    override: str,
    requested_backend: str,
    original_backend: str = "",
    final_backend: str = "",
    applied: bool | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
    reasons: list[str] | None = None,
) -> int | None:
    """Record an explicit routing override phrase as audit evidence and memory."""
    normalized = (override or "").strip().lower()
    requested = (requested_backend or "").strip().lower()
    original = (original_backend or "").strip().lower()
    final = (final_backend or "").strip().lower()
    if not normalized or not requested:
        return None

    was_applied = bool(final == requested) if applied is None else bool(applied)
    readable = normalized.replace("_", " ")
    summary = (
        f"Routing override '{readable}' requested {requested}"
        + (f" instead of {original}" if original else "")
        + (f"; final route {final}" if final else "")
        + "."
    )
    payload = {
        "override": normalized,
        "requested_backend": requested,
        "original_backend": original,
        "final_backend": final,
        "applied": was_applied,
        "reasons": reasons or [],
    }
    event_id = emit_event(
        source="harness_routing",
        event_type="routing_override_applied" if was_applied else "routing_override_ignored",
        status="applied" if was_applied else "ignored",
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        ticket_id=ticket_id,
        board_id=board_id,
        project_id=project_id,
        summary=summary,
        payload=payload,
    )
    record_learning_signal(
        scope="board" if board_id else "project" if project_id else "global",
        scope_id=board_id or project_id,
        rule_type="routing_override",
        summary=summary,
        payload=payload,
    )
    return event_id


def record_ui_quality_validation(
    *,
    artifacts: dict[str, Any] | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
    step_id: int | None = None,
    step_result_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
    execution_session_id: int | None = None,
    standards_context: str = "",
    baseline_set_id: int | None = None,
    baseline_name: str | None = None,
) -> int | None:
    """Record the UI screenshot and flow definition-of-done gate."""
    from distr.core.harness.ui_quality import compare_ui_artifacts_to_baseline, evaluate_ui_artifacts

    artifact_data = artifacts or {}
    selected_baseline_id = baseline_set_id
    if selected_baseline_id is None:
        raw_baseline_id = (
            artifact_data.get("visual_baseline_id")
            or artifact_data.get("baseline_set_id")
            or artifact_data.get("visual_baseline_set_id")
        )
        try:
            selected_baseline_id = int(raw_baseline_id) if raw_baseline_id not in (None, "") else None
        except Exception:
            selected_baseline_id = None
    selected_baseline_name = (
        baseline_name
        or artifact_data.get("visual_baseline_name")
        or artifact_data.get("baseline_name")
    )
    taste_summary = build_visual_taste_summary(board_id=board_id, project_id=project_id)
    evaluation = evaluate_ui_artifacts(
        artifacts,
        taste_summary=taste_summary,
        standards_context=standards_context,
    )
    baseline = get_visual_baseline_set(
        baseline_set_id=selected_baseline_id,
        name=selected_baseline_name,
        board_id=board_id,
        project_id=project_id,
    ) if selected_baseline_id or selected_baseline_name else None
    baseline_comparison = (
        compare_ui_artifacts_to_baseline(artifacts or {}, baseline)
        if baseline
        else None
    )
    missing = evaluation.get("missing") or []
    verdict = str(evaluation.get("verdict") or "fail")
    if baseline_comparison and baseline_comparison.get("verdict") != "pass":
        verdict = "fail"
    observed = (
        "All required UI quality artifacts are present."
        if verdict == "pass"
        else f"Missing UI quality artifacts: {', '.join(str(item) for item in missing)}"
    )
    if baseline_comparison and baseline_comparison.get("verdict") != "pass":
        explanation = baseline_comparison.get("explanation") or "Visual baseline comparison failed."
        observed = f"{observed} {explanation}".strip()
    snapshot = {
        "validation_type": "ui_quality",
        "expected": "UI work includes screenshots and flow evidence before completion.",
        "observed": observed,
        "caller_passed": verdict == "pass",
        "verified_passed": verdict == "pass",
        "verdict": verdict,
        "artifacts": artifacts or {},
        "missing": missing,
    }
    if baseline_comparison:
        snapshot["visual_baseline"] = baseline_comparison
    enriched_standards_context = standards_context or ""
    taste_context = build_visual_taste_context(board_id=board_id, project_id=project_id)
    if taste_context and "[VISUAL TASTE MEMORY]" not in enriched_standards_context:
        enriched_standards_context = (
            enriched_standards_context.rstrip() + "\n\n" + taste_context
        ).strip()
    if enriched_standards_context:
        snapshot["standards_context"] = enriched_standards_context
    return record_validation(
        workflow_id=workflow_id,
        run_id=run_id,
        step_id=step_id,
        step_result_id=step_result_id,
        ticket_id=ticket_id,
        board_id=board_id,
        project_id=project_id,
        execution_session_id=execution_session_id,
        validation_snapshot=snapshot,
        standards_context=enriched_standards_context,
        correction_hint=(
            ""
            if verdict == "pass"
            else "Attach before/after screenshots, compare against the selected visual baseline, explicitly assess the result against learned global user standards, and include a concise happy-path flow summary before marking UI work complete."
        ),
        payload={
            "ui_quality": evaluation,
            "visual_baseline": baseline_comparison,
            "visual_taste": taste_summary,
        },
    )


def _baseline_scope(board_id: int | None = None, project_id: int | None = None) -> tuple[str, int | None]:
    if board_id is not None:
        return "board", int(board_id)
    if project_id is not None:
        return "project", int(project_id)
    return "global", None


def _baseline_storage_slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-._")
    return slug or fallback


def _store_visual_baseline_screenshot(
    *,
    source_path: str,
    storage_dir: str | Path | None,
    baseline_id: int,
    baseline_name: str,
    screen_name: str,
    screen_index: int,
) -> str:
    source = Path(source_path).expanduser()
    if not source.exists() or not source.is_file():
        raise ValueError(f"baseline screenshot file not found: {source_path}")
    root = Path(storage_dir or os.getenv("ORCHESTRATOR_VISUAL_BASELINE_DIR") or "data/orchestrator/visual_baselines").expanduser()
    baseline_slug = _baseline_storage_slug(baseline_name, f"baseline-{baseline_id}")
    screen_slug = _baseline_storage_slug(screen_name, f"screen-{screen_index + 1}")
    extension = source.suffix.lower() or ".png"
    destination_dir = root / f"{baseline_id}-{baseline_slug}"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{screen_index + 1:02d}-{screen_slug}{extension}"
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return str(destination)


def create_visual_baseline_set(
    *,
    name: str,
    screens: list[dict[str, Any]],
    board_id: int | None = None,
    project_id: int | None = None,
    description: str = "",
    version: str = "v1",
    copy_screenshots: bool = False,
    storage_dir: str | Path | None = None,
) -> int:
    """Create a named orchestrator visual baseline set with reference screenshots."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("baseline name is required")
    if not screens:
        raise ValueError("at least one baseline screen is required")
    scope, scope_id = _baseline_scope(board_id=board_id, project_id=project_id)
    ensure_orchestrator_tables()
    with get_session() as session:
        row = OrchestratorVisualBaselineSet(
            name=clean_name,
            scope=scope,
            scope_id=scope_id,
            description=description or "",
            version=(version or "v1").strip() or "v1",
            enabled=1,
        )
        session.add(row)
        session.flush()
        for index, screen in enumerate(screens):
            screen_name = str(screen.get("screen_name") or screen.get("name") or "").strip()
            screenshot_path = str(screen.get("screenshot_path") or screen.get("path") or "").strip()
            if not screen_name or not screenshot_path:
                raise ValueError("baseline screens require screen_name and screenshot_path")
            metadata = screen.get("metadata") or {}
            if copy_screenshots:
                metadata = {**metadata, "source_screenshot_path": screenshot_path}
                screenshot_path = _store_visual_baseline_screenshot(
                    source_path=screenshot_path,
                    storage_dir=storage_dir,
                    baseline_id=int(row.id),
                    baseline_name=clean_name,
                    screen_name=screen_name,
                    screen_index=index,
                )
            session.add(OrchestratorVisualBaselineScreen(
                baseline_set_id=int(row.id),
                screen_name=screen_name,
                screenshot_path=screenshot_path,
                flow_name=str(screen.get("flow_name") or "").strip() or None,
                notes=str(screen.get("notes") or "").strip() or None,
                metadata_json=_json_dumps(metadata),
            ))
        session.commit()
        baseline_id = int(row.id)
    emit_event(
        source="ui_harness",
        event_type="visual_baseline_created",
        status="created",
        board_id=board_id,
        project_id=project_id,
        summary=f"Visual baseline '{clean_name}' created with {len(screens)} reference screen(s).",
        payload={"baseline_set_id": baseline_id, "name": clean_name, "screen_count": len(screens)},
    )
    return baseline_id


def upsert_visual_baseline_screens(
    *,
    name: str,
    screens: list[dict[str, Any]],
    board_id: int | None = None,
    project_id: int | None = None,
    description: str = "",
    version: str = "v1",
    copy_screenshots: bool = False,
    storage_dir: str | Path | None = None,
) -> int:
    """Create a visual baseline set or add/replace screens in an existing named set."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("baseline name is required")
    if not screens:
        raise ValueError("at least one baseline screen is required")
    scope, scope_id = _baseline_scope(board_id=board_id, project_id=project_id)
    ensure_orchestrator_tables()
    with get_session() as session:
        query = (
            session.query(OrchestratorVisualBaselineSet)
            .filter(OrchestratorVisualBaselineSet.enabled == 1)
            .filter(OrchestratorVisualBaselineSet.name == clean_name)
            .filter(OrchestratorVisualBaselineSet.scope == scope)
        )
        if scope_id is None:
            query = query.filter(OrchestratorVisualBaselineSet.scope_id.is_(None))
        else:
            query = query.filter(OrchestratorVisualBaselineSet.scope_id == scope_id)
        row = query.order_by(OrchestratorVisualBaselineSet.updated_at.desc(), OrchestratorVisualBaselineSet.id.desc()).first()
        if not row:
            row = OrchestratorVisualBaselineSet(
                name=clean_name,
                scope=scope,
                scope_id=scope_id,
                description=description or "",
                version=(version or "v1").strip() or "v1",
                enabled=1,
            )
            session.add(row)
            session.flush()
        else:
            if description:
                row.description = description
            row.version = (version or row.version or "v1").strip() or "v1"

        baseline_id = int(row.id)
        existing_by_name = {
            str(screen.screen_name or "").strip().lower(): screen
            for screen in session.query(OrchestratorVisualBaselineScreen)
            .filter(OrchestratorVisualBaselineScreen.baseline_set_id == baseline_id)
            .all()
        }
        existing_count = len(existing_by_name)
        upserted = 0
        for index, screen in enumerate(screens):
            screen_name = str(screen.get("screen_name") or screen.get("name") or "").strip()
            screenshot_path = str(screen.get("screenshot_path") or screen.get("path") or "").strip()
            if not screen_name or not screenshot_path:
                raise ValueError("baseline screens require screen_name and screenshot_path")
            metadata = screen.get("metadata") or {}
            if copy_screenshots:
                metadata = {**metadata, "source_screenshot_path": screenshot_path}
                screenshot_path = _store_visual_baseline_screenshot(
                    source_path=screenshot_path,
                    storage_dir=storage_dir,
                    baseline_id=baseline_id,
                    baseline_name=clean_name,
                    screen_name=screen_name,
                    screen_index=existing_count + index,
                )
            existing = existing_by_name.get(screen_name.lower())
            if existing:
                existing.screenshot_path = screenshot_path
                existing.flow_name = str(screen.get("flow_name") or "").strip() or None
                existing.notes = str(screen.get("notes") or "").strip() or None
                existing.metadata_json = _json_dumps(metadata)
            else:
                session.add(OrchestratorVisualBaselineScreen(
                    baseline_set_id=baseline_id,
                    screen_name=screen_name,
                    screenshot_path=screenshot_path,
                    flow_name=str(screen.get("flow_name") or "").strip() or None,
                    notes=str(screen.get("notes") or "").strip() or None,
                    metadata_json=_json_dumps(metadata),
                ))
            upserted += 1
        session.commit()
    emit_event(
        source="ui_harness",
        event_type="visual_baseline_upserted",
        status="updated",
        board_id=board_id,
        project_id=project_id,
        summary=f"Visual baseline '{clean_name}' updated with {upserted} reference screen(s).",
        payload={"baseline_set_id": baseline_id, "name": clean_name, "screen_count": upserted},
    )
    return baseline_id


def serialize_visual_baseline_set(row: OrchestratorVisualBaselineSet, screens: list[OrchestratorVisualBaselineScreen]) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "name": row.name,
        "scope": row.scope,
        "scope_id": row.scope_id,
        "description": row.description or "",
        "version": row.version or "v1",
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "screens": [
            {
                "id": int(screen.id),
                "screen_name": screen.screen_name,
                "screenshot_path": screen.screenshot_path,
                "flow_name": screen.flow_name,
                "notes": screen.notes or "",
                "metadata": _json_loads(screen.metadata_json) or {},
            }
            for screen in screens
        ],
    }


def get_visual_baseline_set(
    *,
    baseline_set_id: int | None = None,
    name: str | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
) -> dict[str, Any] | None:
    """Return one visual baseline set, preferring an explicit id."""
    ensure_orchestrator_tables()
    with get_session() as session:
        query = session.query(OrchestratorVisualBaselineSet).filter(OrchestratorVisualBaselineSet.enabled == 1)
        if baseline_set_id is not None:
            query = query.filter(OrchestratorVisualBaselineSet.id == int(baseline_set_id))
        else:
            scope, scope_id = _baseline_scope(board_id=board_id, project_id=project_id)
            query = query.filter(OrchestratorVisualBaselineSet.scope == scope)
            if scope_id is None:
                query = query.filter(OrchestratorVisualBaselineSet.scope_id.is_(None))
            else:
                query = query.filter(OrchestratorVisualBaselineSet.scope_id == scope_id)
            if name:
                query = query.filter(OrchestratorVisualBaselineSet.name == str(name).strip())
        row = query.order_by(OrchestratorVisualBaselineSet.updated_at.desc(), OrchestratorVisualBaselineSet.id.desc()).first()
        if not row:
            return None
        screens = (
            session.query(OrchestratorVisualBaselineScreen)
            .filter(OrchestratorVisualBaselineScreen.baseline_set_id == int(row.id))
            .order_by(OrchestratorVisualBaselineScreen.id.asc())
            .all()
        )
        return serialize_visual_baseline_set(row, screens)


def list_visual_baseline_sets(
    *,
    board_id: int | None = None,
    project_id: int | None = None,
    include_global: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List enabled visual baseline sets for a scope."""
    ensure_orchestrator_tables()
    scope, scope_id = _baseline_scope(board_id=board_id, project_id=project_id)
    with get_session() as session:
        query = session.query(OrchestratorVisualBaselineSet).filter(OrchestratorVisualBaselineSet.enabled == 1)
        if include_global and scope != "global":
            query = query.filter(
                (OrchestratorVisualBaselineSet.scope == scope) & (OrchestratorVisualBaselineSet.scope_id == scope_id)
                | ((OrchestratorVisualBaselineSet.scope == "global") & OrchestratorVisualBaselineSet.scope_id.is_(None))
            )
        else:
            query = query.filter(OrchestratorVisualBaselineSet.scope == scope)
            if scope_id is None:
                query = query.filter(OrchestratorVisualBaselineSet.scope_id.is_(None))
            else:
                query = query.filter(OrchestratorVisualBaselineSet.scope_id == scope_id)
        rows = (
            query.order_by(OrchestratorVisualBaselineSet.updated_at.desc(), OrchestratorVisualBaselineSet.id.desc())
            .limit(max(1, min(int(limit or 50), 200)))
            .all()
        )
        result = []
        for row in rows:
            screens = (
                session.query(OrchestratorVisualBaselineScreen)
                .filter(OrchestratorVisualBaselineScreen.baseline_set_id == int(row.id))
                .order_by(OrchestratorVisualBaselineScreen.id.asc())
                .all()
            )
            result.append(serialize_visual_baseline_set(row, screens))
        return result


def inspect_visual_baseline_readiness(
    *,
    baseline_set_id: int | None = None,
    name: str | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
    include_global: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Report whether enabled visual baseline screenshot paths are present on disk."""
    if baseline_set_id is not None or name:
        baseline = get_visual_baseline_set(
            baseline_set_id=baseline_set_id,
            name=name,
            board_id=board_id,
            project_id=project_id,
        )
        baselines = [baseline] if baseline else []
    else:
        baselines = list_visual_baseline_sets(
            board_id=board_id,
            project_id=project_id,
            include_global=include_global,
            limit=limit,
        )

    checked_baselines: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    screen_count = 0
    existing_screen_count = 0

    for baseline in baselines:
        checked_screens: list[dict[str, Any]] = []
        for screen in baseline.get("screens") or []:
            screen_count += 1
            screenshot_path = str(screen.get("screenshot_path") or "").strip()
            exists = bool(screenshot_path and Path(screenshot_path).exists())
            if exists:
                existing_screen_count += 1
            checked = {
                "baseline_set_id": baseline.get("id"),
                "baseline_name": baseline.get("name"),
                "screen_name": screen.get("screen_name"),
                "screenshot_path": screenshot_path,
                "exists": exists,
            }
            checked_screens.append({**screen, "exists": exists})
            if not exists:
                missing.append(checked)
        checked_baselines.append({**baseline, "screens": checked_screens})

    ready = bool(baselines) and screen_count > 0 and not missing
    return {
        "verdict": "pass" if ready else "fail",
        "ready": ready,
        "baseline_count": len(baselines),
        "screen_count": screen_count,
        "existing_screen_count": existing_screen_count,
        "missing_screen_count": len(missing),
        "missing": missing,
        "baselines": checked_baselines,
    }


def list_learned_rules(
    *,
    scope: str | None = None,
    scope_id: int | None = None,
    board_id: int | None = None,
    enabled_only: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ensure_orchestrator_tables()
    with get_session() as session:
        query = session.query(OrchestratorLearnedRule)
        if enabled_only:
            query = query.filter(OrchestratorLearnedRule.enabled == 1)
        if board_id is not None:
            query = query.filter(OrchestratorLearnedRule.scope == "board").filter(
                OrchestratorLearnedRule.scope_id == int(board_id)
            )
        elif scope:
            query = query.filter(OrchestratorLearnedRule.scope == str(scope).lower())
            if scope_id is not None:
                query = query.filter(OrchestratorLearnedRule.scope_id == int(scope_id))
        rows = (
            query.order_by(OrchestratorLearnedRule.evidence_count.desc(), OrchestratorLearnedRule.updated_at.desc())
            .limit(max(1, min(int(limit or 50), 200)))
            .all()
        )
        return [serialize_learned_rule(row) for row in rows]


def build_visual_taste_summary(
    *,
    board_id: int | None = None,
    project_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Aggregate approved/rejected UI feedback labels into reusable taste memory."""
    scope = "board" if board_id is not None else "project" if project_id is not None else "global"
    scope_id = int(board_id if board_id is not None else project_id) if (board_id is not None or project_id is not None) else None
    rules = list_learned_rules(
        board_id=int(board_id) if board_id is not None else None,
        scope="project" if project_id is not None and board_id is None else ("global" if scope == "global" else None),
        scope_id=scope_id if scope != "board" else None,
        enabled_only=True,
        limit=limit,
    )
    labels: dict[str, dict[str, Any]] = {}
    approval_count = 0
    rejection_count = 0
    total_feedback = 0

    for rule in rules:
        if str(rule.get("rule_type") or "") != "ui_feedback":
            continue
        payload = rule.get("payload") if isinstance(rule.get("payload"), dict) else {}
        latest = payload.get("latest") if isinstance(payload.get("latest"), dict) else {}
        if not latest and payload.get("label"):
            latest = payload
        label = str(latest.get("label") or "").strip() or "rejected_other"
        count = max(1, int(rule.get("evidence_count") or 1))
        approved = bool(latest.get("approved")) or label == "approved"
        total_feedback += count
        if approved:
            approval_count += count
        else:
            rejection_count += count
        bucket = labels.setdefault(
            label,
            {
                "count": 0,
                "approved": approved,
                "recent_reasons": [],
                "screenshot_paths": [],
            },
        )
        bucket["count"] = int(bucket.get("count") or 0) + count
        reason = str(latest.get("reason") or "").strip()
        if reason and reason not in bucket["recent_reasons"]:
            bucket["recent_reasons"].append(reason)
        for path in latest.get("screenshot_paths") or []:
            path_text = str(path or "").strip()
            if path_text and path_text not in bucket["screenshot_paths"]:
                bucket["screenshot_paths"].append(path_text)

    for bucket in labels.values():
        bucket["recent_reasons"] = bucket["recent_reasons"][:5]
        bucket["screenshot_paths"] = bucket["screenshot_paths"][:5]

    return {
        "scope": scope,
        "scope_id": scope_id,
        "total_feedback": total_feedback,
        "approval_count": approval_count,
        "rejection_count": rejection_count,
        "labels": labels,
    }


def build_visual_taste_context(
    *,
    board_id: int | None = None,
    project_id: int | None = None,
    limit: int = 100,
) -> str:
    """Format visual taste memory for future UI planning and validation prompts."""
    summary = build_visual_taste_summary(board_id=board_id, project_id=project_id, limit=limit)
    if not summary.get("total_feedback"):
        return ""
    lines = [
        "[VISUAL TASTE MEMORY]",
        f"- Feedback seen: {summary['total_feedback']} labels ({summary['approval_count']} approved, {summary['rejection_count']} rejected).",
    ]
    labels = sorted(
        summary.get("labels", {}).items(),
        key=lambda item: (-int(item[1].get("count") or 0), item[0]),
    )
    for label, data in labels[:8]:
        status = "approved" if data.get("approved") else "rejected"
        readable_label = str(label).replace("_", " ")
        lines.append(f"- {readable_label} ({status}, seen {int(data.get('count') or 0)}x)")
        for reason in (data.get("recent_reasons") or [])[:2]:
            lines.append(f"  - {str(reason)[:240]}")
    return "\n".join(lines).strip()


def serialize_learned_rule(row: OrchestratorLearnedRule) -> dict[str, Any]:
    return {
        "id": row.id,
        "scope": row.scope,
        "scope_id": row.scope_id,
        "rule_type": row.rule_type,
        "summary": row.summary or "",
        "payload": _json_loads(row.payload) or {},
        "confidence": float(row.confidence or 0),
        "evidence_count": int(row.evidence_count or 0),
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_board_activity(
    board_id: int,
    *,
    event_limit: int = 50,
    rule_limit: int = 20,
) -> dict[str, Any]:
    return {
        "board_id": int(board_id),
        "events": list_events(board_id=int(board_id), limit=event_limit),
        "validations": list_validation_records(board_id=int(board_id), limit=min(event_limit, 30)),
        "learned_rules": list_learned_rules(board_id=int(board_id), limit=rule_limit),
        "visual_taste": build_visual_taste_summary(board_id=int(board_id), limit=200),
    }


def list_project_activity(
    project_id: int,
    *,
    event_limit: int = 50,
    rule_limit: int = 20,
) -> dict[str, Any]:
    """Return the orchestration activity bundle for a project."""
    pid = int(project_id)
    return {
        "project_id": pid,
        "events": list_events(project_id=pid, limit=event_limit),
        "validations": list_validation_records(project_id=pid, limit=min(event_limit, 30)),
        "learned_rules": list_learned_rules(scope="project", scope_id=pid, limit=rule_limit),
        "visual_taste": build_visual_taste_summary(project_id=pid, limit=200),
        "runtime_sessions": list_project_runtime_sessions(project_id=pid, limit=10),
    }


def parse_board_orchestrator_policy(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def normalize_board_orchestrator_policy(raw: str | dict | None) -> dict[str, Any]:
    """Return a normalized board orchestrator policy with known keys and defaults."""
    parsed = raw if isinstance(raw, dict) else parse_board_orchestrator_policy(raw if isinstance(raw, str) else None)
    routing_mode = str(parsed.get("routing_mode") or "hybrid").strip().lower()
    if routing_mode not in {"hybrid", "policy", "llm"}:
        routing_mode = "hybrid"
    return {
        "complexity_routing": parsed.get("complexity_routing") or {},
        "routing_mode": routing_mode,
        "require_approval_for_override": bool(parsed.get("require_approval_for_override", True)),
        "prefer_ide_above_complexity": str(parsed.get("prefer_ide_above_complexity") or "").strip().lower(),
        "harness_preferences": parsed.get("harness_preferences") or {},
        "promoted_hints": parsed.get("promoted_hints") or {},
    }


def promote_learned_rule_to_board_policy(
    *,
    board_id: int,
    rule_id: int,
    category: str = "general",
) -> dict[str, Any]:
    """Add a learned rule summary as a draft harness preference (human-approved)."""
    rules = list_learned_rules(board_id=int(board_id), enabled_only=False, limit=200)
    match = next((row for row in rules if int(row.get("id") or 0) == int(rule_id)), None)
    if not match:
        raise ValueError("Learned rule not found for board")

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard

    with get_session() as session:
        board = session.query(KanbanBoard).filter(KanbanBoard.id == int(board_id)).first()
        if not board:
            raise ValueError("Board not found")
        policy = normalize_board_orchestrator_policy(getattr(board, "orchestrator_policy", None))
        hints = policy.get("promoted_hints") or {}
        if not isinstance(hints, dict):
            hints = {}
        key = str(category or "general").strip().lower() or "general"
        hints[key] = {
            "rule_id": int(rule_id),
            "summary": str(match.get("summary") or "").strip(),
            "rule_type": str(match.get("rule_type") or "").strip(),
            "suggested_backend": (match.get("payload") or {}).get("suggested_backend"),
            "enabled": False,
        }
        policy["promoted_hints"] = hints
        board.orchestrator_policy = json.dumps(policy, ensure_ascii=False)
        session.commit()
        return policy


def build_learned_rules_context(board_id: int | None, *, limit: int = 8) -> str:
    """Format enabled board learned rules for validation and planning context."""
    if not board_id:
        return ""
    rules = list_learned_rules(board_id=int(board_id), enabled_only=True, limit=limit)
    if not rules:
        return ""
    lines = ["[BOARD LEARNED RULES]"]
    for rule in rules:
        summary = str(rule.get("summary") or "").strip()
        if not summary:
            continue
        evidence = int(rule.get("evidence_count") or 0)
        rule_type = str(rule.get("rule_type") or "rule").replace("_", " ")
        prefix = f"- ({rule_type}, seen {evidence}x)" if evidence > 1 else f"- ({rule_type})"
        lines.append(f"{prefix} {summary[:500]}")
    return "\n".join(lines).strip() if len(lines) > 1 else ""


def set_learned_rule_enabled(rule_id: int, enabled: bool) -> bool:
    ensure_orchestrator_tables()
    with get_session() as session:
        row = session.query(OrchestratorLearnedRule).filter(OrchestratorLearnedRule.id == int(rule_id)).first()
        if not row:
            return False
        row.enabled = 1 if enabled else 0
        row.updated_at = datetime.utcnow()
        session.commit()
        return True
