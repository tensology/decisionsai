"""Managed class weight overrides are retired; helpers still work."""

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


def test_override_is_retired(db_factory):
    from distr.core.workflow import phases

    ticket_id = _ticket(db_factory, "Add a button")
    result = phases.apply_route_override(ticket_id, "make it heavier", speak=False)
    assert result["changed"] is False
    assert result["reason"] == "managed_class_retired"
    with db_factory() as db:
        assert db.get(KanbanTicket, ticket_id).linked_workflow_id is None


def test_is_retired_workflow_flags_archived_and_missing(db_factory):
    from distr.core.db.workflow import AutoWorkflow
    from distr.core.workflow import phases

    with db_factory() as db:
        active = AutoWorkflow(name="Hand-rolled", status="active")
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
