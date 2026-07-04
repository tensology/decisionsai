from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import patch

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.projects import Project
from distr.gui.web.routes.settings import projects as project_routes


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


def _make_client(factory):
    router = APIRouter()
    templates = Jinja2Templates(directory=str(Path(__file__).parent))
    project_routes.register_routes(router, templates)

    app = FastAPI()
    app.include_router(router, prefix="/api")

    return TestClient(app)


def test_reorder_projects_persists_left_sidebar_order():
    factory = _make_factory()
    with factory() as session:
        alpha = Project(name="Alpha", folder_location="/tmp/alpha", position=0)
        bravo = Project(name="Bravo", folder_location="/tmp/bravo", position=1)
        charlie = Project(name="Charlie", folder_location="/tmp/charlie", position=2)
        session.add_all([alpha, bravo, charlie])
        session.commit()
        order = [bravo.id, charlie.id, alpha.id]

    def get_session():
        return _session_ctx(factory)

    client = _make_client(factory)
    with patch("distr.core.db.get_session", get_session):
        reorder_response = client.post("/api/projects/reorder", json={"order": order})
        list_response = client.get("/api/projects")

    assert reorder_response.status_code == 200, reorder_response.text
    assert reorder_response.json()["success"] is True
    assert list_response.status_code == 200, list_response.text
    assert [project["id"] for project in list_response.json()] == order

    with factory() as session:
        positions = {
            project.id: project.position
            for project in session.query(Project).order_by(Project.position.asc()).all()
        }

    assert positions == {order[0]: 0, order[1]: 1, order[2]: 2}
