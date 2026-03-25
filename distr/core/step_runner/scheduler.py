"""
Step Runner Scheduler

Runs scheduled sessions when due. Called periodically by the main app (QTimer).
Uses QTimer.singleShot for non-blocking step execution (no time.sleep).
"""

import json
import logging
import time as _time
from datetime import datetime, timedelta
from typing import List, Optional, Callable

from distr.core.db import get_session
from distr.core.db.step_runner import StepRunnerSession, StepRunnerStep, StepRunnerRun

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
    schedule_days: for weekly, comma-separated cron weekdays "1,3,5" = Mon, Wed, Fri (0=Sun, 1=Mon, ..., 6=Sat)
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


def _schedule_to_cron(schedule: Optional[str]) -> Optional[str]:
    """Legacy: convert preset or return cron as-is."""
    return schedule_to_cron(schedule, None, None)


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
    tomorrow). Should NOT be used after a session has already run, or it will
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


def get_due_scheduled_sessions() -> List[dict]:
    """Return scheduled sessions that are due to run (next_run_at <= now, enabled, not already running)."""
    now = datetime.utcnow()
    with get_session() as session:
        rows = (
            session.query(StepRunnerSession)
            .filter(
                StepRunnerSession.session_type == "scheduled",
                StepRunnerSession.enabled == True,
                StepRunnerSession.schedule.isnot(None),
                StepRunnerSession.next_run_at.isnot(None),
                StepRunnerSession.next_run_at <= now,
                StepRunnerSession.status != "in_progress",
            )
            .all()
        )
        return [{"id": r.id, "schedule": r.schedule} for r in rows]


def run_scheduled_session(
    session_id: int,
    signal_send_text_input=None,
    on_start_orchestration=None,
) -> bool:
    """
    Execute all steps in a scheduled session and update next_run_at.

    Uses orchestration when on_start_orchestration is provided: runs steps in sequence,
    waits for agent completion (chat_stream_finished) before each next step, and retries
    on chat_stream_error. Falls back to legacy timer-based execution if only
    signal_send_text_input is provided.
    """
    with get_session() as session:
        db_session = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        if not db_session or db_session.session_type != "scheduled":
            return False

        steps = sorted(db_session.steps, key=lambda s: s.position)
        steps_data = [
            {
                "id": s.id,
                "title": s.title,
                "instruction": s.instruction,
                "verification": getattr(s, "verification", None),
            }
            for s in steps
        ]

        if not steps_data:
            logger.warning("Step Runner: scheduled session %d has no steps", session_id)
            _advance_next_run(db_session)
            session.commit()
            return True

        # Reset step statuses to pending so each run starts fresh
        for step in steps:
            step.status = "pending"
            step.result = None

        # Create run record
        run = StepRunnerRun(session_id=session_id, status="running")
        session.add(run)
        session.commit()
        run_id = run.id

        # Mark session in progress and advance next_run_at immediately
        # so the scheduler doesn't re-fire this session on the next tick
        db_session.status = "in_progress"
        _advance_next_run(db_session)
        session.commit()

    if on_start_orchestration:
        on_start_orchestration(session_id, run_id, steps_data, "scheduled")
        logger.info("Step Runner: started orchestration for scheduled session %d (%d steps)", session_id, len(steps_data))
        return True

    # Legacy: non-blocking send with fixed delays (no wait for completion)
    def _send_step(step_index: int):
        if step_index >= len(steps_data):
            _finish_run(session_id, run_id, steps_data, success=True)
            return
        if signal_send_text_input:
            try:
                signal_send_text_input.emit(steps_data[step_index]["instruction"], False, None, None)
            except Exception as e:
                logger.error("Step Runner: failed to send step: %s", e)
        _schedule_next(step_index + 1)

    def _schedule_next(step_index: int):
        try:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: _send_step(step_index))
        except ImportError:
            import time
            time.sleep(2)
            _send_step(step_index)

    _send_step(0)
    logger.info("Step Runner: started scheduled session %d (%d steps) [legacy timer]", session_id, len(steps_data))
    return True


def _finish_run(session_id: int, run_id: int, steps_data: list, success: bool = True):
    """Update run and session after orchestration completes."""
    with get_session() as session:
        db_session = session.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
        run = session.query(StepRunnerRun).filter(StepRunnerRun.id == run_id).first()
        run_status = "completed" if success else "failed"
        if db_session:
            db_session.last_run_at = datetime.utcnow()
            db_session.status = run_status
            # Only advance next_run_at here for instruction sessions.
            # Scheduled sessions already advanced next_run_at in run_scheduled_session
            # to prevent re-firing; advancing again would skip a cycle.
            if db_session.session_type != "scheduled":
                _advance_next_run(db_session)
        if run:
            run.completed_at = datetime.utcnow()
            run.status = run_status
            steps = (
                session.query(StepRunnerStep)
                .filter(StepRunnerStep.session_id == session_id)
                .order_by(StepRunnerStep.position.asc())
                .all()
            )
            run.step_results = json.dumps(
                [
                    {"step_id": s.id, "status": s.status, "result": s.result}
                    for s in steps
                ]
            )
        session.commit()
    logger.info("Step Runner: finished session %d (success=%s)", session_id, success)


def _advance_next_run(session: StepRunnerSession) -> None:
    """Set next_run_at from schedule."""
    cron = schedule_to_cron(
        session.schedule,
        getattr(session, 'schedule_time', None),
        getattr(session, 'timezone', None),
        getattr(session, 'schedule_days', None),
    )
    if not cron:
        session.next_run_at = None
        return
    base = session.last_run_at or datetime.utcnow()
    next_run = _next_run_from_cron(cron, base, session.timezone)
    session.next_run_at = next_run
    if next_run:
        logger.debug("Step Runner: next run for session %d at %s", session.id, next_run)
