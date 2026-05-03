"""Tests for proactive task due detection (R3)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from distr.core.initiative.scheduler import (
    default_local_tz,
    iter_due_proactive_tasks,
    task_is_due,
)
from distr.core.db.proactive import ProactiveTask


@pytest.fixture()
def utc():
    return timezone.utc


def test_hourly_due_first_run(utc):
    t = SimpleNamespace(
        enabled=True,
        frequency="hourly",
        time=None,
        day=None,
        last_run=None,
    )
    assert task_is_due(t, now_utc=datetime(2026, 5, 1, 12, 0, tzinfo=utc), local_tz=utc)


def test_hourly_not_due_within_hour(utc):
    t = SimpleNamespace(
        enabled=True,
        frequency="hourly",
        time=None,
        day=None,
        last_run=datetime(2026, 5, 1, 11, 30, tzinfo=utc),
    )
    assert not task_is_due(t, now_utc=datetime(2026, 5, 1, 12, 0, tzinfo=utc), local_tz=utc)


def test_daily_due_after_slot(utc):
    t = SimpleNamespace(
        enabled=True,
        frequency="daily",
        time="07:00",
        day=None,
        last_run=None,
    )
    assert task_is_due(
        t,
        now_utc=datetime(2026, 5, 1, 8, 0, tzinfo=utc),
        local_tz=utc,
    )


def test_daily_not_due_before_slot(utc):
    t = SimpleNamespace(
        enabled=True,
        frequency="daily",
        time="07:00",
        day=None,
        last_run=None,
    )
    assert not task_is_due(
        t,
        now_utc=datetime(2026, 5, 1, 6, 0, tzinfo=utc),
        local_tz=utc,
    )


def test_daily_not_due_already_ran_morning(utc):
    t = SimpleNamespace(
        enabled=True,
        frequency="daily",
        time="07:00",
        day=None,
        last_run=datetime(2026, 5, 1, 7, 5, tzinfo=utc),
    )
    assert not task_is_due(
        t,
        now_utc=datetime(2026, 5, 1, 8, 0, tzinfo=utc),
        local_tz=utc,
    )


def test_weekly_wrong_weekday(utc):
    # 2026-05-01 is Friday
    t = SimpleNamespace(
        enabled=True,
        frequency="weekly",
        time="08:00",
        day="monday",
        last_run=None,
    )
    assert not task_is_due(
        t,
        now_utc=datetime(2026, 5, 1, 9, 0, tzinfo=utc),
        local_tz=utc,
    )


def test_monthly_first_of_month(utc):
    t = SimpleNamespace(
        enabled=True,
        frequency="monthly",
        time="09:00",
        day="1",
        last_run=None,
    )
    assert task_is_due(
        t,
        now_utc=datetime(2026, 5, 1, 10, 0, tzinfo=utc),
        local_tz=utc,
    )


def test_iter_due_sqlite(tmp_path, monkeypatch):
    """Integration: ORM rows against temp SQLite DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from distr.core.db import Base

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        session.add(
            ProactiveTask(
                name="test_daily",
                frequency="daily",
                time="06:00",
                instruction="hello",
                enabled=True,
                priority=1,
                tier=1,
            )
        )
        session.commit()

    utc = timezone.utc
    with Session() as session:
        due = list(
            iter_due_proactive_tasks(
                session,
                now_utc=datetime(2026, 5, 2, 7, 0, tzinfo=utc),
                local_tz=utc,
            )
        )
    assert len(due) == 1
    assert due[0].name == "test_daily"


def test_default_local_tz_callable():
    assert default_local_tz() is not None
