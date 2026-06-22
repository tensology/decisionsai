from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.projects import Project
from distr.core.project_cli_backends import live_sessions
from distr.gui.web.routes.settings.projects import register_routes


@pytest.fixture(autouse=True)
def clear_live_sessions():
    live_sessions._SESSIONS.clear()
    yield
    live_sessions._SESSIONS.clear()


@pytest.fixture
def db_setup():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def session_ctx():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return engine, factory, session_ctx


@pytest.fixture
def client(db_setup, monkeypatch, tmp_path):
    _engine, factory, session_ctx = db_setup
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")

    folder = tmp_path / "project"
    folder.mkdir()
    with session_ctx() as session:
        project = Project(
            name="Workflow CLI",
            folder_location=str(folder),
            coding_backend="codex",
        )
        session.add(project)
        session.flush()
        project_id = int(project.id)

    monkeypatch.setattr("distr.core.workflow.service.get_session", session_ctx)
    monkeypatch.setattr("distr.core.orchestrator.get_session", session_ctx)
    monkeypatch.setattr("distr.core.db.get_session", session_ctx)
    monkeypatch.setattr(
        "distr.core.project_cli_backends.get_backend",
        lambda backend_id: SimpleNamespace(
            id=str(backend_id),
            name="Codex CLI",
            supports_rpc=False,
            disconnect_session=_disconnect_session_stub,
            get_buffer=lambda project_id, lines=100: "",
        ),
    )
    return TestClient(app), factory, project_id


async def _disconnect_session_stub(project_id: int, folder: str):
    return SimpleNamespace(success=True, backend_id="codex", engine="codex", output="disconnected")


def test_live_session_presence_marks_board_and_workflow_scope():
    live_sessions.mark_live_session_presence(
        11,
        "codex",
        workflow_id=7,
        board_id=19,
        present=True,
        now=100.0,
    )
    session = live_sessions.get_live_session(11, "codex", board_id=19, create=False)
    assert session.workflow_id == 7
    assert session.board_id == 19
    assert session.last_presence_ping_at == 100.0
    assert session.workflow_area_present is True


def test_idle_session_expires_after_three_minutes_without_presence():
    live_sessions.set_live_session_connected(11, "codex", True, board_id=19)
    live_sessions.mark_live_session_presence(
        11,
        "codex",
        workflow_id=7,
        board_id=19,
        present=False,
        now=100.0,
    )
    assert live_sessions.live_session_should_expire(11, "codex", board_id=19, now=281.0) is True


def test_running_session_does_not_expire_while_processing():
    live_sessions.set_live_session_connected(11, "codex", True, board_id=19)
    live_sessions.set_live_session_running(11, "codex", True, board_id=19)
    live_sessions.mark_live_session_presence(
        11,
        "codex",
        workflow_id=7,
        board_id=19,
        present=False,
        now=100.0,
    )
    assert live_sessions.live_session_should_expire(11, "codex", board_id=19, now=600.0) is False


def test_terminal_buffer_reports_workflow_session_meta(client):
    tc, _factory, project_id = client
    live_sessions.set_live_session_connected(project_id, "codex", True, board_id=9, external_session_id="thread-1")
    live_sessions.mark_live_session_presence(
        project_id,
        "codex",
        workflow_id=5,
        board_id=9,
        present=True,
        now=100.0,
    )

    response = tc.get(f"/api/projects/{project_id}/terminal/buffer?lines=10&board_id=9")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == 5
    assert payload["board_id"] == 9
    assert payload["last_presence_ping_at"] == 100.0
    assert "expires_in_seconds" in payload


def test_keepalive_marks_presence_for_board_session(client):
    tc, _factory, project_id = client

    response = tc.post(
        f"/api/projects/{project_id}/terminal/keepalive",
        json={"backend_id": "codex", "workflow_id": 7, "board_id": 19, "present": True},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    session = live_sessions.get_live_session(project_id, "codex", board_id=19, create=False)
    assert session.workflow_id == 7
    assert session.board_id == 19
    assert session.workflow_area_present is True


def test_terminal_disconnects_expired_idle_session(client, monkeypatch):
    tc, _factory, project_id = client
    live_sessions.set_live_session_connected(project_id, "codex", True, board_id=9, external_session_id="thread-1")
    live_sessions.publish_live_session_event(
        project_id,
        "codex",
        {"type": "message_end", "message": {"role": "user", "content": "Keep this board context"}},
        board_id=9,
    )
    live_sessions.mark_live_session_presence(
        project_id,
        "codex",
        workflow_id=5,
        board_id=9,
        present=False,
        now=0.0,
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.live_sessions.live_session_should_expire",
        lambda *args, **kwargs: True,
    )

    response = tc.get(f"/api/projects/{project_id}/terminal/buffer?lines=10&board_id=9")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["alive"] is False
    assert "Keep this board context" in payload["buffer"]


def test_live_sessions_are_isolated_per_board(client):
    tc, _factory, project_id = client
    live_sessions.set_live_session_connected(project_id, "codex", True, board_id=9, external_session_id="thread-9")
    live_sessions.publish_live_session_event(
        project_id,
        "codex",
        {"type": "message_end", "message": {"role": "user", "content": "Board 9 only"}},
        board_id=9,
    )
    live_sessions.set_live_session_connected(project_id, "codex", True, board_id=12, external_session_id="thread-12")
    live_sessions.publish_live_session_event(
        project_id,
        "codex",
        {"type": "message_end", "message": {"role": "user", "content": "Board 12 only"}},
        board_id=12,
    )

    response_9 = tc.get(f"/api/projects/{project_id}/terminal/buffer?lines=10&board_id=9")
    response_12 = tc.get(f"/api/projects/{project_id}/terminal/buffer?lines=10&board_id=12")

    assert response_9.status_code == 200
    assert response_12.status_code == 200
    assert "Board 9 only" in response_9.json()["buffer"]
    assert "Board 12 only" not in response_9.json()["buffer"]
    assert "Board 12 only" in response_12.json()["buffer"]
    assert "Board 9 only" not in response_12.json()["buffer"]
