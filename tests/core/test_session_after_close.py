"""Rows must stay readable after a get_session() block closes.

SessionContext commits on exit. With expire_on_commit=True that expires every
loaded attribute, so a read after the block raised DetachedInstanceError, which
callers swallow, and the feature failed silently.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import distr.core.db.kanban  # noqa: F401
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
    monkeypatch.setattr("distr.core.db.get_session", lambda: factory())
    return factory


def test_the_app_sessionmaker_keeps_attributes_after_commit():
    from distr.core import db as db_module

    assert db_module.Session.kw["expire_on_commit"] is False


def test_reading_a_row_after_the_block_does_not_raise(db_factory):
    with db_factory() as db:
        board = KanbanBoard(name="After close")
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Ready", position=0)
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(lane_id=lane.id, title="Read me later")
        db.add(ticket)
        db.commit()
        ticket_id = int(ticket.id)

    # A fresh session, committed and closed, then read afterwards.
    with db_factory() as db:
        row = db.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()

    assert row.title == "Read me later"
    assert row.lane_id is not None
