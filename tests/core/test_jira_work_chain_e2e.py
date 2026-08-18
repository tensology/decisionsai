import contextlib

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.kanban import jira_intake as intake
from distr.core.kanban import jira_work_lifecycle as jlife


def test_jira_email_to_stage_to_review_chain(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'chain.db'}",
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
    monkeypatch.setattr(intake, "engine", engine)
    monkeypatch.setattr(jlife, "engine", engine)
    jlife.ensure_tables()
    intake.ensure_intake_tables()

    with get_session() as session:
        board = KanbanBoard(
            name="Client site",
            agent_source_lane="Backlog",
            default_project_id=3,
            default_workflow_id=2,
        )
        session.add(board)
        session.flush()
        session.add(KanbanLane(board_id=board.id, name="Backlog", position=0))
        session.flush()
        board_id = board.id

    messages = [
        {"from": "jira@acme.atlassian.net", "subject": "(ACME-1) assigned you", "snippet": "ACME-1 Fix checkout"},
        {"from": "jira@acme.atlassian.net", "subject": "(ACME-2) commented", "body": "Please see ACME-2"},
        {"from": "jira@acme.atlassian.net", "subject": "[JIRA] (ACME-3) created", "snippet": "ACME-3"},
        {"from": "maya@client.com", "subject": "Lunch?", "snippet": "tomorrow"},
    ]

    def fake_fetch(acct, keys, **kwargs):
        return [
            {"key": key, "fields": {"summary": f"Summary {key}", "description": f"Body for {key}"}}
            for key in keys
        ]

    notified = []
    monkeypatch.setattr(
        intake,
        "notify_jira_intake_digest",
        lambda **kwargs: notified.append(kwargs) or True,
    )

    result = intake.run_jira_morning_intake(
        board_id=board_id,
        messages=messages,
        acct={"email": "a@b.c", "api_token": "t", "server_url": "https://acme.atlassian.net"},
        fetch_fn=fake_fetch,
        notify=True,
    )
    assert result["reason"] == "ok"
    assert len(result["created"]) == 3
    assert len(notified) == 1
    assert len(notified[0]["tickets"]) == 3

    with get_session() as session:
        tickets = session.query(KanbanTicket).order_by(KanbanTicket.id).all()
        assert [t.external_id for t in tickets] == ["ACME-1", "ACME-2", "ACME-3"]
        ticket = tickets[0]
        ticket.time_spent = "45m"
        ticket_id = ticket.id
        issue_key = ticket.external_id

    jlife.mark_execution_started(ticket_id=ticket_id, execution_kind="workflow", run_id=77)
    monkeypatch.setattr(jlife, "_audit", lambda *a, **k: None)
    review = jlife.prepare_completed_jira_review(
        ticket_id=ticket_id,
        run_id=77,
        status="completed",
        result_summary="Checkout passes.",
    )
    assert review and review["issue_key"] == issue_key

    comment_calls = []
    decision = jlife.handle_telegram_jira_reply(
        f"jr:{review['token']}:send",
        chat_id=99,
        comment_fn=lambda *a, **k: comment_calls.append(a) or {"success": True},
    )
    assert "Sent to the client" in decision["text"]
    assert len(comment_calls) == 1
    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM jira_work_lifecycles WHERE ticket_id=:id"), {"id": ticket_id}).scalar_one()
    assert status == "reply_sent"
