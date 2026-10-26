"""Activating a board must also activate its project.

Work intake resolves scope from the active project. If board activation left an
old project active, the next implicit work request was routed to the wrong
project. This is the stale-pin divergence seen in the live database
(project = Spoortrack (Old), board = Jackcat).
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base


def _make_factory():
    engine = create_engine(
        "sqlite:///:memory:",
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


def _seed(factory):
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project

    with _session_ctx(factory) as session:
        old_project = Project(name="Old", in_use=True)
        new_project = Project(name="New", in_use=False)
        session.add_all([old_project, new_project])
        session.flush()
        old_board = KanbanBoard(
            name="Old Delivery", default_project_id=old_project.id, in_use=True, position=0
        )
        new_board = KanbanBoard(
            name="New Delivery", default_project_id=new_project.id, in_use=False, position=1
        )
        session.add_all([old_board, new_board])
        session.flush()
        old_project.kanban_board_id = old_board.id
        new_project.kanban_board_id = new_board.id
        session.commit()
        return old_board.id, new_board.id, old_project.id, new_project.id


def test_board_activation_also_activates_linked_project():
    from distr.gui.web.routes.kanban import create_routes

    factory = _make_factory()
    old_board, new_board, old_project, new_project = _seed(factory)

    app = FastAPI()
    with patch(
        "distr.gui.web.routes.kanban.get_session",
        lambda: _session_ctx(factory),
    ):
        app.include_router(create_routes(), prefix="/api")
        response = TestClient(app).post(f"/api/tickets/boards/{new_board}/use")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["project_activated"] is True
    assert body["linked_project"]["id"] == new_project

    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project

    with _session_ctx(factory) as session:
        assert session.get(Project, new_project).in_use is True
        assert session.get(Project, old_project).in_use is False
        assert session.get(KanbanBoard, new_board).in_use is True
        assert session.get(KanbanBoard, old_board).in_use is False


def test_board_without_project_only_switches_board():
    from distr.gui.web.routes.kanban import create_routes
    from distr.core.db.kanban import KanbanBoard

    factory = _make_factory()
    old_board, new_board, old_project, _new_project = _seed(factory)
    with _session_ctx(factory) as session:
        standalone = KanbanBoard(name="Standalone", in_use=False, position=2)
        session.add(standalone)
        session.commit()
        standalone_id = standalone.id

    app = FastAPI()
    with patch(
        "distr.gui.web.routes.kanban.get_session",
        lambda: _session_ctx(factory),
    ):
        app.include_router(create_routes(), prefix="/api")
        response = TestClient(app).post(f"/api/tickets/boards/{standalone_id}/use")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_activated"] is False
    assert body["linked_project"] is None

    from distr.core.db.projects import Project

    with _session_ctx(factory) as session:
        # The previously active project is left as-is when the board has none.
        assert session.get(Project, old_project).in_use is True
        assert session.get(KanbanBoard, standalone_id).in_use is True
        assert session.get(KanbanBoard, old_board).in_use is False
