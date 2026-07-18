"""Runtime contract helpers for workflow step execution.

The workflow runner should be driven by one active step at a time. These
helpers keep preflight and current-step activity generic so the runner does not
need project-specific branching in the UI or dispatcher.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from distr.core import db as db_module
from distr.core.db.workflow import AutoWorkflowRun


ACTIVITY_NOISE_TYPES = {
    "run_started",
    "user_notified",
}

MISSION_CONTROL_LIFECYCLE_TYPES = {
    "workflow_step_started",
    "workflow_step_preflight",
    "route_decided",
    "provider_failover",
    "validation_recorded",
    "workflow_step_completed",
    "workflow_run_started",
    "workflow_run_completed",
    "workflow_run_cancelled",
    "approval_requested",
    "route_approval_requested",
    "route_approval_granted",
    "route_approval_rejected",
}

MISSION_CONTROL_NOISE_TYPES = {
    "execution_message_start",
    "execution_message_end",
    "execution_command_start",
    "execution_agent_start",
    "execution_agent_end",
    "skill_provisioned",
    "execution_session_created",
    "execution_preflight",
    "backend_handoff_created",
    "backend_handoff_updated",
    "project_runtime_snapshot",
    "execution_executor_start",
}


def get_session():
    """Resolve the active session provider at call time, while remaining patchable."""
    return db_module.get_session()


def _json_dict(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}") or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _event_subtype(event: dict[str, Any]) -> str:
    return str(event.get("subtype") or event.get("legacy_event_type") or event.get("event_type") or "").strip().lower()


def _compact_mission_control_event(event: dict[str, Any]) -> dict[str, Any]:
    """Remove large prompt/output bodies while retaining decision evidence."""
    item = dict(event)
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    keep_payload = {
        key: payload[key]
        for key in (
            "step_name",
            "action_type",
            "position",
            "summary",
            "decision",
            "step_role",
            "auto_detected",
            "correction_hint",
            "iteration",
            "loop_iteration",
            "backend_id",
            "route_backend",
            "model",
            "skills",
            "tools",
            "context",
        )
        if key in payload
    }
    compact_evidence: dict[str, Any] = {}
    validation = evidence.get("validation") if isinstance(evidence.get("validation"), dict) else {}
    if validation:
        compact_evidence["validation"] = {
            key: validation[key]
            for key in (
                "step_id",
                "step_name",
                "validation_type",
                "expected",
                "verdict",
                "correction_hint",
            )
            if key in validation
        }
    preview = str(evidence.get("result_preview") or "").strip()
    if preview:
        compact_evidence["result_preview"] = preview[:700] + ("…" if len(preview) > 700 else "")
    error = str(evidence.get("error") or "").strip()
    if error:
        compact_evidence["error"] = error[:500] + ("…" if len(error) > 500 else "")
    item["payload"] = keep_payload
    item["evidence"] = compact_evidence
    return item


def _bound_transcript_value(value: Any, *, depth: int = 0) -> Any:
    """Bound a redacted event for browser inspection without hiding its shape."""
    if depth >= 8:
        return "[nested data omitted]"
    if isinstance(value, dict):
        return {
            str(key): _bound_transcript_value(item, depth=depth + 1)
            for key, item in list(value.items())[:120]
        }
    if isinstance(value, (list, tuple)):
        items = [_bound_transcript_value(item, depth=depth + 1) for item in list(value)[:200]]
        if len(value) > 200:
            items.append(f"[{len(value) - 200} additional items omitted]")
        return items
    if isinstance(value, str) and len(value) > 24000:
        return value[:24000] + f"\n[… {len(value) - 24000} additional characters omitted]"
    return value


def detailed_execution_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a safe, inspectable execution transcript for the run side panel.

    Mission Control's normal poll intentionally removes transport noise and
    large prompt/output bodies. This projection is loaded on demand and keeps
    the complete event sequence, including handoffs, tools, commands and
    worker output, while redacting credentials and bounding pathological data.
    """
    from distr.core.orchestrator import redact_handoff_payload

    transcript: list[dict[str, Any]] = []
    for event in events:
        redacted = redact_handoff_payload(event)
        if not isinstance(redacted, dict):
            continue
        transcript.append(_bound_transcript_value(redacted))
    return transcript


def mission_control_timeline(
    events: list[dict[str, Any]],
    *,
    current_step_id: int | None,
    current_activity_limit: int = 40,
) -> list[dict[str, Any]]:
    """Return the complete step story plus compact current-step activity.

    A run can emit hundreds of heartbeat and transport events. Returning only
    the newest N records hides early completed steps; returning every raw event
    sends large prompts and validation output to the browser every few seconds.
    This projection preserves every lifecycle decision while keeping only the
    useful tail of current-step activity.
    """
    lifecycle: list[dict[str, Any]] = []
    current_activity: list[dict[str, Any]] = []
    latest_heartbeat: dict[str, Any] | None = None
    lifecycle_ids: set[int] = set()

    for event in events:
        subtype = _event_subtype(event)
        event_id = int(event.get("id") or 0)
        if subtype in MISSION_CONTROL_LIFECYCLE_TYPES:
            lifecycle.append(_compact_mission_control_event(event))
            if event_id:
                lifecycle_ids.add(event_id)
            continue
        if current_step_id and event.get("step_id") not in (None, current_step_id):
            continue
        if subtype == "execution_heartbeat":
            latest_heartbeat = _compact_mission_control_event(event)
            continue
        if subtype in MISSION_CONTROL_NOISE_TYPES:
            continue
        current_activity.append(_compact_mission_control_event(event))

    current_activity = current_activity[-max(1, int(current_activity_limit or 40)):]
    if latest_heartbeat:
        current_activity.append(latest_heartbeat)
    merged = lifecycle + [event for event in current_activity if int(event.get("id") or 0) not in lifecycle_ids]
    merged.sort(key=lambda event: (str(event.get("created_at") or ""), int(event.get("id") or 0)))
    return merged


def run_human_checkpoints_enabled(run_id: int | None) -> bool:
    """Return whether human checkpoints are explicitly enabled for this run."""
    if run_id is None:
        return False
    try:
        from distr.core.workflow.run_briefing import human_checkpoint_enabled

        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
            if not run:
                return False
            return bool(human_checkpoint_enabled(_json_dict(run.run_data)))
    except Exception:
        return False


def should_pause_after_step(*, run_id: int | None, step_wait_for_continue: bool, skip_wait: bool = False) -> bool:
    """Legacy step wait flags are honored only when checkpoints are opt-in."""
    if skip_wait or not step_wait_for_continue:
        return False
    return run_human_checkpoints_enabled(run_id)


def emit_step_activity(
    *,
    run_id: int,
    step_id: int,
    event_type: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> int | None:
    """Append a compact current-step activity event to the orchestrator ledger."""
    try:
        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
            if not run:
                return None
            workflow_id = int(run.workflow_id)
            ticket_id = run.ticket_id
            board_id = run.board_id
            run_data = _json_dict(run.run_data)
            project_raw = run_data.get("project_id")
            project_id = int(project_raw) if str(project_raw or "").isdigit() else None
    except Exception:
        return None

    try:
        from distr.core.orchestration_events import emit_orchestration_event

        return emit_orchestration_event(
            source="workflow",
            event_type=event_type,
            status=status,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            ticket_id=ticket_id,
            board_id=board_id,
            project_id=project_id,
            summary=summary,
            payload=payload or {},
            evidence=evidence or {},
        )
    except Exception:
        return None


def _check(name: str, ok: bool, message: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "message": message, **extra}


def build_step_preflight(step_data: dict[str, Any], run_id: int | None) -> dict[str, Any]:
    """Return route/tool readiness checks for a step without executing it."""
    action_type = str(step_data.get("action_type") or "agent_instruction").strip()
    config = step_data.get("config") if isinstance(step_data.get("config"), dict) else {}
    checks: list[dict[str, Any]] = []

    if action_type == "run_command":
        command = str(config.get("command") or step_data.get("code") or step_data.get("instruction") or "").strip()
        checks.append(_check("command", bool(command), "Command configured." if command else "No command configured."))
        cwd = str(config.get("working_directory") or "").strip()
        if cwd:
            path = Path(cwd).expanduser()
            checks.append(_check("working_directory", path.is_dir(), f"Working directory: {path}"))

    elif action_type in {"execute_code", "playwright", "browser_use"}:
        has_code_or_instruction = bool(
            str(config.get("code") or step_data.get("code") or step_data.get("instruction") or "").strip()
        )
        checks.append(_check("code_or_instruction", has_code_or_instruction, "Code/instruction configured." if has_code_or_instruction else "No code or instruction configured."))

    elif action_type == "http_request":
        url = str(config.get("url") or "").strip()
        checks.append(_check("url", bool(url), f"URL configured: {url}" if url else "No URL configured."))

    elif action_type == "send_to_project_cli":
        _add_project_cli_preflight(checks, step_data, config, run_id)

    elif action_type in {"play_recording", "decision_action"}:
        has_action = bool(config.get("action_id") or config.get("recording_id") or step_data.get("action_id") or config.get("recording_name") or step_data.get("recording_filename"))
        checks.append(_check("action", has_action, "Action/recording configured." if has_action else "No action or recording selected."))

    else:
        instruction = str(step_data.get("instruction") or "").strip()
        checks.append(_check("instruction", bool(instruction), "Instruction configured." if instruction else "No instruction configured."))

    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "action_type": action_type,
        "checks": checks,
        "summary": "Preflight passed." if ok else "Preflight failed.",
    }


def _add_project_cli_preflight(
    checks: list[dict[str, Any]],
    step_data: dict[str, Any],
    config: dict[str, Any],
    run_id: int | None,
) -> None:
    instruction = str(config.get("instruction") or step_data.get("instruction") or "").strip()
    checks.append(_check("instruction", bool(instruction), "Instruction configured." if instruction else "No instruction configured for project CLI."))

    run_data: dict[str, Any] = {}
    if run_id is not None:
        try:
            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                run_data = _json_dict(run.run_data) if run else {}
        except Exception:
            run_data = {}

    project_folder = str(run_data.get("project_folder") or "").strip()
    project_id = run_data.get("project_id")
    if project_folder:
        folder = Path(project_folder).expanduser()
        checks.append(_check("worktree", folder.is_dir(), f"Project folder: {folder}", project_id=project_id))
    else:
        checks.append(_check("worktree", bool(project_id), "Project context selected." if project_id else "No linked project/worktree for this run.", project_id=project_id))

    route = config.get("execution_route") if isinstance(config.get("execution_route"), dict) else {}
    snapshot = route.get("route_snapshot") if isinstance(route.get("route_snapshot"), dict) else {}
    active_route = run_data.get("execution_route") if isinstance(run_data.get("execution_route"), dict) else {}
    backend_id = str(
        config.get("backend_id")
        or snapshot.get("backend_id")
        or active_route.get("backend")
        or ""
    ).strip()
    if not backend_id:
        backend_id = "pi"

    try:
        from distr.core.project_cli_backends import get_backend, normalize_backend_id

        normalized = normalize_backend_id(backend_id)
        status = get_backend(normalized).setup_status()
        checks.append(_check(
            "backend",
            bool(status.ready),
            status.message or f"{normalized} readiness checked.",
            backend=normalized,
            state=status.state,
            setup_instructions=status.setup_instructions,
        ))
    except Exception as exc:
        checks.append(_check("backend", False, f"Could not check backend readiness: {exc}", backend=backend_id))


def current_step_activity(
    *,
    workflow_id: int,
    run_id: int,
    limit: int = 60,
) -> dict[str, Any]:
    """Return useful activity for the active step only."""
    from distr.core.orchestration_events import list_orchestration_timeline

    with get_session() as db:
        run = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.id == int(run_id))
            .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
            .first()
        )
        if not run:
            return {"success": False, "error": "Run not found"}
        current_step_id = int(run.current_step_id or 0) if run.current_step_id else None
        run_status = run.status
        run_data = _json_dict(run.run_data)

    timeline = list_orchestration_timeline(workflow_id=workflow_id, run_id=run_id, limit=max(limit * 3, 100))
    events = []
    for event in timeline:
        if current_step_id and event.get("step_id") not in (None, current_step_id):
            continue
        event_type = str(event.get("event_type") or "")
        legacy = str(event.get("legacy_event_type") or "")
        subtype = str(event.get("subtype") or "")
        if event_type in ACTIVITY_NOISE_TYPES or legacy in ACTIVITY_NOISE_TYPES or subtype in ACTIVITY_NOISE_TYPES:
            continue
        if event_type == "worker_progress" and subtype in {"workflow_run_started", "user_notified"}:
            continue
        events.append(event)
        if len(events) >= limit:
            break

    placeholder = ""
    if not events:
        route = run_data.get("execution_route") if isinstance(run_data.get("execution_route"), dict) else {}
        backend = route.get("backend") or "route pending"
        model = route.get("model") or "model pending"
        placeholder = f"Current step is {run_status}; preflight/route activity will appear here. Backend: {backend}; model: {model}."

    return {
        "success": True,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "current_step_id": current_step_id,
        "run_status": run_status,
        "events": events,
        "placeholder": placeholder,
    }
