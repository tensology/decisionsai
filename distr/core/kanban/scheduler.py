"""
Ticket Board Agent Scheduler — compute next run times for board agent check-ins.

Supports hourly, daily, weekly, fortnightly, and monthly frequencies.
"""
from datetime import datetime, timedelta
from typing import List, Optional
import calendar
def compute_next_run(
    frequency: str,
    last_run_at: Optional[datetime],
    agent_time: str,
    created_date: Optional[datetime] = None,
    agent_hours: Optional[List[int]] = None,
    agent_days: Optional[List[int]] = None,
    agent_monthly_day: Optional[int] = None,
) -> Optional[datetime]:
    """Compute the next scheduled run datetime for a kanban agent.

    Args:
        frequency: One of 'hourly', 'daily', 'weekly', 'fortnightly', 'monthly'.
        last_run_at: When the agent last ran. If None, uses created_date.
        agent_time: Time string in "HH:MM" format.
        created_date: Board creation date, used as fallback baseline.
        agent_hours: List of hours (0-23) for hourly frequency.
        agent_days: List of weekday indices (0=Sun..6=Sat) for weekly/fortnightly.
        agent_monthly_day: Day of month (1-28) for monthly frequency.

    Returns:
        The next run datetime, or None if inputs are invalid.
    """
    # Parse agent_time
    try:
        parts = agent_time.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None

    # Determine baseline date
    baseline = last_run_at or created_date
    if baseline is None:
        return None

    # Minute-based frequencies (5min, 10min, 15min, 30min)
    _minute_intervals = {"5min": 5, "10min": 10, "15min": 15, "30min": 30}
    if frequency in _minute_intervals:
        interval = _minute_intervals[frequency]
        next_run = baseline + timedelta(minutes=interval)
        return next_run.replace(second=0, microsecond=0)

    if frequency == "hourly":
        return _compute_hourly(baseline, agent_hours)

    elif frequency == "daily":
        next_date = baseline + timedelta(days=1)
        return next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    elif frequency == "weekly":
        if agent_days:
            result = _find_earliest_weekday(baseline, agent_days, hour, minute, window_days=7)
            if result is not None:
                return result
        next_date = baseline + timedelta(days=7)
        return next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    elif frequency == "fortnightly":
        if agent_days:
            result = _find_earliest_weekday(baseline, agent_days, hour, minute, window_days=14)
            if result is not None:
                return result
        next_date = baseline + timedelta(days=14)
        return next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    elif frequency == "monthly":
        # Same day next month; clamp to last day if needed
        year = baseline.year
        month = baseline.month + 1
        if month > 12:
            month = 1
            year += 1
        max_day = calendar.monthrange(year, month)[1]
        day = min(baseline.day, max_day)
        return datetime(year, month, day, hour, minute, 0)

    return None


def _compute_hourly(
    baseline: datetime,
    agent_hours: Optional[List[int]],
) -> Optional[datetime]:
    """Compute next run for hourly frequency.

    Filters agent_hours to [0, 23], deduplicates and sorts. Finds the earliest
    hour strictly after baseline on the same day. If none remain, wraps to the
    earliest hour on the next day. Returns None if agent_hours is empty after
    filtering.
    """
    if not agent_hours:
        return None

    # Filter to valid range, deduplicate, sort
    valid_hours = sorted(set(h for h in agent_hours if 0 <= h <= 23))
    if not valid_hours:
        return None

    # Find earliest hour strictly after baseline on the same day
    for h in valid_hours:
        candidate = baseline.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > baseline:
            return candidate

    # All hours today have passed — wrap to earliest hour next day
    next_day = baseline + timedelta(days=1)
    return next_day.replace(hour=valid_hours[0], minute=0, second=0, microsecond=0)


def _to_python_weekday(day_index: int) -> int:
    """Convert 0=Sun..6=Sat index to Python weekday (0=Mon..6=Sun)."""
    # 0=Sun -> 6, 1=Mon -> 0, 2=Tue -> 1, ..., 6=Sat -> 5
    return (day_index - 1) % 7


def _find_earliest_weekday(
    baseline: datetime,
    agent_days: List[int],
    hour: int,
    minute: int,
    window_days: int,
) -> Optional[datetime]:
    """Find the earliest matching weekday within a window after baseline.

    Args:
        baseline: The reference datetime (last_run_at or created_date).
        agent_days: List of weekday indices (0=Sun..6=Sat).
        hour: Target hour from agent_time.
        minute: Target minute from agent_time.
        window_days: Number of days in the search window (7 or 14).

    Returns:
        The earliest matching datetime, or None if no match in window.
    """
    # Convert agent_days to Python weekday format
    python_weekdays = set(_to_python_weekday(d) for d in agent_days if 0 <= d <= 6)
    if not python_weekdays:
        return None

    # Search day 1 through window_days (strictly after baseline)
    for offset in range(1, window_days + 1):
        candidate_date = baseline + timedelta(days=offset)
        if candidate_date.weekday() in python_weekdays:
            return candidate_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    return None


def check_kanban_schedules() -> None:
    """Check all boards with a default_workflow_id for due schedules and fire agents.

    Reads global kanban agent settings via load_settings_from_db(). If the global
    kanban_agent_enabled flag is True, uses the global scheduling config to compute
    the next run time. Fires a KanbanAgentCheckIn for each board that has a
    default_workflow_id and whose schedule is due.

    Designed to be called from an existing scheduler tick.
    """
    import json
    import logging

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.kanban.agent import start_agent_checkin
    from distr.core.settings import load_settings_from_db

    logger = logging.getLogger(__name__)
    now = datetime.utcnow()

    settings = load_settings_from_db()
    # Global scheduler switch: when off, no automatic board check-ins are fired.
    if not settings.get("kanban_agent_enabled", False):
        return

    global_frequency = settings.get("kanban_agent_frequency", "daily")
    global_agent_time = settings.get("kanban_agent_time", "09:00")

    # Parse JSON-encoded hours and days from global settings
    raw_hours = settings.get("kanban_agent_hours", "[]")
    if isinstance(raw_hours, str):
        try:
            agent_hours = json.loads(raw_hours)
        except (json.JSONDecodeError, ValueError):
            agent_hours = []
    else:
        agent_hours = raw_hours if isinstance(raw_hours, list) else []

    raw_days = settings.get("kanban_agent_days", "[]")
    if isinstance(raw_days, str):
        try:
            agent_days = json.loads(raw_days)
        except (json.JSONDecodeError, ValueError):
            agent_days = []
    else:
        agent_days = raw_days if isinstance(raw_days, list) else []

    global_agent_monthly_day = settings.get("kanban_agent_monthly_day", 1)

    with get_session() as db:
        boards = (
            db.query(KanbanBoard)
            .filter(KanbanBoard.default_workflow_id.isnot(None))
            .all()
        )
        board_infos = []
        for b in boards:
            # Determine last_run_at from this board's own runs to keep schedules isolated.
            last_run_at = None
            last_run = (
                db.query(AutoWorkflowRun)
                .filter(AutoWorkflowRun.board_id == b.id)
                .order_by(AutoWorkflowRun.started_at.desc())
                .first()
            )
            if last_run and last_run.started_at:
                last_run_at = last_run.started_at

            board_infos.append({
                "id": b.id,
                "last_run_at": last_run_at,
                "created_date": b.created_date,
                "frequency": (b.agent_frequency or global_frequency or "daily"),
                "agent_time": (b.agent_time or global_agent_time or "09:00"),
                "agent_days": b.agent_days,
                "agent_monthly_day": (
                    b.agent_monthly_day
                    if b.agent_monthly_day is not None
                    else global_agent_monthly_day
                ),
            })

    for info in board_infos:
        board_days = info.get("agent_days", "[]")
        if isinstance(board_days, str):
            try:
                parsed_days = json.loads(board_days)
            except (json.JSONDecodeError, ValueError):
                parsed_days = []
        else:
            parsed_days = board_days if isinstance(board_days, list) else []

        next_run_at = compute_next_run(
            frequency=info.get("frequency", global_frequency),
            last_run_at=info["last_run_at"],
            agent_time=info.get("agent_time", global_agent_time),
            created_date=info["created_date"],
            agent_hours=agent_hours,
            agent_days=parsed_days if parsed_days else agent_days,
            agent_monthly_day=info.get("agent_monthly_day", global_agent_monthly_day),
        )
        if next_run_at is None:
            continue
        if next_run_at <= now:
            result = start_agent_checkin(info["id"])
            if result["status"] == "already_running":
                logger.info(
                    "Ticket Board scheduler: board %s already has active check-in, skipping duplicate fire",
                    info["id"],
                )
            elif result["status"] == "started":
                logger.info("Ticket Board scheduler: board %s is due, firing agent check-in", info["id"])
            else:
                logger.info(
                    "Ticket Board scheduler: board %s due but not runnable (%s)",
                    info["id"],
                    result.get("reason", "unknown"),
                )
