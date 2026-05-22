"""
Workflow Scheduler

Runs scheduled workflows when due. Called periodically by the main app (QTimer).
Ported from distr/core/step_runner/scheduler.py to operate on AutoWorkflow models.
"""

import logging
import time as _time
from datetime import datetime, timedelta
from typing import List, Optional, Callable

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

        # Advance next_run_at immediately so the scheduler doesn't re-fire
        _advance_next_run(wf)
        session.commit()

    # Start the workflow run via the unified service
    result = start_workflow_run(
        workflow_id,
        context="Scheduled Run",
        run_metadata={
            "source_type": "scheduled",
            "source_label": "Scheduled",
            "phase": "planning",
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
