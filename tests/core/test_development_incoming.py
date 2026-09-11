from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, WhatsAppMessage, WhatsAppPhoneLink
from distr.core.db.kanban import KanbanBoard
from distr.core.incoming import service as development_incoming


def test_development_incoming_returns_whatsapp_and_readable_gmail_threads(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr(development_incoming, "get_session", session_scope)
    monkeypatch.setattr(
        development_incoming,
        "_load_gmail_inbox",
        lambda max_results: ([{
            "id": "gmail-1",
            "threadId": "gmail-thread-1",
            "subject": "Menu launch review",
            "from": "Client <client@example.com>",
            "to": "Paul <paul@example.com>",
            "date": "Tue, 25 Aug 2026 11:00:00 +0000",
            "snippet": "Please review the launch copy",
            "body": "<p>Please review the <strong>launch copy</strong>.</p>",
            "labels": ["INBOX", "UNREAD"],
            "attachments": [],
        }], {"connected": True, "error": ""}),
    )
    monkeypatch.setattr(
        development_incoming,
        "_load_mailshot_inbox",
        lambda max_results: ([{
            "id": "mailshot-1",
            "thread_id": "mailshot-thread-1",
            "subject": "Tensology deployment",
            "from": "Ops <ops@tensology.com>",
            "to": "Paul <paul@tensology.com>",
            "date": "2026-08-25T08:30:00Z",
            "snippet": "The deployment report is ready",
            "body": "The deployment report is ready.",
            "read": False,
            "attachments": [{"filename": "report.pdf"}],
        }], {"connected": True, "error": ""}),
    )
    monkeypatch.setattr(
        "distr.core.work_intake.get_work_intake_service",
        lambda: type("Intake", (), {"list_inbox": lambda self, limit: [{"event_id": 9, "source": "gmail", "text": "Approve the revised menu copy", "created_at": "2026-08-25T09:00:00Z", "needs_attention": True}]})(),
    )

    with session_scope() as session:
        board = KanbanBoard(name="Menu Project")
        second_board = KanbanBoard(name="Website Refresh")
        session.add_all([board, second_board])
        session.flush()
        session.add(WhatsAppPhoneLink(board_id=board.id, phone_jid="menu@g.us", contact_name="Menu client", auto_snapshot=False))
        session.add(WhatsAppPhoneLink(board_id=second_board.id, phone_jid="menu@g.us", contact_name="Menu client", auto_snapshot=False))
        session.add(WhatsAppMessage(message_id="wa-1", jid="menu@g.us", sender_push_name="Client", text="Add vegetarian filters", whatsapp_timestamp=100, from_me=False, processed=False))
        session.add(WhatsAppMessage(message_id="wa-out", jid="menu@g.us", sender_push_name="Paul", text="I will handle it", whatsapp_timestamp=99, from_me=True, processed=True, media_type="audio", media_mime_type="audio/ogg"))
        session.add(WhatsAppMessage(message_id="wa-2", jid="new-group@g.us", chat_type="group", sender_push_name="Member", text="Review the launch copy", raw_data='{"group_name":"Launch group"}', whatsapp_timestamp=200, from_me=False, processed=False))

    result = development_incoming.list_development_incoming(limit=1)

    assert result["counts"] == {"whatsapp": 3, "gmail": 2, "mailshot": 1}
    assert all(item["source"] != "telegram" for item in result["items"])
    whatsapp = next(item for item in result["items"] if item["source_thread_id"] == "menu@g.us")
    assert whatsapp["board_name"] == "Menu Project"
    assert whatsapp["can_snapshot"] is True
    assert whatsapp["conversation_label"] == "Menu client"
    assert [link["board_name"] for link in whatsapp["links"]] == ["Menu Project", "Website Refresh"]
    outgoing = next(item for item in result["items"] if item.get("source_message_id") == "wa-out")
    assert outgoing["from_me"] is True
    assert outgoing["direction"] == "outbound"
    assert outgoing["media_mime_type"] == "audio/ogg"
    group = next(item for item in result["items"] if item["source_thread_id"] == "new-group@g.us")
    assert group["conversation_label"] == "Launch group"
    assert group["chat_type"] == "group"
    gmail = next(item for item in result["items"] if item["key"] == "gmail:gmail-1")
    assert gmail["source_thread_id"] == "gmail-thread-1"
    assert gmail["conversation_label"] == "Menu launch review"
    assert gmail["text"] == "Please review the launch copy."
    assert gmail["unread"] is True
    assert result["channels"]["gmail"] == {"connected": True, "error": ""}
    mailshot = next(item for item in result["items"] if item["key"] == "mailshot:mailshot-1")
    assert mailshot["conversation_label"] == "Tensology deployment"
    assert mailshot["attachments"] == [{"filename": "report.pdf"}]
    assert result["channels"]["mailshot"] == {"connected": True, "error": ""}
    assert result["links"][0]["label"] == "Menu client"
