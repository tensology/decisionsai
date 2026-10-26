"""Scope safety and phrasing coverage for the work-intake router.

Regression context: unscoped work silently landed on the first board by
position, and a project whose board had no workflow raised a technical error
("Board 'Spoortrack (Old)' has no default workflow"). Both must now ask the
user which project/board to use instead of guessing or failing.
"""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.work_intake import WorkIntake, WorkIntakeAction
from distr.core.work_intake.service import OrchestratorIntakeService


def _factory():
    from distr.core.db import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _patch(monkeypatch, factory):
    monkeypatch.setattr(
        "distr.core.work_intake.service.get_session",
        lambda: _session_ctx(factory),
    )
    monkeypatch.setattr("distr.core.orchestrator.emit_channel_intake_event", lambda **k: None)


def _seed(factory, *, projects, boards, lanes=None, workflows=None):
    """projects: list of (name, in_use); boards: list of dict(name, project, in_use, workflow)."""
    from distr.core.db.kanban import KanbanBoard, KanbanLane
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow

    with _session_ctx(factory) as session:
        project_rows = []
        for name, in_use in projects:
            p = Project(name=name, folder_location=f"/tmp/{name.lower()}", in_use=in_use)
            session.add(p)
            project_rows.append(p)
        session.flush()
        workflow_rows = []
        for wname in workflows or []:
            w = AutoWorkflow(name=wname, status="active")
            session.add(w)
            workflow_rows.append(w)
        session.flush()
        board_rows = []
        for index, spec in enumerate(boards):
            project = project_rows[spec["project"]] if spec.get("project") is not None else None
            workflow = workflow_rows[spec["workflow"]] if spec.get("workflow") is not None else None
            b = KanbanBoard(
                name=spec["name"],
                default_project_id=project.id if project else None,
                default_workflow_id=workflow.id if workflow else None,
                in_use=spec.get("in_use", False),
                position=index,
            )
            session.add(b)
            session.flush()
            session.add(KanbanLane(board_id=b.id, name="Backlog", position=0))
            if project is not None:
                project.kanban_board_id = b.id
            board_rows.append(b)
        session.flush()
        ids = {
            "projects": [p.id for p in project_rows],
            "boards": [b.id for b in board_rows],
            "workflows": [w.id for w in workflow_rows],
        }
    return ids


def _ticket_count(factory):
    from distr.core.db.kanban import KanbanTicket

    with _session_ctx(factory) as session:
        return session.query(KanbanTicket).count()


# --- classifier: natural phrasings must route -------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "go and fix the login bug in Merrypak",
        "please go and update the docs",
        "sort out the login bug",
        "look into the redis error",
        "have a look at the failing tests",
        "deal with the broken import in Player1Sport",
        "the login is broken",
        "there's a bug in the Merrypak checkout",
        "Tensology: fix the redis issue",
        "start the merrypak project",
        "deploy to production",
        "can you start working on the auctionnow pr",
    ],
)
def test_natural_work_phrasings_route(text):
    decision = OrchestratorIntakeService().classify(
        WorkIntake(source="web", user_text=text)
    )
    assert decision.action in {
        WorkIntakeAction.RUN_WORKFLOW,
        WorkIntakeAction.CREATE_TICKET,
    }, f"{text!r} -> {decision.action.value}"


@pytest.mark.parametrize(
    "text",
    [
        "What projects are we working on?",
        "what's currently working?",
        "which projects are active",
        "How do I fix the login bug?",
        "Is the build broken?",
        "Does the build pass?",
        "Tell me about the project",
        "Hello?",
        "tell Greg I'll be there at five",
    ],
)
def test_questions_and_status_stay_conversational(text):
    decision = OrchestratorIntakeService().classify(
        WorkIntake(source="web", user_text=text)
    )
    assert decision.action == WorkIntakeAction.ANSWER_DIRECTLY, f"{text!r} -> {decision.action.value}"


@pytest.mark.parametrize("text", ["Run action login", "Run the action login", "Run, action, login."])
def test_desktop_action_runs_are_not_routed_as_work(text):
    decision = OrchestratorIntakeService().classify(
        WorkIntake(source="web", user_text=text)
    )
    assert decision.action == WorkIntakeAction.ANSWER_DIRECTLY, f"{text!r} -> {decision.action.value}"


def test_look_at_this_without_context_stays_with_chat():
    decision = OrchestratorIntakeService().classify(
        WorkIntake(source="web", user_text="have a look at this")
    )
    assert decision.action == WorkIntakeAction.ANSWER_DIRECTLY


# --- scope: ambiguous boards must ask, not guess ----------------------------


def test_ambiguous_scope_asks_instead_of_picking_first_board(monkeypatch):
    factory = _factory()
    _patch(monkeypatch, factory)
    ids = _seed(
        factory,
        projects=[("Alpha", False), ("Beta", False)],
        boards=[
            {"name": "Alpha Delivery", "project": 0},
            {"name": "Beta Delivery", "project": 1},
        ],
    )

    decision = OrchestratorIntakeService().ingest(
        WorkIntake(source="web", user_text="Update the docs")
    )

    assert decision.action == WorkIntakeAction.ASK_MISSING_INFO
    assert decision.status == "needs_info"
    assert decision.handled is True
    assert decision.diagnostics.get("scope_needs_info") is True
    assert decision.ticket_id is None
    assert "Alpha Delivery" in decision.response_text
    assert "Beta Delivery" in decision.response_text
    assert _ticket_count(factory) == 0


def test_named_project_does_not_trigger_scope_question(monkeypatch):
    factory = _factory()
    _patch(monkeypatch, factory)
    ids = _seed(
        factory,
        projects=[("Alpha", False), ("Beta", False)],
        boards=[
            {"name": "Alpha Delivery", "project": 0, "workflow": 0},
            {"name": "Beta Delivery", "project": 1, "workflow": 0},
        ],
        workflows=["Development"],
    )

    decision = OrchestratorIntakeService().ingest(
        WorkIntake(source="web", user_text="Update the Beta docs")
    )

    assert decision.action == WorkIntakeAction.CREATE_TICKET
    assert decision.board_id == ids["boards"][1]
    assert decision.project_id == ids["projects"][1]
    assert decision.diagnostics.get("scope_needs_info") is not True


# --- scope: project board without a workflow must ask, not fail --------------


def test_workflow_requires_scope_ask_when_active_project_has_no_workflow(monkeypatch):
    factory = _factory()
    _patch(monkeypatch, factory)
    ids = _seed(
        factory,
        projects=[("Stale", True)],
        boards=[
            {"name": "Stale Delivery", "project": 0},          # in-use project, no workflow
            {"name": "Ready Delivery", "project": None, "workflow": 0},
        ],
        workflows=["Development"],
    )
    service = OrchestratorIntakeService()

    decision = service.ingest(
        WorkIntake(source="web", user_text="Fix the checkout validation")
    )

    assert decision.action == WorkIntakeAction.ASK_MISSING_INFO
    assert decision.status == "needs_info"
    assert decision.handled is True
    assert decision.workflow_run_id is None
    assert "no workflow" in decision.response_text.lower()
    # Names the single workflow-ready alternative so the user can just say yes.
    assert "Ready Delivery" in decision.response_text
    assert _ticket_count(factory) == 0


def test_single_workflow_ready_board_is_still_selected(monkeypatch):
    factory = _factory()
    _patch(monkeypatch, factory)
    ids = _seed(
        factory,
        projects=[("Only", False)],
        boards=[{"name": "Only Delivery", "project": 0, "workflow": 0}],
        workflows=["Development"],
    )
    service = OrchestratorIntakeService()
    service._start_workflow = lambda intake, decision: None

    decision = service.ingest(
        WorkIntake(source="web", user_text="Fix the checkout validation")
    )

    assert decision.action == WorkIntakeAction.RUN_WORKFLOW
    assert decision.board_id == ids["boards"][0]
    assert decision.diagnostics.get("scope_needs_info") is not True
