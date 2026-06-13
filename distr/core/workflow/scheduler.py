"""
Workflow Scheduler

Runs scheduled workflows when due. Called periodically by the main app (QTimer).
Ported from distr/core/step_runner/scheduler.py to operate on AutoWorkflow models.
"""

import logging
import json
import re
import time as _time
from datetime import datetime, timedelta
from typing import Any, List, Optional, Callable

from sqlalchemy import func

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun

logger = logging.getLogger(__name__)

# Qt scheduler timer bounds — keep wakeups light unless a sub-minute schedule needs them.
SCHEDULER_POLL_MIN_MS = 2000
SCHEDULER_POLL_DEFAULT_MS = 60_000
SCHEDULER_POLL_IDLE_MS = 300_000
SCHEDULER_POLL_MAX_MS = 300_000

# Preset schedules -> cron (default times)
SCHEDULE_PRESETS = {
    "15min": "*/15 * * * *",     # Every 15 minutes
    "30min": "*/30 * * * *",     # Every 30 minutes
    "hourly": "0 * * * *",       # Every hour at :00
    "daily": "0 9 * * *",        # 9am daily
    "weekly": "0 9 * * 1",       # Monday 9am
}


def parse_interval_schedule(schedule_time: Optional[str]) -> tuple[int, str] | None:
    """Decode interval schedules stored as ``15:minutes`` or ``30:seconds``."""
    raw = str(schedule_time or "").strip().lower()
    if not raw or ":" not in raw:
        return None
    value_raw, unit_raw = raw.split(":", 1)
    try:
        value = max(1, int(value_raw.strip()))
    except ValueError:
        return None
    unit = unit_raw.strip()
    if unit.startswith("sec"):
        return value, "seconds"
    return value, "minutes"


def interval_timedelta(value: int, unit: str) -> timedelta:
    if str(unit or "").strip().lower().startswith("sec"):
        return timedelta(seconds=max(1, int(value)))
    return timedelta(minutes=max(1, int(value)))


def next_run_for_interval(
    value: int,
    unit: str,
    from_dt: Optional[datetime] = None,
    *,
    allow_current_window: bool = False,
) -> datetime:
    base = from_dt or datetime.utcnow()
    if allow_current_window:
        base = base - timedelta(seconds=1)
    return base + interval_timedelta(value, unit)


def parse_once_run_at_as_utc(raw: str) -> Optional[datetime]:
    """Parse a one-time schedule timestamp for UTC storage/comparison.

  ``datetime-local`` values from the automation UI are naive local wall-clock
  times. Explicit ``Z`` or offset suffixes are treated as absolute UTC/offset
  timestamps.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        from datetime import timezone

        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed - _utc_offset()


def normalize_once_run_at_storage(raw: str) -> str:
    """Normalize a one-time run-at value to UTC ISO for persistence."""
    parsed = parse_once_run_at_as_utc(raw)
    if not parsed:
        return str(raw or "").strip()
    return parsed.replace(microsecond=0).isoformat() + "Z"


def once_run_at_for_datetime_local_input(stored: str) -> str:
    """Convert a stored one-time run-at value to ``datetime-local`` input text."""
    parsed = parse_once_run_at_as_utc(stored)
    if not parsed:
        return str(stored or "").strip().replace("Z", "")
    local = parsed + _utc_offset()
    return local.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")


def get_minimum_schedule_interval_seconds(*, subminute_only: bool = False) -> Optional[int]:
    """Return the smallest active interval schedule in seconds, if any."""
    minimum: int | None = None
    with get_session() as session:
        rows = (
            session.query(AutoWorkflow.schedule_time)
            .filter(
                AutoWorkflow.schedule_enabled == True,  # noqa: E712
                AutoWorkflow.schedule_preset == "interval",
            )
            .all()
        )
    for (schedule_time,) in rows:
        parsed = parse_interval_schedule(schedule_time)
        if not parsed:
            continue
        value, unit = parsed
        if subminute_only and unit != "seconds":
            continue
        seconds = value if unit == "seconds" else value * 60
        minimum = seconds if minimum is None else min(minimum, seconds)
    return minimum


def get_seconds_until_next_due_workflow() -> Optional[float]:
    """Seconds until the next enabled scheduled workflow is due, or None if none."""
    now = datetime.utcnow()
    with get_session() as session:
        next_at = (
            session.query(func.min(AutoWorkflow.next_run_at))
            .filter(
                AutoWorkflow.schedule_enabled == True,  # noqa: E712
                AutoWorkflow.schedule_preset.isnot(None),
                AutoWorkflow.next_run_at.isnot(None),
            )
            .scalar()
        )
    if next_at is None:
        return None
    return (next_at - now).total_seconds()


def get_workflow_scheduler_interval_ms() -> int:
    """Choose a low-overhead Qt timer interval based on active schedules.

    - No enabled schedules: idle (5 min).
    - Sub-minute interval schedules: poll at ~1/3 of the interval (min 2s, max 10s).
    - Everything else: sleep toward the next due time instead of hammering every second.
    """
    subminute = get_minimum_schedule_interval_seconds(subminute_only=True)
    if subminute is not None and subminute < 60:
        return max(
            SCHEDULER_POLL_MIN_MS,
            min((subminute * 1000) // 3, 10_000),
        )

    seconds_until_due = get_seconds_until_next_due_workflow()
    if seconds_until_due is None:
        return SCHEDULER_POLL_IDLE_MS
    if seconds_until_due <= 0:
        return SCHEDULER_POLL_MIN_MS
    if seconds_until_due <= 120:
        return max(SCHEDULER_POLL_MIN_MS, int(seconds_until_due * 1000))
    if seconds_until_due <= 3600:
        return max(15_000, min(int(seconds_until_due * 500), SCHEDULER_POLL_MAX_MS))
    return SCHEDULER_POLL_MAX_MS


def apply_workflow_scheduler_timer_interval(timer: Any) -> int:
    """Sync a Qt timer with the tightest active schedule interval."""
    interval_ms = get_workflow_scheduler_interval_ms()
    if timer is not None and hasattr(timer, "interval") and hasattr(timer, "setInterval"):
        if timer.interval() != interval_ms:
            timer.setInterval(interval_ms)
    return interval_ms


def normalize_schedule_time(value: Optional[str], *, default: str = "09:00") -> str:
    """Normalize human-entered times to HH:MM.

    The automation UI uses native time inputs, but some shells/browsers/custom
    controls can hand us values like "9/20" or "920". Keep those schedules from
    silently falling back to 09:00 and missing the intended minute.
    """
    raw = str(value or "").strip()
    if not raw:
        raw = default
    raw = raw.lower().replace("am", "").replace("pm", "").strip()

    match = re.fullmatch(r"(\d{1,2})\D+(\d{1,2})", raw)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
    elif re.fullmatch(r"\d{3,4}", raw):
        hour, minute = int(raw[:-2]), int(raw[-2:])
    elif re.fullmatch(r"\d{1,2}", raw):
        hour, minute = int(raw), 0
    else:
        raise ValueError(f"Invalid schedule time: {value!r}")

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Schedule time out of range: {value!r}")
    return f"{hour:02d}:{minute:02d}"


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
    if s == "interval":
        parsed = parse_interval_schedule(schedule_time)
        if not parsed:
            return None
        value, unit = parsed
        if unit == "seconds":
            return f"interval:{value}:seconds"
        if 1 <= value <= 59:
            return f"*/{value} * * * *"
        return f"interval:{value}:minutes"
    if s in {"15min", "15m"}:
        return SCHEDULE_PRESETS["15min"]
    if s in {"30min", "30m"}:
        return SCHEDULE_PRESETS["30min"]
    if s == "hourly":
        return SCHEDULE_PRESETS["hourly"]
    if s == "once":
        run_at = (schedule_time or "").strip()
        return f"once:{run_at}" if run_at else None
    if s == "daily":
        try:
            time_str = normalize_schedule_time(schedule_time, default="09:00")
            h, m = [int(part) for part in time_str.split(":", 1)]
            return f"{m} {h} * * *"
        except (ValueError, IndexError):
            return "0 9 * * *"
    if s == "weekly":
        days_str = (schedule_days or "1").strip()  # 1 = Monday default
        try:
            time_str = normalize_schedule_time(schedule_time, default="09:00")
            h, m = [int(part) for part in time_str.split(":", 1)]
            return f"{m} {h} * * {days_str}"
        except (ValueError, IndexError):
            return f"0 9 * * {days_str}"
    if s == "monthly":
        days_str = (schedule_days or "1").strip()
        try:
            time_str = normalize_schedule_time(schedule_time, default="09:00")
            h, m = [int(part) for part in time_str.split(":", 1)]
            return f"{m} {h} {days_str} * *"
        except (ValueError, IndexError):
            return f"0 9 {days_str} * *"
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


def _parse_simple_field_values(value: str, *, minimum: int, maximum: int) -> list[int] | None:
    raw = str(value or "").strip()
    if raw == "*":
        return list(range(minimum, maximum + 1))
    if raw.startswith("*/"):
        step = _parse_int_field(raw[2:], minimum=1, maximum=maximum)
        if not step:
            return None
        return list(range(minimum, maximum + 1, step))
    parsed = _parse_int_field(raw, minimum=minimum, maximum=maximum)
    return [parsed] if parsed is not None else None


def _next_run_from_simple_cron(cron_expr: str, base_local: datetime) -> Optional[datetime]:
    """Fallback for the simple hourly/daily/weekly cron forms this UI emits."""
    parts = (cron_expr or "").strip().split()
    if len(parts) != 5:
        return None
    minute_raw, hour_raw, day_raw, month_raw, weekday_raw = parts
    if day_raw != "*" or month_raw != "*":
        return None
    minutes = _parse_simple_field_values(minute_raw, minimum=0, maximum=59)
    if not minutes:
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
            for minute in minutes:
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
            return parse_once_run_at_as_utc(raw)
        except Exception as e:
            logger.warning("Could not parse one-time schedule %r: %s", raw, e)
            return None
    if (cron_expr or "").strip().lower().startswith("interval:"):
        parts = (cron_expr or "").strip().split(":")
        if len(parts) >= 3:
            try:
                value = max(1, int(parts[1]))
            except ValueError:
                return None
            unit = parts[2]
            return next_run_for_interval(
                value,
                unit,
                from_dt,
                allow_current_window=allow_current_minute,
            )
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


def _notify_automation_schedule_changed() -> None:
    try:
        from distr.gui.web.workflow_events import increment_workflow_updated

        increment_workflow_updated()
    except Exception:
        logger.debug("Automation schedule UI notify failed", exc_info=True)


def run_scheduled_workflow(
    workflow_id: int,
    on_start_orchestration: Optional[Callable] = None,
    event_queue: Optional[Any] = None,
    on_scheduled_automation: Optional[Callable[[dict[str, Any]], Any]] = None,
) -> bool:
    """
    Trigger a scheduled workflow run and advance next_run_at.

    Uses the unified workflow service's start_workflow_run() for execution.
    Advances next_run_at immediately to prevent re-firing on the next tick.
    """
    from distr.core.workflow.service import start_workflow_run

    now = datetime.utcnow()
    with get_session() as session:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf or not wf.schedule_enabled:
            return False
        if not wf.next_run_at or wf.next_run_at > now:
            logger.debug(
                "Workflow scheduler: workflow %d is no longer due (next_run_at=%s)",
                workflow_id,
                wf.next_run_at,
            )
            return False

        if not wf.steps:
            logger.warning("Workflow scheduler: workflow %d has no steps", workflow_id)
            _advance_next_run(wf)
            session.commit()
            return True

        due_at = wf.next_run_at
        timing_metadata = _scheduled_timing_metadata(due_at)
        scheduled_automation = None
        try:
            from distr.core.automation_orchestrator import (
                is_automation_workflow,
                serialize_automation_workflow,
            )

            if is_automation_workflow(wf):
                scheduled_automation = serialize_automation_workflow(wf)
        except Exception:
            logger.debug("Workflow scheduler: automation detection failed for workflow %d", workflow_id, exc_info=True)
        skip_message = _scheduled_action_preflight(wf)
        if skip_message:
            logger.warning("Workflow scheduler: %s", skip_message)
            _advance_next_run(wf)
            _record_scheduled_action_skip(session, wf, skip_message, due_at=due_at, timing=timing_metadata)
            session.commit()
            return True

        # Advance next_run_at immediately so the scheduler doesn't re-fire
        _advance_next_run(wf)
        wf.modified_date = datetime.utcnow()
        notify_schedule_ui = scheduled_automation is not None
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
            if notify_schedule_ui:
                _notify_automation_schedule_changed()
            return True
        session.commit()
        if notify_schedule_ui:
            _notify_automation_schedule_changed()

    if scheduled_automation is not None:
        try:
            if on_scheduled_automation is not None:
                result = on_scheduled_automation({
                    "automation": scheduled_automation,
                    "timing": timing_metadata,
                    "workflow_id": workflow_id,
                })
            else:
                from distr.core.automation_orchestrator import dispatch_automation_to_current_chat

                result = dispatch_automation_to_current_chat(
                    scheduled_automation,
                    manual=False,
                    schedule_metadata={
                        "source_type": "scheduled",
                        "source_label": "Scheduled",
                        "phase": "scheduled_automation",
                        **timing_metadata,
                    },
                )
            if isinstance(result, dict) and result.get("status") == "failed":
                logger.warning(
                    "Workflow scheduler: scheduled automation %d dispatch failed: %s",
                    workflow_id,
                    result.get("summary"),
                )
                return False
            logger.info(
                "Workflow scheduler: dispatched automation %d to orchestrator chat",
                workflow_id,
            )
            return True
        except Exception as exc:
            logger.error(
                "Workflow scheduler: scheduled automation %d dispatch errored: %s",
                workflow_id,
                exc,
                exc_info=True,
            )
            return False

    # Start the workflow run via the unified service
    result = start_workflow_run(
        workflow_id,
        context="Scheduled Run",
        event_queue=event_queue,
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
    preset = str(workflow.schedule_preset or "").strip().lower()
    if preset == "once":
        workflow.schedule_enabled = False
        workflow.next_run_at = None
        workflow.last_run_at = datetime.utcnow()
        return
    if preset == "interval":
        parsed = parse_interval_schedule(workflow.schedule_time)
        if parsed:
            value, unit = parsed
            base = datetime.utcnow()
            workflow.next_run_at = base + interval_timedelta(value, unit)
            if workflow.next_run_at:
                logger.debug(
                    "Workflow scheduler: next interval run for workflow %d at %s",
                    workflow.id,
                    workflow.next_run_at,
                )
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
    # Always schedule from the moment we just fired. Using last_run_at here can
    # leave next_run_at in the past (e.g. daily 09:00 fired at 09:05) and cause
    # the scheduler to dispatch the same automation again on the next poll.
    now = datetime.utcnow()
    next_run = _next_run_from_cron(cron, now, workflow.schedule_timezone)
    guard = 0
    while next_run and next_run <= now and guard < 8:
        next_run = _next_run_from_cron(
            cron,
            next_run + timedelta(seconds=1),
            workflow.schedule_timezone,
        )
        guard += 1
    workflow.next_run_at = next_run
    if next_run:
        logger.debug("Workflow scheduler: next run for workflow %d at %s", workflow.id, next_run)
