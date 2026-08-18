import contextlib

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.kanban import jira_intake as intake
from distr.core.kanban import jira_work_lifecycle as jlife


def _session_factory(monkeypatch, tmp_path=None):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stage.db'}" if tmp_path else "sqlite:///:memory:",
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

    monkeypatch.setattr(intake, "get_session", get_session)
    monkeypatch.setattr(jlife, "get_session", get_session)
    monkeypatch.setattr(jlife, "engine", engine)
    monkeypatch.setattr(intake, "engine", engine)
    jlife.ensure_tables()
    return get_session, engine


def test_stage_batch_creates_and_skips_existing(monkeypatch, tmp_path):
    get_session, engine = _session_factory(monkeypatch, tmp_path)
    with get_session() as session:
        board = KanbanBoard(name="Client", agent_source_lane="Backlog", default_project_id=3, default_workflow_id=9)
        session.add(board)
        session.flush()
        session.add(KanbanLane(board_id=board.id, name="Backlog", position=0))
        session.flush()
        board_id = board.id

    drafts = [
        {"title": "ACME-1: One", "description": "Do one", "external_id": "ACME-1", "key": "ACME-1"},
        {"title": "ACME-2: Two", "description": "Do two", "external_id": "ACME-2", "key": "ACME-2"},
        {"title": "ACME-3: Three", "description": "Do three", "external_id": "ACME-3", "key": "ACME-3"},
    ]
    first = intake.stage_jira_intake_batch(board_id=board_id, drafts=drafts)
    assert len(first["created"]) == 3
    second = intake.stage_jira_intake_batch(board_id=board_id, drafts=drafts)
    assert len(second["created"]) == 0
    assert len(second["skipped"]) == 3
    with get_session() as session:
        rows = session.query(KanbanTicket).order_by(KanbanTicket.id).all()
        assert [r.external_id for r in rows] == ["ACME-1", "ACME-2", "ACME-3"]
        assert all(r.source_provider == "jira" for r in rows)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM jira_work_lifecycles")).scalar_one() == 3
