from sqlalchemy import create_engine, text

from distr.core.kanban import jira_intake as intake


def _iso(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'intake.db'}")
    monkeypatch.setattr(intake, "engine", engine)
    intake.ensure_intake_tables()
    return engine


def test_notify_empty_batch_sends_nothing(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app",
        lambda: type("M", (), {"send_to_telegram": lambda self, *a, **k: sent.append((a, k)) or True})(),
    )
    assert intake.notify_jira_intake_digest(tickets=[], batch={"token": "x"}) is False
    assert sent == []


def test_notify_sends_once(monkeypatch, tmp_path):
    _iso(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app",
        lambda: type("M", (), {"send_to_telegram": lambda self, *a, **k: sent.append((a, k)) or True})(),
    )
    ok = intake.notify_jira_intake_digest(
        tickets=[{"id": 1, "external_id": "ACME-1", "title": "ACME-1: One"}],
        batch={"token": "tok123"},
    )
    assert ok is True
    assert len(sent) == 1
    assert "ACME-1" in sent[0][0][0]
    assert sent[0][1]["reply_markup"] == intake.intake_markup("tok123")


def test_intake_ignore_and_prio(monkeypatch, tmp_path):
    engine = _iso(monkeypatch, tmp_path)
    batch = intake.record_intake_batch(board_id=7, ticket_ids=[1, 2], issue_keys=["ACME-1", "ACME-2"])
    ignored = intake.handle_jira_intake_telegram_reply(f"ji:{batch['token']}:ignore", chat_id=9)
    assert "Ignored" in ignored["text"]
    again = intake.handle_jira_intake_telegram_reply(f"ji:{batch['token']}:ignore", chat_id=9)
    assert "already handled" in again["text"]

    batch2 = intake.record_intake_batch(board_id=7, ticket_ids=[3], issue_keys=["ACME-3"])
    prio = intake.handle_jira_intake_telegram_reply(f"ji:{batch2['token']}:prio", chat_id=9)
    assert prio["action"] == "prioritize"
    assert prio["ticket_ids"] == [3]
    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM jira_intake_batches WHERE token=:t"), {"t": batch2["token"]}).scalar_one()
    assert status == "prioritized"
