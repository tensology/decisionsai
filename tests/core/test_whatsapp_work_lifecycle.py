from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text

from distr.core.kanban import whatsapp_work_lifecycle as lifecycle


def _isolated_engine(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    monkeypatch.setattr(lifecycle, "engine", engine)
    lifecycle.ensure_tables()
    return engine


def test_ticket_lifecycle_is_durable_and_idempotent(monkeypatch, tmp_path):
    engine = _isolated_engine(monkeypatch, tmp_path)
    first = lifecycle.record_ticket_created(
        ticket_id=41, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Client group",
        message_ids=[10, 11],
    )
    second = lifecycle.record_ticket_created(
        ticket_id=41, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Client group",
        message_ids=[10, 11, 12],
    )
    assert first["status"] == "ticket_created"
    assert second["message_ids"] == "[10, 11, 12]"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM whatsapp_work_lifecycles")).scalar_one() == 1


def test_completed_ticket_creates_unsent_review_not_completion(monkeypatch, tmp_path):
    _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=42, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Maya",
        message_ids=[20],
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.get.return_value = SimpleNamespace(title="Fix the checkout button")
    monkeypatch.setattr(lifecycle, "get_session", lambda: session)
    saved = {}
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_compose_drafts.save_compose_draft",
        lambda **kwargs: saved.update(kwargs) or kwargs,
    )
    review = lifecycle.prepare_completed_reply(
        ticket_id=42, run_id=8, status="completed", result_summary="Checkout now passes browser validation."
    )
    assert review and review["token"]
    assert "ready for your review" in review["draft"]
    assert saved["source"] == "agent"
    with lifecycle.engine.connect() as conn:
        row = conn.execute(text("SELECT status, reply_status FROM whatsapp_work_lifecycles WHERE ticket_id=42")).mappings().one()
    assert row == {"status": "awaiting_reply_review", "reply_status": "pending"}


def test_telegram_review_revise_then_leave_draft(monkeypatch, tmp_path):
    engine = _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=43, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Maya", message_ids=[30],
    )
    now = 1.0
    with engine.begin() as conn:
        lifecycle_id = conn.execute(text("SELECT id FROM whatsapp_work_lifecycles WHERE ticket_id=43")).scalar_one()
        conn.execute(text("UPDATE whatsapp_work_lifecycles SET reply_draft='Old draft' WHERE id=:id"), {"id": lifecycle_id})
        conn.execute(text("INSERT INTO whatsapp_reply_reviews(token,lifecycle_id,status,created_at,updated_at) VALUES ('tok',:id,'pending',:now,:now)"), {"id": lifecycle_id, "now": now})
    saved = {}
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_compose_drafts.save_compose_draft",
        lambda **kwargs: saved.update(kwargs) or kwargs,
    )
    prompt = lifecycle.handle_telegram_reply("wa:tok:revise", chat_id=99)
    assert "revised wording" in prompt["text"]
    revised = lifecycle.handle_telegram_reply("Hi Maya, this is ready now.", chat_id=99)
    assert revised["reply_markup"] == lifecycle.review_markup("tok")
    assert saved["text"] == "Hi Maya, this is ready now."
    left = lifecycle.handle_telegram_reply("wa:tok:leave", chat_id=99)
    assert "left the reply" in left["text"]
    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM whatsapp_work_lifecycles WHERE ticket_id=43")).scalar_one()
    assert status == "reply_draft_ready"


def test_reply_controls_never_offer_complete():
    labels = [button["text"] for button in lifecycle.review_markup("abc")["inline_keyboard"][0]]
    assert labels == ["Send", "Revise", "Leave draft"]
    assert "Complete" not in labels


def test_send_review_is_claimed_once_and_failed_send_returns_to_pending(monkeypatch, tmp_path):
    engine = _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=44, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Maya", message_ids=[31],
    )
    with engine.begin() as conn:
        lifecycle_id = conn.execute(text("SELECT id FROM whatsapp_work_lifecycles WHERE ticket_id=44")).scalar_one()
        conn.execute(text("UPDATE whatsapp_work_lifecycles SET reply_draft='Ready' WHERE id=:id"), {"id": lifecycle_id})
        conn.execute(text("INSERT INTO whatsapp_reply_reviews(token,lifecycle_id,status,created_at,updated_at) VALUES ('sendtok',:id,'pending',1,1)"), {"id": lifecycle_id})
    calls = []
    monkeypatch.setattr(
        "distr.core.integrations.whatsapp.relay_client.send_message_via_relay",
        lambda **kwargs: calls.append(kwargs) or {"success": False, "error": "offline"},
    )
    failed = lifecycle.handle_telegram_reply("wa:sendtok:send", chat_id=99)
    assert "draft is still saved" in failed["text"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM whatsapp_reply_reviews WHERE token='sendtok'")).scalar_one() == "pending"
    assert len(calls) == 1

    with engine.begin() as conn:
        conn.execute(text("UPDATE whatsapp_reply_reviews SET status='resolving' WHERE token='sendtok'"))
    duplicate = lifecycle.handle_telegram_reply("wa:sendtok:send", chat_id=99)
    assert "already being applied" in duplicate["text"]
    assert len(calls) == 1
