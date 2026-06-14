"""Proactive scheduled tasks (R3) — DB-backed definitions for initiative scheduler."""

from __future__ import annotations

import json
import logging

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from distr.core.db import Base
from distr.core.db.time import utc_now_naive

logger = logging.getLogger(__name__)


class ProactiveTask(Base):
    """
    User- or system-defined cadence work executed by the initiative stack.

    ``time`` is local HH:MM; ``last_run`` is stored UTC (timezone-aware naive UTC ok).
    """

    __tablename__ = "proactive_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    frequency = Column(String, nullable=False)  # hourly | daily | weekly | monthly
    time = Column(String, nullable=True)  # HH:MM local; None for hourly
    day = Column(String, nullable=True)  # monday..sunday / 1-31
    instruction = Column(Text, nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=True)
    priority = Column(Integer, nullable=False, default=50)  # lower = sooner in queue
    tier = Column(Integer, nullable=False, default=1)  # PermissionTier 0-3
    last_run = Column(DateTime, nullable=True)
    run_count = Column(Integer, nullable=False, default=0)
    conditions = Column(Text, nullable=False, default="{}")  # JSON
    outcome_history = Column(Text, nullable=False, default="[]")  # JSON
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)


# Single owner for seed rows (DESIGN §2.3). Names are soft-unique for idempotent upsert.
SYSTEM_PROACTIVE_TASK_SPECS: tuple[dict, ...] = (
    {
        "name": "Memory Distillation",
        "frequency": "daily",
        "time": "00:00",
        "day": None,
        "instruction": (
            "Review EVENTS.md and distill durable memories into MEMORY.md per product policy. "
            "(No-op until memory subsystem is enabled.)"
        ),
        "enabled": False,
        "priority": 1,
        "tier": 0,
    },
    {
        "name": "Morning Brief",
        "frequency": "daily",
        "time": "07:00",
        "day": None,
        "instruction": (
            "Produce a concise morning brief: calendar highlights, stuck tickets, "
            "and one suggested priority for today."
        ),
        "enabled": True,
        "priority": 10,
        "tier": 1,
    },
    {
        "name": "Day Planner",
        "frequency": "daily",
        "time": "07:00",
        "day": None,
        "instruction": (
            "Draft today's structured plan (priorities, calendar blocks, stuck items) "
            "as markdown suitable for chat + optional voice readout."
        ),
        "enabled": True,
        "priority": 11,
        "tier": 1,
    },
    {
        "name": "Week Planner",
        "frequency": "weekly",
        "time": "08:00",
        "day": "monday",
        "instruction": (
            "Produce a weekly outlook: themes, deadlines, and backlog suggestions."
        ),
        "enabled": True,
        "priority": 20,
        "tier": 1,
    },
    {
        "name": "Month Planner",
        "frequency": "monthly",
        "time": "09:00",
        "day": "1",
        "instruction": (
            "Produce a monthly goals recap and adjustments based on open work."
        ),
        "enabled": True,
        "priority": 30,
        "tier": 1,
    },
)


def ensure_system_proactive_tasks(session) -> int:
    """Insert bundled system tasks once each (by name). Returns number of rows added."""
    inserted = 0
    for spec in SYSTEM_PROACTIVE_TASK_SPECS:
        exists = session.query(ProactiveTask).filter(ProactiveTask.name == spec["name"]).first()
        if exists:
            continue
        row = ProactiveTask(
            name=spec["name"],
            frequency=spec["frequency"],
            time=spec["time"],
            day=spec["day"],
            instruction=spec["instruction"],
            enabled=spec["enabled"],
            priority=spec["priority"],
            tier=spec["tier"],
            conditions=json.dumps({"system": True}),
            outcome_history=json.dumps([]),
        )
        session.add(row)
        inserted += 1
        logger.info("ensure_system_proactive_tasks: inserted %r", spec["name"])
    if inserted:
        session.commit()
    return inserted
