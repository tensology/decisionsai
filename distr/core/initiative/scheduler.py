"""Due detection for proactive tasks (R3).

``time`` / weekday / day-of-month are interpreted in *local* timezone (default: system local).
``last_run`` on ``ProactiveTask`` is compared in UTC.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_hhmm(s: str | None) -> tuple[int, int] | None:
    if not s or not str(s).strip():
        return None
    parts = str(s).strip().split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        return h, m
    except ValueError:
        return None


_WEEKDAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _weekday_index(day: str | None) -> int | None:
    if day is None:
        return None
    d = str(day).strip().lower()
    return _WEEKDAY_MAP.get(d)


def default_local_tz():
    """Best-effort system local zone for scheduling."""
    now = datetime.now(timezone.utc).astimezone()
    return now.tzinfo or timezone.utc


def _monthly_slot_local(now_local: datetime, day_num: int, hour: int, minute: int) -> datetime:
    y, m = now_local.year, now_local.month
    last_d = calendar.monthrange(y, m)[1]
    d = max(1, min(int(day_num), last_d))
    return now_local.replace(day=d, hour=hour, minute=minute, second=0, microsecond=0)


def task_is_due(
    task: Any,
    *,
    now_utc: datetime,
    local_tz,
) -> bool:
    """
    Return True if an enabled task should run now.

    *task* must expose: frequency, time, day, last_run, enabled.
    """
    if not getattr(task, "enabled", False):
        return False

    freq = (getattr(task, "frequency", None) or "").strip().lower()
    last = _as_utc(getattr(task, "last_run", None))
    now_local = now_utc.astimezone(local_tz)

    if freq == "hourly":
        if last is None:
            return True
        return (now_utc - last) >= timedelta(hours=1)

    hm = _parse_hhmm(getattr(task, "time", None))
    if hm is None:
        logger.debug("task_is_due: missing/invalid time for non-hourly task id=%s", getattr(task, "id", "?"))
        return False

    hour, minute = hm

    if freq == "daily":
        slot_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now_local < slot_local:
            return False
        slot_start_utc = slot_local.astimezone(timezone.utc)
        return last is None or last < slot_start_utc

    if freq == "weekly":
        want = _weekday_index(getattr(task, "day", None))
        if want is None:
            return False
        if now_local.weekday() != want:
            return False
        slot_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now_local < slot_local:
            return False
        slot_start_utc = slot_local.astimezone(timezone.utc)
        return last is None or last < slot_start_utc

    if freq == "monthly":
        raw_day = getattr(task, "day", None)
        try:
            day_num = int(str(raw_day).strip())
        except (TypeError, ValueError):
            return False
        slot_local = _monthly_slot_local(now_local, day_num, hour, minute)
        if now_local.date() != slot_local.date():
            return False
        if now_local < slot_local:
            return False
        slot_start_utc = slot_local.astimezone(timezone.utc)
        return last is None or last < slot_start_utc

    logger.debug("task_is_due: unknown frequency %r id=%s", freq, getattr(task, "id", "?"))
    return False


def iter_due_proactive_tasks(session, *, now_utc: datetime | None = None, local_tz=None):
    """Yield ORM rows that are due, ordered by priority then id."""
    from distr.core.db.proactive import ProactiveTask

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if local_tz is None:
        local_tz = default_local_tz()

    rows = (
        session.query(ProactiveTask)
        .filter(ProactiveTask.enabled.is_(True))
        .order_by(ProactiveTask.priority.asc(), ProactiveTask.id.asc())
        .all()
    )
    for row in rows:
        if task_is_due(row, now_utc=now_utc, local_tz=local_tz):
            yield row


def mark_proactive_task_run(session, task_id: int, *, now_utc: datetime | None = None) -> None:
    """Set last_run / run_count after an attempt."""
    from distr.core.db.proactive import ProactiveTask

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    row = session.query(ProactiveTask).filter(ProactiveTask.id == task_id).first()
    if not row:
        return
    row.last_run = now_utc.replace(tzinfo=None) if now_utc.tzinfo else now_utc
    row.run_count = int(row.run_count or 0) + 1
    session.commit()
