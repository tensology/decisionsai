from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text

from distr.core.kanban import jira_work_lifecycle as lifecycle


def _isolated(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'jlife.db'}")
    monkeypatch.setattr(lifecycle, "engine", engine)
    lifecycle.ensure_tables()
    return engine


def test_ticket_lifecycle_durable_and_idempotent(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    first = lifecycle.record_ticket_created(ticket_id=41, board_id=7, project_id=3, issue_key="ACME-1")
    second = lifecycle.record_ticket_created(ticket_id=41, board_id=7, project_id=3, issue_key="ACME-1")
    assert first["status"] == "ticket_created"
    assert second["issue_key"] == "ACME-1"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM jira_work_lifecycles")).scalar_one() == 1


def test_completed_creates_unsent_client_draft(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=42, board_id=7, project_id=3, issue_key="ACME-2",
        outbound_channel="email", outbound_target="msg123", client_contact="Maya",
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.get.return_value = SimpleNamespace(
        title="ACME-2: Fix checkout",
        time_spent="1h 20m",
        external_id="ACME-2",
        source_provider="jira",
        source_thread_id="msg123",
        source_contact="Maya",
        source_external_id="msg123",
    )
    monkeypatch.setattr(lifecycle, "get_session", lambda: session)
    monkeypatch.setattr(lifecycle, "_audit", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(lifecycle, "_send_client_message", lambda *a, **k: sent.append(1) or {"success": True})
    review = lifecycle.prepare_completed_jira_review(
        ticket_id=42, run_id=8, status="completed", result_summary="Checkout passes browser validation."
    )
    assert review and review["token"]
    assert "Maya" in review["draft"] or "checkout" in review["draft"].lower()
    assert sent == []
    with lifecycle.engine.connect() as conn:
        row = conn.execute(text("SELECT status, review_status FROM jira_work_lifecycles WHERE ticket_id=42")).mappings().one()
    assert row == {"status": "awaiting_reply_review", "review_status": "pending"}


def test_revise_then_leave(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(ticket_id=43, board_id=7, project_id=3, issue_key="ACME-3")
    monkeypatch.setattr(lifecycle, "_audit", lambda *a, **k: None)
    with engine.begin() as conn:
        lifecycle_id = conn.execute(text("SELECT id FROM jira_work_lifecycles WHERE ticket_id=43")).scalar_one()
        conn.execute(text("UPDATE jira_work_lifecycles SET comment_draft='Old' WHERE id=:id"), {"id": lifecycle_id})
        conn.execute(text("INSERT INTO jira_reply_reviews(token,lifecycle_id,status,created_at,updated_at) VALUES ('tok',:id,'pending',1,1)"), {"id": lifecycle_id})
    prompt = lifecycle.handle_telegram_jira_reply("jr:tok:revise", chat_id=99)
    assert "revised client message" in prompt["text"]
    revised = lifecycle.handle_telegram_jira_reply("Hi Maya, checkout is ready now.", chat_id=99)
    assert revised["reply_markup"] == lifecycle.review_markup("tok")
    left = lifecycle.handle_telegram_jira_reply("jr:tok:leave", chat_id=99)
    assert "local draft" in left["text"]
    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM jira_work_lifecycles WHERE ticket_id=43")).scalar_one()
    assert status == "comment_draft_ready"


def test_controls_are_client_send_revise_leave():
    labels = [b["text"] for row in lifecycle.review_markup("abc")["inline_keyboard"] for b in row]
    assert labels == ["Send to client", "Revise", "Leave draft"]
    assert "Complete" not in labels


def test_send_claimed_once_failed_returns_pending(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(ticket_id=44, board_id=7, project_id=3, issue_key="ACME-4")
    monkeypatch.setattr(lifecycle, "_audit", lambda *a, **k: None)
    with engine.begin() as conn:
        lifecycle_id = conn.execute(text("SELECT id FROM jira_work_lifecycles WHERE ticket_id=44")).scalar_one()
        conn.execute(text("UPDATE jira_work_lifecycles SET comment_draft='Ready', outbound_channel='jira_comment' WHERE id=:id"), {"id": lifecycle_id})
        conn.execute(text("INSERT INTO jira_reply_reviews(token,lifecycle_id,status,created_at,updated_at) VALUES ('sendtok',:id,'pending',1,1)"), {"id": lifecycle_id})
    calls = []
    failed = lifecycle.handle_telegram_jira_reply(
        "jr:sendtok:send",
        chat_id=99,
        comment_fn=lambda *a, **k: calls.append(1) or {"success": False, "error": "offline"},
    )
    assert "Draft is still saved" in failed["text"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM jira_reply_reviews WHERE token='sendtok'")).scalar_one() == "pending"
    assert len(calls) == 1

    with engine.begin() as conn:
        conn.execute(text("UPDATE jira_reply_reviews SET status='resolving' WHERE token='sendtok'"))
    duplicate = lifecycle.handle_telegram_jira_reply(
        "jr:sendtok:send",
        chat_id=99,
        comment_fn=lambda *a, **k: calls.append(1) or {"success": True},
    )
    assert "already being applied" in duplicate["text"]
    assert len(calls) == 1
