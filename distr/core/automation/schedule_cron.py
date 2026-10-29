"""autocog 2026-10-22: calendar schedule <-> cron mapping for automations.

The automation row stores the schedule as preset/time/days columns plus the
normalized dict in ``schedule_config``; cron is derived from it whenever
next_run_at is computed. This module owns the calendar kinds the schedule
modal edits (daily, weekly, monthly, yearly) and maps them both ways.
"""

from __future__ import annotations

import re
from typing import Any

CALENDAR_KINDS = ("daily", "weekly", "monthly", "yearly")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


class ScheduleValidationError(ValueError):
    pass


def _parse_time(value: Any) -> tuple[int, int]:
    match = _TIME.match(str(value or "").strip())
    if not match:
        raise ScheduleValidationError(f"Invalid time {value!r}; use HH:MM")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleValidationError(f"Invalid time {value!r}; use HH:MM")
    return hour, minute


def _int_list(value: Any, low: int, high: int, label: str) -> list[int]:
    items = [part.strip() for part in str(value if value is not None else "").split(",") if part.strip()]
    if not items:
        raise ScheduleValidationError(f"Pick at least one {label}")
    out: list[int] = []
    for item in items:
        if not item.isdigit() or not (low <= int(item) <= high):
            raise ScheduleValidationError(f"Invalid {label} {item!r}")
        if int(item) not in out:
            out.append(int(item))
    return sorted(out)


def validate_calendar_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a daily/weekly/monthly/yearly schedule dict."""
    kind = str(schedule.get("kind") or "").strip().lower()
    if kind not in CALENDAR_KINDS:
        raise ScheduleValidationError(f"Unsupported schedule kind: {kind or 'empty'}")
    hour, minute = _parse_time(schedule.get("time"))
    out: dict[str, Any] = {"kind": kind, "time": f"{hour:02d}:{minute:02d}"}
    if kind == "weekly":
        # cron weekdays: 0=Sun .. 6=Sat (7 accepted as Sunday)
        days = [0 if d == 7 else d for d in _int_list(schedule.get("days"), 0, 7, "weekday")]
        out["days"] = ",".join(str(d) for d in sorted(set(days)))
    elif kind == "monthly":
        out["days"] = ",".join(str(d) for d in _int_list(schedule.get("days"), 1, 31, "day of month"))
    elif kind == "yearly":
        month = _int_list(schedule.get("month"), 1, 12, "month")
        day = _int_list(schedule.get("days"), 1, 31, "day of month")
        if len(month) != 1 or len(day) != 1:
            raise ScheduleValidationError("Yearly schedules need one month and one day")
        limits = {2: 29, 4: 30, 6: 30, 9: 30, 11: 30}
        if day[0] > limits.get(month[0], 31):
            raise ScheduleValidationError("That day does not exist in the chosen month")
        out["month"] = str(month[0])
        out["days"] = str(day[0])
    else:
        out["days"] = str(schedule.get("days") or "1")
    return out


def calendar_schedule_to_cron(schedule: dict[str, Any]) -> str:
    s = validate_calendar_schedule(schedule)
    hour, minute = _parse_time(s["time"])
    if s["kind"] == "daily":
        return f"{minute} {hour} * * *"
    if s["kind"] == "weekly":
        return f"{minute} {hour} * * {s['days']}"
    if s["kind"] == "monthly":
        return f"{minute} {hour} {s['days']} * *"
    return f"{minute} {hour} {s['days']} {s['month']} *"


def cron_to_calendar_schedule(cron: str) -> dict[str, Any]:
    parts = str(cron or "").split()
    if len(parts) != 5:
        raise ScheduleValidationError(f"Not a 5-field cron: {cron!r}")
    minute, hour, dom, month, dow = parts
    if not (minute.isdigit() and hour.isdigit()):
        raise ScheduleValidationError(f"Cron {cron!r} is not a fixed-time schedule")
    time = f"{int(hour):02d}:{int(minute):02d}"
    if dom == "*" and month == "*" and dow == "*":
        out = {"kind": "daily", "time": time}
    elif dom == "*" and month == "*":
        out = {"kind": "weekly", "time": time, "days": dow}
    elif month == "*" and dow == "*":
        out = {"kind": "monthly", "time": time, "days": dom}
    elif dow == "*":
        out = {"kind": "yearly", "time": time, "days": dom, "month": month}
    else:
        raise ScheduleValidationError(f"Cron {cron!r} has no calendar equivalent")
    return validate_calendar_schedule(out)


def validate_cron_expression(expr: Any, timezone: Any = None) -> str:
    """gap1 2026-10-29: validate a raw 5-field cron (ranges, lists, steps) and its timezone."""
    text = " ".join(str(expr or "").split())
    if len(text.split()) != 5:
        raise ScheduleValidationError(f"Cron must have 5 fields: {expr!r}")
    try:
        from croniter import croniter
    except ImportError as exc:  # pragma: no cover
        raise ScheduleValidationError("croniter is not installed") from exc
    if not croniter.is_valid(text):
        raise ScheduleValidationError(f"Invalid cron expression: {expr!r}")
    tz = str(timezone or "").strip()
    if tz:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(tz)
        except Exception as exc:
            raise ScheduleValidationError(f"Unknown timezone: {tz!r}") from exc
    return text
