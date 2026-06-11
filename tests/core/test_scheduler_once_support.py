from datetime import datetime
import contextlib
import builtins
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base


def test_once_schedule_to_cron_returns_marker():
    from distr.core.workflow.scheduler import schedule_to_cron

    assert schedule_to_cron("once", "2026-06-02T13:05:00") == "once:2026-06-02T13:05:00"


def test_schedule_to_cron_normalizes_human_time_separators():
    from distr.core.workflow.scheduler import schedule_to_cron

    assert schedule_to_cron("daily", "9/20") == "20 9 * * *"
    assert schedule_to_cron("daily", "920") == "20 9 * * *"
    assert schedule_to_cron("weekly", "9.20", schedule_days="2") == "20 9 * * 2"


def test_next_run_from_once_iso_returns_requested_datetime():
    from distr.core.workflow.scheduler import _next_run_from_cron

    result = _next_run_from_cron("once:2026-06-02T13:05:00Z")

    assert result == datetime(2026, 6, 2, 13, 5, 0)


def test_next_run_from_daily_cron_falls_back_when_croniter_missing(monkeypatch):
    from distr.core.workflow.scheduler import _next_run_from_cron

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "croniter":
            raise ModuleNotFoundError("No module named 'croniter'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = _next_run_from_cron(
        "15 10 * * *",
        from_dt=datetime(2026, 6, 2, 8, 0, 0),
    )

    assert result == datetime(2026, 6, 2, 8, 15, 0)


def test_next_run_from_weekday_cron_falls_back_when_croniter_missing(monkeypatch):
    from distr.core.workflow.scheduler import _next_run_from_cron

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "croniter":
            raise ModuleNotFoundError("No module named 'croniter'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = _next_run_from_cron(
        "30 8 * * 1,2,3,4,5",
        from_dt=datetime(2026, 6, 6, 9, 0, 0),
    )

    assert result == datetime(2026, 6, 8, 6, 30, 0)


def test_advance_next_run_disables_once_workflow_after_fire():
    from distr.core.db.workflow import AutoWorkflow
    from distr.core.workflow.scheduler import _advance_next_run

    wf = AutoWorkflow(
        id=1,
        schedule_enabled=True,
        schedule_preset="once",
        schedule_time="2026-06-02T13:05:00",
    )

    _advance_next_run(wf)

    assert wf.schedule_enabled is False
    assert wf.next_run_at is None


def _scheduler_session_ctx_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def session_ctx():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return session_ctx, factory


def test_run_scheduled_workflow_skips_when_required_app_not_foreground(monkeypatch):
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow.scheduler import run_scheduled_workflow

    session_ctx, factory = _scheduler_session_ctx_factory()
    with factory() as db:
        wf = AutoWorkflow(
            name="Press Enter",
            workflow_type="scheduled",
            schedule_enabled=True,
            schedule_preset="once",
            schedule_time="2026-06-02T13:05:00",
            next_run_at=datetime(2026, 6, 2, 13, 5, 0),
        )
        db.add(wf)
        db.flush()
        db.add(AutoWorkflowStep(
            workflow_id=wf.id,
            position=0,
            name="Press Enter",
            action_type="computer_use",
            instruction="Press enter.",
            config=json.dumps({
                "scheduled_action": {"type": "keypress", "key": "enter"},
                "target_context": {"app_name": "Chrome"},
                "safety": {"require_app_in_foreground": True},
            }),
        ))
        db.commit()
        workflow_id = wf.id

    monkeypatch.setattr("distr.core.workflow.scheduler.get_session", session_ctx)
    monkeypatch.setattr("distr.core.workflow.scheduler._is_target_app_frontmost", lambda app: False, raising=False)
    started = []
    monkeypatch.setattr(
        "distr.core.workflow.service.start_workflow_run",
        lambda *args, **kwargs: started.append((args, kwargs)) or {"run_id": 99},
    )

    assert run_scheduled_workflow(workflow_id) is True
    assert started == []

    with factory() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
        assert wf.schedule_enabled is False
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.workflow_id == workflow_id).one()
        assert run.status == "skipped"
        run_data = json.loads(run.run_data)
        assert run_data["phase"] == "scheduled_action"
        assert run_data["result"] == "skipped"
        assert "Chrome" in run_data["message"]


def test_run_scheduled_workflow_focuses_target_app_before_start(monkeypatch):
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow.scheduler import run_scheduled_workflow

    session_ctx, factory = _scheduler_session_ctx_factory()
    with factory() as db:
        wf = AutoWorkflow(
            name="Open dashboard",
            workflow_type="scheduled",
            schedule_enabled=True,
            schedule_preset="daily",
            schedule_time="08:30",
            next_run_at=datetime(2025, 6, 2, 8, 30, 0),
        )
        db.add(wf)
        db.flush()
        db.add(AutoWorkflowStep(
            workflow_id=wf.id,
            position=0,
            name="Open dashboard",
            action_type="computer_use",
            instruction="Bring the target app to the front first. Open Chrome.",
            config=json.dumps({
                "scheduled_action": {"type": "open_app", "app_name": "Chrome"},
                "target_context": {"app_name": "Chrome"},
                "safety": {"bring_app_to_front": True},
            }),
        ))
        db.commit()
        workflow_id = wf.id

    monkeypatch.setattr("distr.core.workflow.scheduler.get_session", session_ctx)
    opened = []
    monkeypatch.setattr("distr.core.actions.desktop.open_app", lambda app: opened.append(app), raising=False)
    started = []
    monkeypatch.setattr(
        "distr.core.workflow.service.start_workflow_run",
        lambda *args, **kwargs: started.append((args, kwargs)) or {"run_id": 42},
    )

    assert run_scheduled_workflow(workflow_id) is True
    assert opened == ["Chrome"]
    assert started == []
    with factory() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.workflow_id == workflow_id).one()
        run_data = json.loads(run.run_data)
        assert run.status == "completed"
        assert run_data["execution_mode"] == "direct_desktop_action"
        assert run_data["result_packet"]["summary"].startswith("Opened Chrome")


def test_run_scheduled_workflow_marks_overdue_run_as_late(monkeypatch):
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow.scheduler import run_scheduled_workflow

    session_ctx, factory = _scheduler_session_ctx_factory()
    with factory() as db:
        wf = AutoWorkflow(
            name="Open Chrome",
            workflow_type="scheduled",
            schedule_enabled=True,
            schedule_preset="daily",
            schedule_time="08:30",
            next_run_at=datetime(2025, 6, 2, 8, 30, 0),
        )
        db.add(wf)
        db.flush()
        db.add(AutoWorkflowStep(
            workflow_id=wf.id,
            position=0,
            name="Open Chrome",
            action_type="computer_use",
            instruction="Open Chrome.",
            config=json.dumps({"scheduled_action": {"type": "open_app", "app_name": "Chrome"}}),
        ))
        db.commit()
        workflow_id = wf.id

    monkeypatch.setattr("distr.core.workflow.scheduler.get_session", session_ctx)
    monkeypatch.setattr("distr.core.actions.desktop.open_app", lambda app: None, raising=False)

    assert run_scheduled_workflow(workflow_id) is True
    with factory() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.workflow_id == workflow_id).one()
        metadata = json.loads(run.run_data)
    assert metadata["scheduled_due_at"] == "2025-06-02T08:30:00"
    assert metadata["late"] is True
    assert metadata["late_policy"] == "run_as_soon_as_possible"
    assert metadata["phase"] == "scheduled_action"


def test_run_scheduled_workflow_passes_event_queue_to_service(monkeypatch):
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
    from distr.core.workflow.scheduler import run_scheduled_workflow

    session_ctx, factory = _scheduler_session_ctx_factory()
    with factory() as db:
        wf = AutoWorkflow(
            name="Voice reminder",
            workflow_type="scheduled",
            schedule_enabled=True,
            schedule_preset="daily",
            schedule_time="21:05",
            next_run_at=datetime(2026, 6, 4, 21, 5, 0),
        )
        db.add(wf)
        db.flush()
        db.add(AutoWorkflowStep(
            workflow_id=wf.id,
            position=0,
            name="Automation Instruction",
            action_type="agent_instruction",
            instruction="Send a voice note to Telegram.",
        ))
        db.commit()
        workflow_id = wf.id

    monkeypatch.setattr("distr.core.workflow.scheduler.get_session", session_ctx)
    started = []
    monkeypatch.setattr(
        "distr.core.workflow.service.start_workflow_run",
        lambda *args, **kwargs: started.append((args, kwargs)) or {"run_id": 150},
    )
    event_queue = object()

    assert run_scheduled_workflow(workflow_id, event_queue=event_queue) is True

    assert started
    assert started[0][1]["event_queue"] is event_queue
    assert started[0][1]["run_metadata"]["source_type"] == "scheduled"
