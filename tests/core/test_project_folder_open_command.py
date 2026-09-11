import contextlib

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.projects import Project
from distr.gui.web.routes.settings import projects
from distr.gui.web.routes.settings.projects import _project_folder_open_command


def test_project_folder_command_uses_finder_on_macos():
    assert _project_folder_open_command("/tmp/project", "darwin") == (
        ["open", "/tmp/project"],
        "Finder",
    )


def test_project_folder_command_uses_explorer_on_windows():
    assert _project_folder_open_command(r"C:\\work\\project", "win32") == (
        ["explorer", r"C:\\work\\project"],
        "Explorer",
    )


def test_project_folder_command_uses_generic_file_manager_elsewhere():
    assert _project_folder_open_command("/srv/project", "linux") == (
        ["xdg-open", "/srv/project"],
        "File Manager",
    )


def test_open_project_folder_route_uses_the_linked_project_directory(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    folder = tmp_path / "linked-project"
    folder.mkdir()
    with get_session() as session:
        project = Project(name="Linked project", folder_location=str(folder))
        session.add(project)
        session.flush()
        project_id = int(project.id)

    opened = []
    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr(
        projects.subprocess,
        "Popen",
        lambda command, **kwargs: opened.append((command, kwargs)),
    )
    app = FastAPI()
    router = APIRouter()
    projects.register_routes(router, None)
    app.include_router(router, prefix="/api")

    response = TestClient(app).post(f"/api/projects/{project_id}/open-folder")

    assert response.status_code == 200
    assert response.json()["file_manager"] in {"Finder", "Explorer", "File Manager"}
    assert opened[0][0][-1] == str(folder)
