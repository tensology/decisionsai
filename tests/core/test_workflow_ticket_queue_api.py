"""Regression: workflow ticket queue API must not 500 when tickets have projects."""

from __future__ import annotations

import contextlib
import json
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


def _seed_workflow_ticket(factory):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow

    session = factory()
    try:
        workflow = AutoWorkflow(name="Development", status="active")
        project = Project(
            name="Spotify",
            folder_location="/tmp/spotify-remake",
            coding_backend="cursor",
            in_use=True,
        )
        session.add_all([workflow, project])
        session.flush()

        board = KanbanBoard(
            name="[e2e] Product board",
            default_project_id=project.id,
            in_use=True,
        )
        session.add(board)
        session.flush()

        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Tighten the now-playing bar",
            description="Make the scrubber easier to grab.",
            linked_project_id=project.id,
            linked_workflow_id=workflow.id,
            workflow_queue_position=0,
            position=0,
        )
        session.add(ticket)
        session.commit()
        return workflow.id, ticket.id
    finally:
        session.close()


def test_get_workflow_tickets_includes_cli_route_without_500():
    from distr.gui.web.routes.kanban import create_routes

    factory = _make_factory()
    workflow_id, ticket_id = _seed_workflow_ticket(factory)

    def get_session():
        return _session_ctx(factory)

    app = FastAPI()
    with patch("distr.gui.web.routes.kanban.get_session", get_session):
        app.include_router(create_routes(), prefix="/api")
        client = TestClient(app)
        response = client.get(f"/api/tickets/workflows/{workflow_id}/tickets")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == ticket_id
    assert isinstance(rows[0].get("cli_route"), dict)
