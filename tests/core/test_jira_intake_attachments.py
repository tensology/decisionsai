import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, KanbanTicketFile
from distr.core.kanban import jira_intake as intake
from distr.core.kanban import jira_work_lifecycle as jlife


def test_attachment_soft_skip_and_success(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'att.db'}",
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

    with get_session() as session:
        board = KanbanBoard(name="Client", agent_source_lane="Backlog")
        session.add(board)
        session.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        session.add(lane)
        session.flush()
        ticket = KanbanTicket(lane_id=lane.id, title="ACME-1: x", description="d", external_id="ACME-1")
        session.add(ticket)
        session.flush()
        ticket_id = ticket.id

    assert intake.attach_intake_files(ticket_id=ticket_id, attachments_meta=[]) == []
    assert intake.attach_intake_files(
        ticket_id=ticket_id,
        attachments_meta=[{"filename": "a.pdf"}],
        download_fn=None,
    ) == []

    def boom(_meta):
        raise RuntimeError("network")

    assert intake.attach_intake_files(
        ticket_id=ticket_id,
        attachments_meta=[{"filename": "a.pdf"}],
        download_fn=boom,
    ) == []

    attached = intake.attach_intake_files(
        ticket_id=ticket_id,
        attachments_meta=[{"filename": "spec.pdf"}],
        download_fn=lambda meta: str(tmp_path / meta["filename"]),
    )
    assert attached == [str(tmp_path / "spec.pdf")]
    with get_session() as session:
        files = session.query(KanbanTicketFile).filter_by(ticket_id=ticket_id).all()
        assert len(files) == 1
        assert files[0].filename == "spec.pdf"
