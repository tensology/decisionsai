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
        alpha = Project(name="Alpha", folder_location="/tmp/alpha", position=0, startup_instructions="npm run alpha")
        bravo = Project(name="Bravo", folder_location="/tmp/bravo", position=1, startup_instructions="npm run bravo")
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
    assert {project["name"]: project["startup_instructions"] for project in list_response.json()} == {
        "Alpha": "npm run alpha",
        "Bravo": "npm run bravo",
        "Charlie": "",
    }

    with factory() as session:
        positions = {
            project.id: project.position
            for project in session.query(Project).order_by(Project.position.asc()).all()
        }

    assert positions == {order[0]: 0, order[1]: 1, order[2]: 2}


def test_update_project_persists_identity_fields_and_rejects_blank_name():
    factory = _make_factory()
    with factory() as session:
        project = Project(name="Before", folder_location="/tmp/before", position=0)
        session.add(project)
        session.commit()
        project_id = project.id

    def get_session():
        return _session_ctx(factory)

    client = _make_client(factory)
    with patch("distr.core.db.get_session", get_session):
        response = client.patch(
            f"/api/projects/{project_id}",
            json={"name": "After", "folder_location": "/tmp/after"},
        )
        invalid = client.patch(f"/api/projects/{project_id}", json={"name": "   "})

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "After"
    assert response.json()["folder_location"] == "/tmp/after"
    assert invalid.status_code == 422

    with factory() as session:
        updated = session.get(Project, project_id)
        assert updated.name == "After"
        assert updated.folder_location == "/tmp/after"


def test_terminal_status_returns_shared_startup_and_shell_state():
    factory = _make_factory()
    client = _make_client(factory)
    sessions = {
        (7, "startup"): [{"process_id": "startup-1", "pid": 101, "purpose": "startup"}],
        (7, "cli_shell"): [{"process_id": "shell-1", "pid": 102, "purpose": "cli_shell"}],
    }

    def get_sessions(project_id: int, purpose: str):
        return sessions.get((project_id, purpose), [])

    with (
        patch("distr.core.terminal.get_startup_sessions_for_project", get_sessions),
        patch.object(project_routes, "_process_memory_bytes", return_value=4096),
    ):
        response = client.get("/api/projects/terminal-status?project_ids=7,8")

    assert response.status_code == 200, response.text
    payload = response.json()["projects"]
    assert payload["7"]["running"] is True
    assert payload["7"]["startup_count"] == 1
    assert payload["7"]["shell_count"] == 1
    assert [session["process_id"] for session in payload["7"]["sessions"]] == ["startup-1", "shell-1"]
    assert payload["8"] == {"running": False, "startup_count": 0, "shell_count": 0, "sessions": []}
