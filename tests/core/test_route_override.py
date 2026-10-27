"""Overriding a ticket's weight class relinks it, and risk still wins."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import distr.core.db.kanban  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket


@pytest.fixture()
def db_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _get_session():
        return factory()

    monkeypatch.setattr("distr.core.db.get_session", _get_session)
    return factory


def _ticket(factory, title: str, description: str = "") -> int:
    with factory() as db:
        board = KanbanBoard(name="Route board")
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Ready", position=0)
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(lane_id=lane.id, title=title, description=description)
        db.add(ticket)
        db.commit()
        return int(ticket.id)


def _workflow_ids():
    return {"light": 11, "standard": 10, "heavy": 12, "critical": 13}


def test_override_relinks_the_ticket(db_factory, monkeypatch):
    from distr.core.workflow import phases

    ticket_id = _ticket(db_factory, "Add a button")
    monkeypatch.setattr(phases, "find_class_workflow_id", lambda key: _workflow_ids()[key])

    result = phases.apply_route_override(ticket_id, "make it heavier", speak=False)

    assert result["changed"] is True
    assert result["class"] == "heavy"
    assert result["workflow_id"] == 12
    with db_factory() as db:
        assert db.get(KanbanTicket, ticket_id).linked_workflow_id == 12


def test_override_cannot_talk_a_migration_down(db_factory, monkeypatch):
    from distr.core.workflow import phases

    ticket_id = _ticket(db_factory, "Add a schema migration for the invoices table")
    monkeypatch.setattr(phases, "find_class_workflow_id", lambda key: _workflow_ids()[key])

    result = phases.apply_route_override(ticket_id, "make it lighter", speak=False)

    # Risk floors the override: a migration cannot become the cheap policy.
    assert result["class"] == "heavy"
    assert result["workflow_id"] == 12


def test_ordinary_text_is_not_an_override(db_factory, monkeypatch):
    from distr.core.workflow import phases

    ticket_id = _ticket(db_factory, "Add a button")
    monkeypatch.setattr(phases, "find_class_workflow_id", lambda key: 10)

    assert phases.apply_route_override(ticket_id, "fix the button", speak=False)["changed"] is False


def test_explicit_class_word_override(db_factory, monkeypatch):
    from distr.core.workflow import phases

    ticket_id = _ticket(db_factory, "Tidy the readme")
    monkeypatch.setattr(phases, "find_class_workflow_id", lambda key: _workflow_ids()[key])

    result = phases.apply_route_override(ticket_id, "make it critical", speak=False)

    assert result["class"] == "critical"
    assert result["workflow_id"] == 13


def test_is_retired_workflow_flags_archived_and_missing(db_factory):
    from distr.core.db.workflow import AutoWorkflow
    from distr.core.workflow import phases

    with db_factory() as db:
        active = AutoWorkflow(name="Ticket: standard (one session)", status="active")
        archived = AutoWorkflow(name="Development", status="archived")
        db.add_all([active, archived])
        db.commit()
        active_id, archived_id = int(active.id), int(archived.id)

    assert phases.is_retired_workflow(active_id) is False
    assert phases.is_retired_workflow(archived_id) is True
    assert phases.is_retired_workflow(999999) is True
    assert phases.is_retired_workflow(0) is False


def test_relink_ticket_points_it_at_the_routed_workflow(db_factory):
    from distr.core.db.kanban import KanbanTicket
    from distr.core.workflow import phases

    ticket_id = _ticket(db_factory, "Add a button")

    assert phases.relink_ticket_to_workflow(ticket_id, 12) is True
    with db_factory() as db:
        assert db.get(KanbanTicket, ticket_id).linked_workflow_id == 12

    assert phases.relink_ticket_to_workflow(999999, 12) is False
