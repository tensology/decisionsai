from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, WhatsAppPhoneLink
from distr.core.db.kanban import KanbanBoard, KanbanLane
from distr.core.db.projects import Project
from distr.core.kanban import whatsapp_binding_integrity as integrity


def test_repair_recovers_referenced_board_without_deleting_whatsapp_link(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'bindings.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(integrity, "get_session", lambda: Session())
    with Session() as session:
        session.add(Project(id=2, name="Merrypak", kanban_board_id=2))
        session.add(WhatsAppPhoneLink(
            id=1, board_id=2, phone_jid="120@g.us", phone_number="120",
            contact_name="MP Web", auto_snapshot=True,
        ))
        session.commit()

    report = integrity.audit_and_repair_whatsapp_bindings()

    assert report["recovered_boards"] == [2]
    with Session() as session:
        board = session.get(KanbanBoard, 2)
        assert board.name == "Merrypak"
        assert board.default_project_id == 2
        assert session.query(KanbanLane).filter_by(board_id=2).count() == 4
        assert session.query(WhatsAppPhoneLink).filter_by(board_id=2).count() == 1


def test_repair_mirrors_project_link_to_primary_board_without_auto_snapshot(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'bindings.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(integrity, "get_session", lambda: Session())
    with Session() as session:
        session.add(Project(id=4, name="Player1Sport", kanban_board_id=11))
        session.add_all([
            KanbanBoard(id=8, name="Player1Sport Jira", default_project_id=4, source="jira"),
            KanbanBoard(id=11, name="Player1Sport", default_project_id=4, source="database"),
        ])
        session.add(WhatsAppPhoneLink(
            board_id=8, phone_jid="999@g.us", phone_number="999",
            contact_name="Player1 Web", auto_snapshot=True,
        ))
        session.commit()

    report = integrity.audit_and_repair_whatsapp_bindings()

    assert report["mirrored_links"][0]["board_id"] == 11
    with Session() as session:
        mirror = session.query(WhatsAppPhoneLink).filter_by(board_id=11).one()
        assert mirror.phone_jid == "999@g.us"
        assert mirror.auto_snapshot is False

