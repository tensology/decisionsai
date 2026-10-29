"""Advance automation next_run_at — mirrors workflow scheduler logic."""

from __future__ import annotations

from datetime import datetime, timedelta

from distr.core.db.automation import Automation
from distr.core.workflow.scheduler import (
    _next_run_from_cron,
    interval_timedelta,
    parse_interval_schedule,
    schedule_to_cron,
)


def advance_next_run_for_automation(row: Automation) -> None:
    preset = str(row.schedule_preset or "").strip().lower()
    if preset == "once":
        row.schedule_enabled = False
        row.next_run_at = None
        row.last_run_at = datetime.utcnow()
        return
    if preset == "interval":
        parsed = parse_interval_schedule(row.schedule_time)
        if parsed:
            value, unit = parsed
            base = datetime.utcnow()
            row.next_run_at = base + interval_timedelta(value, unit)
            return
    if preset == "cron":
        # gap1: raw cron + timezone lives in schedule_config.
        from distr.core.automation.store import schedule_dict_from_row

        cron = schedule_dict_from_row(row).get("cron") or None
    elif preset == "yearly":
        # autocog: yearly lives only in schedule_config (needs the month).
        from distr.core.automation.schedule_cron import calendar_schedule_to_cron
        from distr.core.automation.store import schedule_dict_from_row

        try:
            cron = calendar_schedule_to_cron(schedule_dict_from_row(row))
        except ValueError:
            cron = None
    else:
        cron = schedule_to_cron(
            row.schedule_preset,
            row.schedule_time,
            row.schedule_timezone,
            row.schedule_days,
        )
    if not cron:
        row.next_run_at = None
        return
    now = datetime.utcnow()
    next_run = _next_run_from_cron(cron, now, row.schedule_timezone)
    guard = 0
    while next_run and next_run <= now and guard < 8:
        next_run = _next_run_from_cron(
            cron,
            next_run + timedelta(seconds=1),
            row.schedule_timezone,
        )
        guard += 1
    # calendar: never land on an ignored occurrence.
    from distr.core.automation.occurrences import skip_ignored
    from distr.core.automation.store import schedule_dict_from_row

    row.next_run_at = skip_ignored(next_run, schedule_dict_from_row(row))
