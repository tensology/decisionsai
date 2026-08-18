from sqlalchemy import create_engine, text

from distr.core.kanban import jira_intake as intake


def test_run_callback_starts_execution_without_jira_write(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'run.db'}")
    monkeypatch.setattr(intake, "engine", engine)
    intake.ensure_intake_tables()
    batch = intake.record_intake_batch(board_id=1, ticket_ids=[10, 11], issue_keys=["A-1", "A-2"])
    calls = []
    monkeypatch.setattr(
        intake,
        "start_execution_for_tickets",
        lambda ids: calls.append(list(ids)) or {"started": [{"ticket_id": i} for i in ids], "error": None},
    )
    jira_calls = []
    monkeypatch.setattr(
        "distr.core.kanban.jira_work_lifecycle.post_jira_comment",
        lambda *a, **k: jira_calls.append(1) or {"success": True},
    )
    result = intake.handle_jira_intake_telegram_reply(f"ji:{batch['token']}:run", chat_id=5)
    assert result["action"] == "run"
    assert calls == [[10, 11]]
    assert jira_calls == []
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM jira_intake_batches WHERE token=:t"), {"t": batch["token"]}).scalar_one() == "executed"
