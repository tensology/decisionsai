from datetime import datetime
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool
from distr.core.db import Base, WhatsAppMessage
from distr.core.db.kanban import KanbanBoard, KanbanTicket
from distr.core.db.projects import Project
from distr.core.kanban.lifecycle import ensure_delivery_lanes
from distr.core.kanban import whatsapp_work_lifecycle as lifecycle


def test_whatsapp_message_to_ticket_to_qa_reply_review_chain(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'chain.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    @contextmanager
    def session_scope():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    monkeypatch.setattr(lifecycle, "engine", engine)
    monkeypatch.setattr(lifecycle, "get_session", session_scope)
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_compose_drafts.save_compose_draft",
        lambda **kwargs: kwargs,
    )

    with Session() as session:
        project = Project(id=3, name="Client site", folder_location=str(tmp_path), kanban_board_id=7)
        board = KanbanBoard(
            id=7, name="Client site", source="database", default_project_id=3,
            default_workflow_id=2,
        )
        session.add_all([project, board])
        session.flush()
        ensure_delivery_lanes(session, board.id)
        message = WhatsAppMessage(
            message_id="wa-1", jid="120@g.us", jid_phone="120", chat_type="group",
            sender_push_name="Maya", sender_phone="555", text="Please make the button black",
            caption="", media_type="", processed=False, whatsapp_timestamp=1,
            created_date=datetime(2026, 7, 27, 12, 0, 0),
        )
        session.add(message)
        session.commit()
        message_id = message.id

    tool = KanbanTicketTool()
    monkeypatch.setattr(tool, "_get_session", session_scope)
    result = tool._action_whatsapp_snapshot_to_ticket(
        board_id=7, message_ids=[message_id], title="Make the button black",
    )
    assert "I turned those WhatsApp messages into a ticket" in result

    with Session() as session:
        ticket = session.query(KanbanTicket).one()
        assert ticket.linked_project_id == 3
        assert ticket.linked_workflow_id == 2
        assert ticket.source_thread_id == "120@g.us"
        ticket_id = ticket.id
    with engine.connect() as conn:
        durable = conn.execute(text(
            "SELECT status, message_ids FROM whatsapp_work_lifecycles WHERE ticket_id=:id"
        ), {"id": ticket_id}).mappings().one()
    assert durable["status"] == "ticket_created"
    assert str(message_id) in durable["message_ids"]

    review = lifecycle.prepare_completed_reply(
        ticket_id=ticket_id, run_id=77, status="completed",
        result_summary="The button was changed and browser validation passed.",
    )
    assert review and "browser validation passed" in review["draft"]
    decision = lifecycle.handle_telegram_reply(f"wa:{review['token']}:leave", chat_id=99)
    assert "left the reply" in decision["text"]
    with engine.connect() as conn:
        final_state = conn.execute(text(
            "SELECT status FROM whatsapp_work_lifecycles WHERE ticket_id=:id"
        ), {"id": ticket_id}).scalar_one()
    assert final_state == "reply_draft_ready"
