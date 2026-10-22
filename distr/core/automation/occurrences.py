"""calendar 2026-10-22: per-occurrence "Ignore" for recurring automations.

Storage: ``schedule["ignored_occurrences"]`` inside the automation's schedule JSON
(``schedule_config`` column) -- a sorted list of local wall-clock keys
``YYYY-MM-DDTHH:MM`` in the schedule's timezone. No new table/migration.
next_run_at skips ignored keys, so the scheduler never claims them; the claim
path re-checks as a guard.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Iterable

IGNORABLE_KINDS = {"daily", "weekly", "monthly", "yearly"}
MAX_IGNORED = 200
_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


def normalize_ignored(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    out = sorted({str(v).strip()[:16] for v in values if _KEY.match(str(v).strip()[:16])})
    return out[-MAX_IGNORED:]


def valid_key(value: str) -> str:
    key = str(value or "").strip()[:16]
    if not _KEY.match(key):
        raise ValueError("Occurrence must look like YYYY-MM-DDTHH:MM")
    datetime.strptime(key, "%Y-%m-%dT%H:%M")
    return key


def occurrence_key(next_run_utc: datetime, timezone: str | None) -> str:
    from distr.core.workflow.scheduler import _utc_naive_to_local_wall

    return _utc_naive_to_local_wall(next_run_utc, timezone).strftime("%Y-%m-%dT%H:%M")


def schedule_cron(schedule: dict[str, Any]) -> str | None:
    kind = str(schedule.get("kind") or "")
    if kind == "yearly":
        from distr.core.automation.schedule_cron import calendar_schedule_to_cron

        return calendar_schedule_to_cron(schedule)
    from distr.core.workflow.scheduler import schedule_to_cron

    return schedule_to_cron(kind, schedule.get("time"), schedule.get("timezone"), schedule.get("days"))


def skip_ignored(next_run: datetime | None, schedule: dict[str, Any], *, guard: int = 400) -> datetime | None:
    """Advance ``next_run`` (UTC naive) past every ignored occurrence."""
    ignored = set(normalize_ignored(schedule.get("ignored_occurrences")))
    if next_run is None or not ignored or str(schedule.get("kind") or "") not in IGNORABLE_KINDS:
        return next_run
    from distr.core.workflow.scheduler import _next_run_from_cron

    cron = schedule_cron(schedule)
    tz = schedule.get("timezone") or None
    steps = 0
    while next_run is not None and occurrence_key(next_run, tz) in ignored and steps < guard:
        next_run = _next_run_from_cron(cron or "", next_run + timedelta(seconds=1), tz)
        steps += 1
    return next_run


def with_ignored(schedule: dict[str, Any], key: str, ignored: bool) -> dict[str, Any]:
    current: Iterable[str] = normalize_ignored(schedule.get("ignored_occurrences"))
    keys = set(current)
    (keys.add if ignored else keys.discard)(key)
    return {**schedule, "ignored_occurrences": normalize_ignored(list(keys))}
