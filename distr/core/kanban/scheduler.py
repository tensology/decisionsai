"""
Kanban Agent Scheduler — compute next run times for board agent check-ins.

Supports daily, weekly, fortnightly, and monthly frequencies.
"""
from datetime import datetime, timedelta
from typing import Optional
import calendar


def compute_next_run(
    frequency: str,
    last_run_at: Optional[datetime],
    agent_time: str,
    created_date: Optional[datetime] = None,
) -> Optional[datetime]:
    """Compute the next scheduled run datetime for a kanban agent.

    Args:
        frequency: One of 'daily', 'weekly', 'fortnightly', 'monthly'.
        last_run_at: When the agent last ran. If None, uses created_date.
        agent_time: Time string in "HH:MM" format.
        created_date: Board creation date, used as fallback baseline.

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

    if frequency == "daily":
        next_date = baseline + timedelta(days=1)
        return next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    elif frequency == "weekly":
        next_date = baseline + timedelta(days=7)
        return next_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    elif frequency == "fortnightly":
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


def check_kanban_schedules() -> None:
    """Check all agent-enabled boards for due schedules and fire agents.

    Queries all KanbanBoard records where agent_enabled=True. For each board,
    computes the next run time using compute_next_run(). If the next run time
    is at or before now, fires a KanbanAgentCheckIn in a background thread.

    Designed to be called from an existing scheduler tick.
    """
    import logging
    import threading

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.kanban.agent import KanbanAgentCheckIn

    logger = logging.getLogger(__name__)
    now = datetime.utcnow()

    with get_session() as db:
        boards = db.query(KanbanBoard).filter(KanbanBoard.agent_enabled == True).all()  # noqa: E712
        board_infos = []
        for b in boards:
            # Determine last_run_at: most recent workflow run for this board's workflow
            last_run_at = None
            if b.default_workflow_id:
                last_run = (
                    db.query(AutoWorkflowRun)
                    .filter(AutoWorkflowRun.workflow_id == b.default_workflow_id)
                    .order_by(AutoWorkflowRun.started_at.desc())
                    .first()
                )
                if last_run and last_run.started_at:
                    last_run_at = last_run.started_at

            board_infos.append({
                "id": b.id,
                "agent_frequency": b.agent_frequency or "daily",
                "agent_time": b.agent_time or "09:00",
                "last_run_at": last_run_at,
                "created_date": b.created_date,
            })

    for info in board_infos:
        next_run_at = compute_next_run(
            frequency=info["agent_frequency"],
            last_run_at=info["last_run_at"],
            agent_time=info["agent_time"],
            created_date=info["created_date"],
        )
        if next_run_at is None:
            continue
        if next_run_at <= now:
            logger.info("Kanban scheduler: board %s is due, firing agent check-in", info["id"])
            agent = KanbanAgentCheckIn(info["id"])
            threading.Thread(target=agent.run, daemon=True).start()
