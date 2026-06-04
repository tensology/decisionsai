"""
Workflow Scheduler

Runs scheduled workflows when due. Called periodically by the main app (QTimer).
Ported from distr/core/step_runner/scheduler.py to operate on AutoWorkflow models.
"""

import logging
import json
import time as _time
from datetime import datetime, timedelta
from typing import Any, List, Optional, Callable

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun

logger = logging.getLogger(__name__)

# Preset schedules -> cron (default times)
SCHEDULE_PRESETS = {
    "hourly": "0 * * * *",       # Every hour at :00
    "daily": "0 9 * * *",        # 9am daily
    "weekly": "0 9 * * 1",       # Monday 9am
}


def schedule_to_cron(
    schedule: Optional[str],
    schedule_time: Optional[str] = None,
    timezone: Optional[str] = None,
    schedule_days: Optional[str] = None,
) -> Optional[str]:
    """
    Convert preset or schedule string to cron.
    Supports: "daily", "hourly", "weekly", "daily:08:00", "0 9 * * *"
    schedule_days: for weekly, comma-separated cron weekdays "1,3,5" = Mon, Wed, Fri
    """
    if not schedule or not schedule.strip():
        return None
    s = schedule.strip().lower()
    # daily:08:00 or daily@08:00 (legacy)
    if ":" in s or "@" in s:
        sep = ":" if ":" in s else "@"
        parts = s.split(sep, 1)
        preset = parts[0].strip()
        time_str = parts[1].strip() if len(parts) > 1 else None
        if preset in SCHEDULE_PRESETS and time_str:
            try:
                h, m = time_str.split(":")[:2]
                h, m = int(h), int(m)
                if preset == "daily":
                    return f"{m} {h} * * *"
                if preset == "weekly":
                    return f"{m} {h} * * 1"
            except (ValueError, IndexError):
                pass
    if s == "hourly":
        return SCHEDULE_PRESETS["hourly"]
    if s == "once":
        run_at = (schedule_time or "").strip()
        return f"once:{run_at}" if run_at else None
    if s == "daily":
        time_str = (schedule_time or "09:00").strip()
        try:
            parts = time_str.split(":")
            h, m = int(parts[0] or 9), int(parts[1] or 0) if len(parts) > 1 else 0
            return f"{m} {h} * * *"
        except (ValueError, IndexError):
            return "0 9 * * *"
    if s == "weekly":
        time_str = (schedule_time or "09:00").strip()
        days_str = (schedule_days or "1").strip()  # 1 = Monday default
        try:
            parts = time_str.split(":")
            h, m = int(parts[0] or 9), int(parts[1] or 0) if len(parts) > 1 else 0
            return f"{m} {h} * * {days_str}"
        except (ValueError, IndexError):
            return f"0 9 * * {days_str}"
    return schedule.strip()


def _utc_offset() -> timedelta:
    """Return the local UTC offset as a timedelta (positive = east of UTC)."""
    offset_secs = -(_time.timezone if _time.daylight == 0 else _time.altzone)
    return timedelta(seconds=offset_secs)


def _cron_weekday(dt: datetime) -> int:
    """Return cron weekday for a datetime: Sunday=0, Monday=1."""
    return (dt.weekday() + 1) % 7


def _parse_int_field(value: str, *, minimum: int, maximum: int) -> int | None:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


def _next_run_from_simple_cron(cron_expr: str, base_local: datetime) -> Optional[datetime]:
    """Fallback for the simple hourly/daily/weekly cron forms this UI emits."""
    parts = (cron_expr or "").strip().split()
    if len(parts) != 5:
        return None
    minute_raw, hour_raw, day_raw, month_raw, weekday_raw = parts
    if day_raw != "*" or month_raw != "*":
        return None
    minute = _parse_int_field(minute_raw, minimum=0, maximum=59)
    if minute is None:
        return None
    hours: list[int]
    if hour_raw == "*":
        hours = list(range(24))
    else:
        hour = _parse_int_field(hour_raw, minimum=0, maximum=23)
        if hour is None:
            return None
        hours = [hour]
    weekdays: set[int] | None = None
    if weekday_raw != "*":
        weekdays = set()
        for item in weekday_raw.split(","):
            weekday = _parse_int_field(item, minimum=0, maximum=7)
            if weekday is None:
                return None
            weekdays.add(0 if weekday == 7 else weekday)

    candidates: list[datetime] = []
    for day_offset in range(8):
        day = (base_local + timedelta(days=day_offset)).replace(second=0, microsecond=0)
        if weekdays is not None and _cron_weekday(day) not in weekdays:
            continue
        for hour in hours:
            candidate = day.replace(hour=hour, minute=minute)
            if candidate > base_local:
                candidates.append(candidate)
    return min(candidates) if candidates else None


def _next_run_from_cron(
    cron_expr: str,
    from_dt: Optional[datetime] = None,
    timezone: Optional[str] = None,
    allow_current_minute: bool = False,
) -> Optional[datetime]:
    """Compute next run time from cron expression.
    Cron hours/minutes are local machine time (user typed them in the UI).
    Returns UTC datetime for DB storage.

    allow_current_minute=True: subtract 59s so croniter can match the current
    minute (used on initial save so a schedule set at 09:00 fires today, not
    tomorrow). Should NOT be used after a workflow has already run, or it will
    re-schedule to the same minute that just fired.
    """
    if (cron_expr or "").strip().lower().startswith("once:"):
        raw = (cron_expr or "").split(":", 1)[1].strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception as e:
            logger.warning("Could not parse one-time schedule %r: %s", raw, e)
            return None
    try:
        from croniter import croniter
        offset = _utc_offset()
        base_utc = from_dt or datetime.utcnow()
        if allow_current_minute:
            base_utc = base_utc - timedelta(seconds=59)
        base_local = base_utc + offset
        it = croniter(cron_expr, base_local)
        local_next = it.get_next(datetime)
        # Convert back to UTC
        return local_next - offset
    except Exception as e:
        offset = _utc_offset()
        base_utc = from_dt or datetime.utcnow()
        if allow_current_minute:
            base_utc = base_utc - timedelta(seconds=59)
        local_next = _next_run_from_simple_cron(cron_expr, base_utc + offset)
        if local_next:
            logger.warning("croniter failed for %r: %s; used built-in simple schedule fallback", cron_expr, e)
            return local_next - offset
        logger.warning("croniter failed for %r: %s", cron_expr, e)
        return None


def get_due_scheduled_workflows() -> List[dict]:
    """Return scheduled workflows that are due to run.

    A workflow is due when:
    - schedule_enabled is True
    - schedule_preset is set (schedule configuration exists)
    - next_run_at is set and <= now
    - No active run exists (status 'running' or 'waiting')
    """
    now = datetime.utcnow()
    with get_session() as session:
        rows = (
            session.query(AutoWorkflow)
            .filter(
                AutoWorkflow.schedule_enabled == True,
                AutoWorkflow.schedule_preset.isnot(None),
                AutoWorkflow.next_run_at.isnot(None),
                AutoWorkflow.next_run_at <= now,
            )
            .all()
        )
        result = []
        for wf in rows:
            # Skip if an active run exists (status running or waiting)
            active_run = (
                session.query(AutoWorkflowRun)
                .filter(
                    AutoWorkflowRun.workflow_id == wf.id,
                    AutoWorkflowRun.status.in_(["running", "waiting"]),
                )
                .first()
            )
            if active_run:
                logger.warning(
                    "Workflow scheduler: skipping workflow %d — active run %d (status=%s)",
                    wf.id, active_run.id, active_run.status,
                )
                continue
            result.append({
                "id": wf.id,
                "schedule_preset": wf.schedule_preset,
            })
        return result


def _json_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _scheduled_action_runtime_config(workflow: AutoWorkflow) -> dict[str, Any]:
    step = sorted(list(workflow.steps or []), key=lambda s: s.position or 0)[0] if workflow.steps else None
    config = _json_config(getattr(step, "config", None))
    return {
        "target_context": config.get("target_context") if isinstance(config.get("target_context"), dict) else {},
        "safety": config.get("safety") if isinstance(config.get("safety"), dict) else {},
    }


def _is_target_app_frontmost(app_name: str) -> bool | None:
    try:
        from distr.core.actions.desktop import is_app_frontmost

        return is_app_frontmost(app_name)
    except Exception as exc:
        logger.warning("Workflow scheduler: could not check frontmost app %r: %s", app_name, exc)
        return None


def _bring_target_app_to_front(app_name: str) -> None:
    try:
        from distr.core.actions.desktop import open_app

        open_app(app_name)
    except Exception as exc:
        logger.warning("Workflow scheduler: could not bring app %r to front: %s", app_name, exc)


def _scheduled_timing_metadata(due_at: datetime | None, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.utcnow()
    late = bool(due_at and due_at < now)
    return {
        "scheduled_due_at": due_at.isoformat() if due_at else None,
        "scheduled_started_at": now.isoformat(),
        "late": late,
        "late_by_seconds": int((now - due_at).total_seconds()) if late and due_at else 0,
        "late_policy": "run_as_soon_as_possible",
    }


def _record_scheduled_action_skip(
    session,
    workflow: AutoWorkflow,
    message: str,
    *,
    due_at: datetime | None = None,
    timing: dict[str, Any] | None = None,
) -> None:
    now = datetime.utcnow()
    timing_data = timing or _scheduled_timing_metadata(due_at, now=now)
    run = AutoWorkflowRun(
        workflow_id=workflow.id,
        status="skipped",
        started_at=now,
        completed_at=now,
        run_data=json.dumps({
            "source_type": "scheduled",
            "source_label": "Scheduled",
            "phase": "scheduled_action",
            "result": "skipped",
            "message": message,
            **timing_data,
        }),
    )
    session.add(run)
    workflow.last_run_at = now


def _record_scheduled_action_direct_result(
    session,
    workflow: AutoWorkflow,
    *,
    status: str,
    message: str,
    due_at: datetime | None = None,
    timing: dict[str, Any] | None = None,
) -> None:
    now = datetime.utcnow()
    timing_data = timing or _scheduled_timing_metadata(due_at, now=now)
    packet_status = "completed" if status == "completed" else "failed"
    run = AutoWorkflowRun(
        workflow_id=workflow.id,
        status=status,
        started_at=now,
        completed_at=now,
        run_data=json.dumps({
            "source_type": "scheduled",
            "source_label": "Scheduled",
            "phase": "scheduled_action",
            "execution_mode": "direct_desktop_action",
            "result": packet_status,
            "message": message,
            "result_packet": {
                "status": packet_status,
                "summary": message,
                "artifacts": {"logs": [f"workflow_run:{workflow.id}:scheduled_direct"]},
                "execution": {
                    "action_trace": [{
                        "step": "1",
                        "action_type": "scheduled_desktop_action",
                        "description": message,
                        "result": packet_status,
                    }],
                },
            },
            **timing_data,
        }),
    )
    session.add(run)
    workflow.last_run_at = now


def _execute_direct_scheduled_action(workflow: AutoWorkflow) -> tuple[bool, str] | None:
    """Execute simple scheduled desktop actions without a full agent loop.

    Returns (success, message) when the action was handled directly, or None
    when the workflow should continue through normal workflow dispatch.
    """
    runtime = _scheduled_action_runtime_config(workflow)
    step = sorted(list(workflow.steps or []), key=lambda s: s.position or 0)[0] if workflow.steps else None
    config = _json_config(getattr(step, "config", None))
    action = config.get("scheduled_action")
    if not isinstance(action, dict):
        return None
    action_type = str(action.get("type") or "").strip().lower()
    if action_type != "open_app":
        return None
    app_name = str(action.get("app_name") or "").strip()
    if not app_name:
        return False, "Scheduled open-app action failed because app_name was missing."
    try:
        target = runtime.get("target_context") or {}
        safety = runtime.get("safety") or {}
        target_app = str(target.get("app_name") or "").strip()
        already_focused = bool(
            safety.get("bring_app_to_front")
            and target_app
            and target_app.strip().lower() == app_name.strip().lower()
        )
        if not already_focused:
            from distr.core.actions.desktop import open_app

            open_app(app_name)
        suffix = ""
        if safety.get("bring_app_to_front") and target_app:
            suffix = f" Target app focus requested: {target_app}."
        return True, f"Opened {app_name} for scheduled action.{suffix}".strip()
    except Exception as exc:
        return False, f"Scheduled open-app action failed for {app_name}: {exc}"


def _scheduled_action_preflight(workflow: AutoWorkflow) -> str | None:
    runtime = _scheduled_action_runtime_config(workflow)
    target = runtime.get("target_context") or {}
    safety = runtime.get("safety") or {}
    app_name = str(target.get("app_name") or "").strip()
    if not app_name:
        return None

    if safety.get("require_app_in_foreground"):
        frontmost = _is_target_app_frontmost(app_name)
        if frontmost is not True:
            if frontmost is None:
                return f"Skipped scheduled action because the foreground app could not be verified for {app_name}."
            return f"Skipped scheduled action because {app_name} was not the foreground app."

    if safety.get("bring_app_to_front"):
        _bring_target_app_to_front(app_name)
    return None


def run_scheduled_workflow(
    workflow_id: int,
    on_start_orchestration: Optional[Callable] = None,
) -> bool:
    """
    Trigger a scheduled workflow run and advance next_run_at.

    Uses the unified workflow service's start_workflow_run() for execution.
    Advances next_run_at immediately to prevent re-firing on the next tick.
    """
    from distr.core.workflow.service import start_workflow_run

    with get_session() as session:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf or not wf.schedule_enabled:
            return False

        if not wf.steps:
            logger.warning("Workflow scheduler: workflow %d has no steps", workflow_id)
            _advance_next_run(wf)
            session.commit()
            return True

        due_at = wf.next_run_at
        timing_metadata = _scheduled_timing_metadata(due_at)
        skip_message = _scheduled_action_preflight(wf)
        if skip_message:
            logger.warning("Workflow scheduler: %s", skip_message)
            _advance_next_run(wf)
            _record_scheduled_action_skip(session, wf, skip_message, due_at=due_at, timing=timing_metadata)
            session.commit()
            return True

        # Advance next_run_at immediately so the scheduler doesn't re-fire
        _advance_next_run(wf)
        direct_result = _execute_direct_scheduled_action(wf)
        if direct_result is not None:
            ok, message = direct_result
            _record_scheduled_action_direct_result(
                session,
                wf,
                status="completed" if ok else "failed",
                message=message,
                due_at=due_at,
                timing=timing_metadata,
            )
            session.commit()
            return True
        session.commit()

    # Start the workflow run via the unified service
    result = start_workflow_run(
        workflow_id,
        context="Scheduled Run",
        run_metadata={
            "source_type": "scheduled",
            "source_label": "Scheduled",
            "phase": "scheduled_action",
            **timing_metadata,
        },
    )
    if "error" in result:
        logger.error(
            "Workflow scheduler: failed to start workflow %d: %s",
            workflow_id, result["error"],
        )
        # Mark last_run_at even on failure to prevent infinite retry loops
        with get_session() as session:
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
            if wf:
                wf.last_run_at = datetime.utcnow()
                session.commit()
        return False

    logger.info(
        "Workflow scheduler: started workflow %d (run_id=%s)",
        workflow_id, result.get("run_id"),
    )
    return True


def _advance_next_run(workflow: AutoWorkflow) -> None:
    """Set next_run_at from schedule configuration."""
    if str(workflow.schedule_preset or "").strip().lower() == "once":
        workflow.schedule_enabled = False
        workflow.next_run_at = None
        workflow.last_run_at = datetime.utcnow()
        return
    cron = schedule_to_cron(
        workflow.schedule_preset,
        workflow.schedule_time,
        workflow.schedule_timezone,
        workflow.schedule_days,
    )
    if not cron:
        workflow.next_run_at = None
        return
    base = workflow.last_run_at or datetime.utcnow()
    next_run = _next_run_from_cron(cron, base, workflow.schedule_timezone)
    workflow.next_run_at = next_run
    if next_run:
        logger.debug("Workflow scheduler: next run for workflow %d at %s", workflow.id, next_run)
