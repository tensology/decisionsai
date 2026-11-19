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
from distr.core.db.kanban import KanbanBoard
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
        session.flush()
        board = KanbanBoard(name="Before", source="database", default_project_id=project.id)
        session.add(board)
        session.flush()
        project.kanban_board_id = board.id
        session.commit()
        project_id = project.id
        board_id = board.id

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
        assert session.get(KanbanBoard, board_id).name == "After"


def test_create_project_also_creates_its_development_board():
    factory = _make_factory()

    def get_session():
        return _session_ctx(factory)

    client = _make_client(factory)
    with (
        patch("distr.core.db.get_session", get_session),
        patch("distr.core.workspace_memory.provision.bootstrap_project"),
        patch("distr.core.tensology_project_sync.invalidate_project_sync") as wake_sync,
        patch("distr.core.tensology_project_sync.sync_projects") as network_sync,
    ):
        response = client.post(
            "/api/projects",
            json={"name": "Shared surface", "folder_location": "/tmp/shared"},
        )

    assert response.status_code == 200, response.text
    wake_sync.assert_called_once_with()
    network_sync.assert_not_called()
    with factory() as session:
        project = session.get(Project, response.json()["id"])
        board = session.get(KanbanBoard, project.kanban_board_id)
        assert board.name == "Shared surface"
        assert board.default_project_id == project.id
        assert [lane.name for lane in board.lanes] == ["Backlog", "In Progress", "QA", "Complete"]


def test_import_tensology_starter_creates_project_and_replays_without_overwriting(tmp_path, monkeypatch):
    from distr.core import tensology_project_import

    factory = _make_factory()
    client = _make_client(factory)
    customer_project = "08a00e08-a82e-4c75-944a-b72da77ea096"
    preview = f"https://www.tensology.com/dashboard/trial-store?preview=1&project={customer_project}"
    manifest = {
        "version": 1,
        "starter": "ecommerce",
        "project_id": customer_project,
        "name": "Northwind Store",
        "preview_url": preview,
    }
    local = "http://127.0.0.1:8765"
    token = "test-only-internal-token"
    headers = {"Origin": local, "X-DecisionsAI-Internal-Token": token}
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", token)
    monkeypatch.setattr(tensology_project_import, "_projects_dir", lambda: tmp_path / "projects")
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))

    def request(body=manifest, request_headers=headers):
        return client.post(local + "/api/projects/import-tensology", json=body, headers=request_headers)

    assert request(request_headers={"Origin": local}).status_code == 401
    assert request(request_headers={**headers, "Origin": "https://www.tensology.com"}).status_code == 403
    assert request({**manifest, "command": "touch unexpected"}).status_code == 422
    assert request({**manifest, "preview_url": preview + "&preview=1"}).status_code == 422
    assert request({**manifest, "preview_url": preview.replace(customer_project, "other")}).status_code == 422
    assert not (tmp_path / "projects").exists()

    imported = request()
    assert imported.status_code == 200, imported.text
    project_id = imported.json()["id"]
    assert imported.json()["created"] is True
    with factory() as session:
        project = session.get(Project, project_id)
        assert project.name == "Northwind Store"
        assert project.coding_backend == "codex"
        assert project.context_items[0].content == preview
        assert session.get(KanbanBoard, project.kanban_board_id).default_project_id == project_id
        folder = Path(project.folder_location)
    assert (folder / "backend" / "manage.py").is_file()
    assert (folder / "frontend" / "package.json").is_file()
    (folder / "README.md").write_text("my local change", encoding="utf-8")
    assert request().json() == {"id": project_id, "created": False}
    assert (folder / "README.md").read_text(encoding="utf-8") == "my local change"


def test_import_tensology_starter_keeps_an_unrelated_folder(tmp_path, monkeypatch):
    from distr.core import tensology_project_import

    factory = _make_factory()
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(tensology_project_import, "_projects_dir", lambda: tmp_path)
    customer_project = "08a00e08-a82e-4c75-944a-b72da77ea096"
    existing = tmp_path / f"tensology-{customer_project}"
    existing.mkdir()
    original = existing / "README.md"
    original.write_text("local work", encoding="utf-8")
    manifest = tensology_project_import.parse_manifest(
        (
            '{"version":1,"starter":"ecommerce","name":"Northwind","project_id":"'
            + customer_project
            + '","preview_url":"https://tensology.com/dashboard/trial-store?preview=1&project='
            + customer_project
            + '"}'
        ).encode()
    )
    try:
        tensology_project_import.import_starter(manifest)
    except tensology_project_import.StarterImportError as error:
        assert "already exists" in str(error)
    else:
        raise AssertionError("An unrelated folder was accepted")
    assert original.read_text(encoding="utf-8") == "local work"
    assert list(existing.iterdir()) == [original]


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
    assert payload["8"] == {"running": False, "startup_count": 0, "shell_count": 0, "pi_cli_count": 0, "sessions": []}
