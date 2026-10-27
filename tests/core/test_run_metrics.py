"""A run must be measurable: sessions, models, tokens and wall time."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.projects import Project
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowRun,
    AutoWorkflowStep,
    AutoWorkflowStepResult,
)
from distr.core.workflow import run_metrics


@pytest.fixture()
def db_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    from distr.core.cost_ledger.schema import ensure_tables

    ensure_tables(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("distr.core.db.get_session", lambda: factory())
    return factory


def _seed_run(factory, *, sessions=1, steps=1, tokens_in=100, tokens_out=50) -> int:
    from distr.core.db.kanban import ProjectExecutionSession

    with factory() as db:
        project = Project(name="Metrics project", folder_location="/tmp/metrics")
        db.add(project)
        db.flush()
        workflow = AutoWorkflow(name="Ticket: standard (one session)", status="active")
        db.add(workflow)
        db.flush()
        started = datetime(2026, 10, 20, 10, 0, 0)
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="completed",
            started_at=started,
            completed_at=started + timedelta(minutes=30),
        )
        db.add(run)
        db.flush()
        step = AutoWorkflowStep(workflow_id=workflow.id, position=0, name="Session")
        db.add(step)
        db.flush()
        for _ in range(steps):
            db.add(AutoWorkflowStepResult(step_id=step.id, run_id=run.id, status="passed", agent_response="ok"))
        for _ in range(sessions):
            db.add(
                ProjectExecutionSession(
                    project_id=project.id,
                    workflow_id=workflow.id,
                    run_id=run.id,
                    route_type="project_cli",
                    route_backend="cursor",
                    selected_model="cursor-grok-4.6-medium",
                    status="completed",
                )
            )
        db.flush()
        db.execute(
            text(
                "INSERT INTO cost_ledger_entries "
                "(run_id, recorded_at, tokens_in, tokens_out, tokens_total, source) "
                "VALUES (:rid, :now, :ti, :to, :tt, 'workflow')"
            ),
            {
                "rid": int(run.id),
                "now": 1.0,
                "ti": int(tokens_in),
                "to": int(tokens_out),
                "tt": int(tokens_in) + int(tokens_out),
            },
        )
        db.commit()
        return int(run.id)


def test_run_metrics_reports_sessions_models_tokens_and_wall_time(db_factory):
    run_id = _seed_run(db_factory, sessions=2, steps=3, tokens_in=1000, tokens_out=250)

    metrics = run_metrics.run_metrics(run_id)

    assert metrics["sessions"] == 2
    assert metrics["step_executions"] == 3
    assert metrics["tokens_in"] == 1000
    assert metrics["tokens_out"] == 250
    assert metrics["tokens_total"] == 1250
    assert metrics["wall_seconds"] == 1800.0
    assert metrics["models"] == {"cursor:cursor-grok-4.6-medium": 2}
    assert metrics["status"] == "completed"


def test_unknown_run_reports_an_error(db_factory):
    assert run_metrics.run_metrics(4242)["error"] == "run not found"


def test_run_with_no_ledger_rows_does_not_invent_tokens(db_factory):
    run_id = _seed_run(db_factory)
    with db_factory() as db:
        db.execute(text("DELETE FROM cost_ledger_entries"))
        db.commit()

    metrics = run_metrics.run_metrics(run_id)

    assert metrics["tokens_total"] == 0
    assert metrics["ledger_entries"] == 0


def test_compare_runs_reports_deltas(db_factory):
    baseline = _seed_run(db_factory, sessions=4, steps=6, tokens_in=4000, tokens_out=1000)
    candidate = _seed_run(db_factory, sessions=1, steps=1, tokens_in=500, tokens_out=100)

    compare = run_metrics.compare_runs(baseline, candidate)

    assert compare["delta"]["sessions"] == -3
    assert compare["delta"]["step_executions"] == -5
    assert compare["delta"]["tokens_total"] == -4400


def test_formatted_report_names_the_numbers(db_factory):
    run_id = _seed_run(db_factory)

    report = run_metrics._format_metrics(run_metrics.run_metrics(run_id))

    assert "sessions" in report
    assert "tokens in/out" in report
    assert "cursor:cursor-grok-4.6-medium" in report


def test_main_requires_run_ids():
    assert run_metrics.main([]) == 2
    assert run_metrics.main(["not-an-int"]) == 2
