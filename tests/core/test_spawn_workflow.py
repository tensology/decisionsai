"""Tests for dynamic ticket workflow spawning."""

from __future__ import annotations

import json

import distr.core.db.workflow  # noqa: F401
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
from distr.core.workflow.spawn_workflow import infer_preset_slug_for_ticket, spawn_workflow_for_ticket


@pytest.fixture()
def db_factory(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _get_session():
        from contextlib import contextmanager

        @contextmanager
        def ctx():
            session = factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        return ctx()

    monkeypatch.setattr("distr.core.workflow.spawn_workflow.get_session", _get_session)
    monkeypatch.setattr("distr.core.workflow.service.get_session", _get_session)
    monkeypatch.setattr("distr.core.workflow.import_export.get_session", _get_session)
    return factory


def test_infer_preset_slug_dogfood():
    assert infer_preset_slug_for_ticket(title="Dogfood catalog page", description="") == "decisionsai-dogfood-ticket"
    assert (
        infer_preset_slug_for_ticket(
            title="Home page",
            description="",
            project_folder="/Users/paul/development/TENSOLOGY/DECISIONS/DecisionsAI",
        )
        == "decisionsai-dogfood-ticket"
    )


def test_infer_preset_slug_default_development():
    assert infer_preset_slug_for_ticket(title="Add login form", description="Build auth UI") == (
        "development-ticket-to-implementation"
    )


def test_spawn_workflow_for_ticket_creates_steps_and_links(db_factory, monkeypatch):
    session = db_factory()
    try:
        project = Project(name="Spawn test project", folder_location="/tmp/spawn-test")
        session.add(project)
        board = KanbanBoard(name="Spawn board", default_project_id=None)
        session.add(board)
        session.flush()
        lane = KanbanLane(board_id=board.id, name="Ready", position=0)
        session.add(lane)
        session.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Spawn me a workflow",
            description="Acceptance: green marker file exists",
            linked_project_id=project.id,
        )
        session.add(ticket)
        session.commit()
        ticket_id = ticket.id
    finally:
        session.close()

    marker_cmd = "echo spawned-ok"

    dispatched: list[dict] = []

    def fake_start(workflow_id, **kwargs):
        dispatched.append({"workflow_id": workflow_id, **kwargs})
        return {"run_id": 999, "status": "running"}

    monkeypatch.setattr(
        "distr.core.workflow.service.start_workflow_run",
        fake_start,
    )

    result = spawn_workflow_for_ticket(
        ticket_id,
        steps=[
            {
                "position": 0,
                "name": "Spawn smoke",
                "action_type": "run_command",
                "instruction": "Echo ok",
                "config": {"command": marker_cmd, "timeout_seconds": 5},
            }
        ],
        workflow_input={"slug": "spawn-e2e-smoke", "skip_human_checkpoints": True},
        start_run=True,
        skip_human_checkpoints=True,
    )

    assert result["success"] is True
    assert result["reused"] is False
    assert result["run_id"] == 999
    assert result["step_count"] == 1
    assert dispatched

    session = db_factory()
    try:
        linked = session.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
        assert linked.linked_workflow_id == result["workflow_id"]
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == result["workflow_id"])
            .all()
        )
        assert len(steps) == 1
        assert steps[0].name == "Spawn smoke"
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == result["workflow_id"]).first()
        data = json.loads(wf.workflow_input or "{}")
        assert data.get("spawned_for_ticket_id") == ticket_id
    finally:
        session.close()


def test_spawn_reuses_existing_linked_workflow(db_factory, monkeypatch):
    session = db_factory()
    try:
        wf = AutoWorkflow(name="Existing loop", workflow_input="{}")
        session.add(wf)
        session.flush()
        board = KanbanBoard(name="Reuse board")
        session.add(board)
        session.flush()
        lane = KanbanLane(board_id=board.id, name="Ready", position=0)
        session.add(lane)
        session.flush()
        step = AutoWorkflowStep(
            workflow_id=wf.id,
            position=0,
            name="Only step",
            action_type="run_command",
            config='{"command":"echo ok"}',
        )
        session.add(step)
        session.flush()
        ticket = KanbanTicket(lane_id=lane.id, title="Reuse ticket", linked_workflow_id=wf.id)
        session.add(ticket)
        session.commit()
        ticket_id = ticket.id
        workflow_id = wf.id
    finally:
        session.close()

    monkeypatch.setattr(
        "distr.core.workflow.service.start_workflow_run",
        lambda *a, **k: {"run_id": 42, "status": "running"},
    )

    result = spawn_workflow_for_ticket(ticket_id, start_run=True)
    assert result["success"] is True
    assert result["reused"] is True
    assert result["workflow_id"] == workflow_id
    assert result["run_id"] == 42
