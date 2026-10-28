"""Persisted managed workflows must pick up phase-contract improvements."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep


@pytest.fixture()
def db_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("distr.core.db.get_session", lambda: factory())
    return factory


def test_refresh_rewrites_instructions_and_keeps_step_ids(db_factory):
    from distr.core.workflow import phases

    with db_factory() as db:
        workflow = AutoWorkflow(
            name="Ticket: standard (one session)",
            status="active",
            workflow_type="manual",
            run_settings=json.dumps({"managed_by": "phase_engine", "weight_class": "standard"}),
        )
        db.add(workflow)
        db.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            position=0,
            name="Stale session",
            instruction="Old instruction with no reply contract.",
        )
        db.add(step)
        db.commit()
        step_id = int(step.id)

    updated = phases.refresh_managed_workflow_instructions()

    assert updated.get("standard") == 1
    with db_factory() as db:
        refreshed = db.get(AutoWorkflowStep, step_id)
        assert refreshed is not None, "step must be updated in place, not replaced"
        assert "reply_text:" in refreshed.instruction
        assert phases.REPLY_PHASE_MARKER in refreshed.instruction


def test_refresh_skips_workflows_that_are_not_managed(db_factory):
    from distr.core.workflow import phases

    with db_factory() as db:
        workflow = AutoWorkflow(name="Hand-rolled", status="active", run_settings="{}")
        db.add(workflow)
        db.flush()
        db.add(
            AutoWorkflowStep(
                workflow_id=workflow.id,
                position=0,
                name="Untouched",
                instruction="Leave me alone.",
            )
        )
        db.commit()

    updated = phases.refresh_managed_workflow_instructions()

    assert updated == {}
