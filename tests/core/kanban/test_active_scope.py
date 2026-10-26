"""A project and its board are one active scope.

Regression: ``Project.in_use`` and ``KanbanBoard.in_use`` could point at
different scopes, so chat context (board) and work intake (project) disagreed.
Every activation now moves both flags, and reconcile heals legacy drift.
"""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base


def _factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextlib.contextmanager
def _ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _seed(factory, *, in_use_project=None, in_use_board=None, project_time=None, board_time=None):
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project

    with _ctx(factory) as s:
        a = Project(name="Alpha", in_use=(in_use_project == "A"))
        b = Project(name="Beta", in_use=(in_use_project == "B"))
        s.add_all([a, b])
        s.flush()
        ba = KanbanBoard(name="Alpha Delivery", default_project_id=a.id, in_use=(in_use_board == "A"))
        bb = KanbanBoard(name="Beta Delivery", default_project_id=b.id, in_use=(in_use_board == "B"))
        s.add_all([ba, bb])
        s.flush()
        a.kanban_board_id = ba.id
        b.kanban_board_id = bb.id
        if project_time:
            target = "A" if in_use_project == "A" else "B"
            (a if target == "A" else b).modified_date = project_time
        if board_time:
            target = "A" if in_use_board == "A" else "B"
            (ba if target == "A" else bb).modified_date = board_time
        s.commit()
        return {
            "A": a.id, "B": b.id, "boardA": ba.id, "boardB": bb.id,
        }


def _pins(factory):
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project

    with _ctx(factory) as s:
        proj = [p.id for p in s.query(Project).filter(Project.in_use == True).all()]
        board = [b.id for b in s.query(KanbanBoard).filter(KanbanBoard.in_use == True).all()]
    return proj, board


def test_set_active_project_activates_its_board():
    from distr.core.kanban.scope import set_active_project

    factory = _factory()
    ids = _seed(factory, in_use_project="A", in_use_board="A")
    with _ctx(factory) as s:
        project, board = set_active_project(s, ids["B"])
        assert project.id == ids["B"]
        assert board.id == ids["boardB"]
    assert _pins(factory) == ([ids["B"]], [ids["boardB"]])


def test_set_active_board_activates_its_project():
    from distr.core.kanban.scope import set_active_board

    factory = _factory()
    ids = _seed(factory, in_use_project="A", in_use_board="A")
    with _ctx(factory) as s:
        project, board = set_active_board(s, ids["boardB"])
        assert project.id == ids["B"]
        assert board.id == ids["boardB"]
    assert _pins(factory) == ([ids["B"]], [ids["boardB"]])


def test_reconcile_prefers_board_side_when_divergent():
    from distr.core.kanban.scope import reconcile_active_scope

    factory = _factory()
    # project pin = A, board pin = B -> the board side wins and the project aligns.
    ids = _seed(factory, in_use_project="A", in_use_board="B")
    with _ctx(factory) as s:
        project, board = reconcile_active_scope(s)
    assert project.id == ids["B"]
    assert board.id == ids["boardB"]
    assert _pins(factory) == ([ids["B"]], [ids["boardB"]])


def test_reconcile_project_only_scope_activates_its_board():
    from distr.core.kanban.scope import reconcile_active_scope

    factory = _factory()
    ids = _seed(factory, in_use_project="A", in_use_board=None)
    with _ctx(factory) as s:
        project, board = reconcile_active_scope(s)
    assert project.id == ids["A"]
    assert board.id == ids["boardA"]
    assert _pins(factory) == ([ids["A"]], [ids["boardA"]])


def test_reconcile_is_noop_when_already_consistent():
    from distr.core.kanban.scope import reconcile_active_scope

    factory = _factory()
    ids = _seed(factory, in_use_project="A", in_use_board="A")
    with _ctx(factory) as s:
        project, board = reconcile_active_scope(s)
    assert (project.id, board.id) == (ids["A"], ids["boardA"])
    assert _pins(factory) == ([ids["A"]], [ids["boardA"]])


def test_set_active_board_for_standalone_clears_previous_project():
    from distr.core.db.kanban import KanbanBoard
    from distr.core.kanban.scope import set_active_board

    factory = _factory()
    ids = _seed(factory, in_use_project="A", in_use_board="A")
    with _ctx(factory) as s:
        standalone = KanbanBoard(name="Standalone", in_use=False)
        s.add(standalone)
        s.flush()
        standalone_id = standalone.id
    with _ctx(factory) as s:
        project, board = set_active_board(s, standalone_id)
    assert project is None
    assert board.id == standalone_id
    # Standalone board = board-only scope: no project remains active.
    assert _pins(factory) == ([], [standalone_id])
