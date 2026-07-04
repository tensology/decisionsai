from datetime import datetime, timedelta
from unittest.mock import patch

from distr.core.db.workflow import AutoWorkflow


def test_advance_next_run_daily_schedule_is_strictly_in_the_future():
    from distr.core.workflow.scheduler import _advance_next_run

    wf = AutoWorkflow(
        id=2,
        schedule_enabled=True,
        schedule_preset="daily",
        schedule_time="09:00",
        last_run_at=datetime(2026, 6, 10, 9, 0, 0),
        next_run_at=datetime(2026, 6, 11, 9, 0, 0),
    )
    fixed_now = datetime(2026, 6, 11, 9, 5, 0)

    with patch("distr.core.workflow.scheduler.datetime") as mock_datetime:
        mock_datetime.utcnow.return_value = fixed_now
        _advance_next_run(wf)

    assert wf.next_run_at is not None
    assert wf.next_run_at > fixed_now


def test_advance_next_run_interval_uses_current_time_not_last_run():
    from distr.core.workflow.scheduler import _advance_next_run

    wf = AutoWorkflow(
        id=1,
        schedule_enabled=True,
        schedule_preset="interval",
        schedule_time="15:minutes",
        last_run_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    fixed_now = datetime(2026, 6, 2, 12, 0, 0)

    with patch("distr.core.workflow.scheduler.datetime") as mock_datetime:
        mock_datetime.utcnow.return_value = fixed_now
        _advance_next_run(wf)

    assert wf.next_run_at == fixed_now + timedelta(minutes=15)


def test_get_workflow_scheduler_interval_ms_speeds_up_for_subminute_seconds_only():
    from distr.core.workflow.scheduler import get_workflow_scheduler_interval_ms

    with patch(
        "distr.core.workflow.scheduler.get_minimum_schedule_interval_seconds",
        side_effect=lambda subminute_only=False: 15 if subminute_only else None,
    ):
        assert get_workflow_scheduler_interval_ms() == 5000

    with patch(
        "distr.core.workflow.scheduler.get_minimum_schedule_interval_seconds",
        return_value=None,
    ), patch(
        "distr.core.workflow.scheduler.get_seconds_until_next_due_workflow",
        return_value=None,
    ):
        assert get_workflow_scheduler_interval_ms() == 300_000

    with patch(
        "distr.core.workflow.scheduler.get_minimum_schedule_interval_seconds",
        return_value=None,
    ), patch(
        "distr.core.workflow.scheduler.get_seconds_until_next_due_workflow",
        return_value=7200.0,
    ):
        assert get_workflow_scheduler_interval_ms() == 300_000

    with patch(
        "distr.core.workflow.scheduler.get_minimum_schedule_interval_seconds",
        return_value=None,
    ), patch(
        "distr.core.workflow.scheduler.get_seconds_until_next_due_workflow",
        return_value=45.0,
    ):
        assert get_workflow_scheduler_interval_ms() == 45_000


def test_once_run_at_local_input_is_converted_to_utc_storage():
    from distr.core.workflow.scheduler import (
        normalize_once_run_at_storage,
        once_run_at_for_datetime_local_input,
        parse_once_run_at_as_utc,
    )

    with patch("distr.core.workflow.scheduler._utc_offset", return_value=timedelta(hours=2)):
        stored = normalize_once_run_at_storage("2026-06-02T13:05:00")
        assert stored == "2026-06-02T11:05:00Z"
        assert parse_once_run_at_as_utc(stored) == datetime(2026, 6, 2, 11, 5, 0)
        assert once_run_at_for_datetime_local_input(stored) == "2026-06-02T13:05"


def test_automations_api_once_schedule_round_trips_local_run_at(monkeypatch):
    from distr.core.db import get_session
    from distr.core.db.automation import Automation
    from distr.gui.web.routes.automations import create_routes

    monkeypatch.setattr(
        "distr.core.workflow.scheduler._utc_offset",
        lambda: timedelta(hours=2),
    )

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    create_resp = client.post(
        "/api/automations",
        json={
            "name": "One Shot",
            "instruction": "Do the thing once.",
            "schedule": {"kind": "once", "run_at": "2026-06-02T13:05:00"},
        },
    )
    assert create_resp.status_code == 200
    automation = create_resp.json()["automation"]
    assert str(automation["id"]).startswith("auto_")
    record_id = int(automation["record_id"])
    assert automation["schedule"]["run_at"] == "2026-06-02T13:05"

    with get_session() as session:
        row = session.query(Automation).filter(Automation.id == record_id).first()
        assert row is not None
        assert row.schedule_time == "2026-06-02T11:05:00Z"
        session.delete(row)
        session.commit()
