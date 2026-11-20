import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane
from distr.core.db.projects import Project
from distr.core.workflow.studio_board import (
    StudioBoardRequired,
    ensure_board_for_project,
    resolve_studio_work_board,
)


def _memory(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    return get_session


def test_resolve_without_board_or_project_is_a_clear_error(monkeypatch):
    _memory(monkeypatch)
    try:
        resolve_studio_work_board(project_id=None, board_key="")
    except StudioBoardRequired as exc:
        assert exc.code == "board_required"
        assert "Choose a board" in str(exc)
    else:
        raise AssertionError("expected StudioBoardRequired")


def test_resolve_uses_and_creates_the_project_board(monkeypatch):
    get_session = _memory(monkeypatch)
    with get_session() as session:
        project = Project(name="E2E UX test 3 (safe to delete)")
        session.add(project)
        session.flush()
        project_id = int(project.id)
        board = ensure_board_for_project(session, project_id)
        assert board is not None
        assert board.name == "E2E UX test 3 (safe to delete)"
        lanes = session.query(KanbanLane).filter_by(board_id=board.id).all()
        assert {lane.name for lane in lanes} >= {"Backlog"}
        board_id = int(board.id)

    resolved = resolve_studio_work_board(project_id=project_id, board_key="")
    assert resolved["board_key"] == f"decisions:{board_id}"
    assert resolved["board_id"] == board_id
    assert resolved["project_id"] == project_id


def test_resolve_keeps_an_explicit_board_key(monkeypatch):
    get_session = _memory(monkeypatch)
    with get_session() as session:
        project = Project(name="Keep Key")
        session.add(project)
        session.flush()
        board = KanbanBoard(name="Keep Key", source="database", default_project_id=project.id)
        session.add(board)
        session.flush()
        key = f"decisions:{int(board.id)}"
        project_id = int(project.id)

    resolved = resolve_studio_work_board(project_id=project_id, board_key=key)
    assert resolved["board_key"] == key
    assert resolved["created"] is False
