"""End-to-end: intake → CLI dispatch → humanized draft → Telegram revise → send."""

from __future__ import annotations

import contextlib

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.kanban import jira_intake as intake
from distr.core.kanban import jira_work_lifecycle as jlife
from distr.core.kanban import work_ops
from distr.core.kanban.ticket_time_tracking import add_time_spent_seconds


def test_work_ops_intake_cli_draft_revise_send_e2e(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'work_ops_e2e.db'}",
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
    monkeypatch.setattr(work_ops, "_in_use_board_id", lambda: board_id)
    monkeypatch.setattr(intake, "engine", engine)
    monkeypatch.setattr(jlife, "engine", engine)
    jlife.ensure_tables()
    intake.ensure_intake_tables()
    monkeypatch.setattr(jlife, "_audit", lambda *a, **k: None)

    with get_session() as session:
        project = Project(id=9, name="Client site", folder_location=str(tmp_path))
        board = KanbanBoard(
            name="Client site",
            agent_source_lane="Backlog",
            default_project_id=9,
            send_to_cli=True,
            in_use=True,
            source="database",
        )
        session.add_all([project, board])
        session.flush()
        session.add(KanbanLane(board_id=board.id, name="Backlog", position=0))
        session.flush()
        board_id = board.id

    messages = [
        {"from": "jira@acme.atlassian.net", "subject": "(ACME-10) assigned you", "snippet": "ACME-10 Fix login"},
        {"from": "jira@acme.atlassian.net", "subject": "(ACME-11) created", "body": "ACME-11 Add PDF"},
        {"from": "friend@example.com", "subject": "coffee?", "snippet": " tomorrow"},
    ]

    def fake_fetch(acct, keys, **kwargs):
        return [
            {"key": key, "fields": {"summary": f"Summary {key}", "description": f"Body {key}"}}
            for key in keys
        ]

    digests = []
    monkeypatch.setattr(
        intake,
        "notify_jira_intake_digest",
        lambda **kwargs: digests.append(kwargs) or True,
    )
    monkeypatch.setattr(
        "distr.core.kanban.jira_intake.load_jira_account",
        lambda: {"email": "a@b.c", "api_token": "t", "server_url": "https://acme.atlassian.net"},
    )
    monkeypatch.setattr("distr.core.kanban.jira_intake.fetch_jira_issues", fake_fetch)

    # 1) Intake via work_ops (not a named "turn on" command)
    intake_result = work_ops.work_intake(board_id=board_id, messages=messages, notify=True)
    assert intake_result["success"] is True
    assert len(digests) == 1
    created_ids = [c["id"] for c in intake_result["result"]["created"]]
    assert len(created_ids) == 2

    with get_session() as session:
        tickets = session.query(KanbanTicket).order_by(KanbanTicket.id).all()
        assert [t.external_id for t in tickets] == ["ACME-10", "ACME-11"]
        assert all(t.send_to_cli for t in tickets)
        assert all(t.linked_project_id == 9 for t in tickets)
        first_id = tickets[0].id

    # 2) Run via CLI path (mocked dispatch)
    cli_calls = []
    monkeypatch.setattr(
        "distr.core.agent.tools.integrations.kanban_ticket.KanbanTicketTool._run",
        lambda self, **kwargs: cli_calls.append(kwargs) or "sent to CLI",
    )
    run_result = work_ops.work_run([first_id])
    assert run_result["success"] is True
    assert run_result["started"][0]["dispatched"] == "cli"
    assert cli_calls and cli_calls[0]["action"] == "send_to_cli"

    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status, execution_kind FROM jira_work_lifecycles WHERE ticket_id=:id"),
            {"id": first_id},
        ).mappings().one()
    assert status["status"] == "executing"
    assert status["execution_kind"] == "cli"

    # 3) Time + completion → humanized client draft (Telegram review, no auto-send)
    with get_session() as session:
        ticket = session.get(KanbanTicket, first_id)
        ticket.time_spent = add_time_spent_seconds(ticket.time_spent, 45 * 60)
        ticket.source_contact = "Maya"
        ticket.source_provider = "jira"

    reviews = []
    monkeypatch.setattr(
        "distr.core.kanban.jira_work_lifecycle.notify_telegram_jira_review",
        lambda review: reviews.append(review) or True,
    )
    complete = work_ops.work_complete_simulated(
        ticket_id=first_id,
        run_id=101,
        result_summary="Login now works on staging.",
        notify=True,
    )
    assert complete["success"] is True
    assert reviews and "Maya" in reviews[0]["draft"]
    assert "—" not in reviews[0]["draft"]

    status_view = work_ops.work_status(first_id)
    assert "awaiting_reply_review" in status_view["spoken_summary"]

    draft_view = work_ops.work_draft(first_id)
    assert draft_view["success"] is True
    token = reviews[0]["token"]

    # 4) Voice/text revise back-and-forth on Telegram
    revise = jlife.handle_telegram_jira_reply(f"jr:{token}:revise", chat_id=55)
    assert "revised client message" in revise["text"]
    revised = jlife.handle_telegram_jira_reply(
        "Hi Maya, login is fixed on staging. Have a look when you can.",
        chat_id=55,
    )
    assert "Updated client draft" in revised["text"]
    assert revised["reply_markup"] == jlife.review_markup(token)

    # 5) Send only after approve
    send_calls = []
    sent = jlife.handle_telegram_jira_reply(
        f"jr:{token}:send",
        chat_id=55,
        comment_fn=lambda *a, **k: send_calls.append(a) or {"success": True},
    )
    assert "Sent to the client" in sent["text"]
    assert len(send_calls) == 1
    with engine.connect() as conn:
        final = conn.execute(
            text("SELECT status, review_status FROM jira_work_lifecycles WHERE ticket_id=:id"),
            {"id": first_id},
        ).mappings().one()
    assert final == {"status": "reply_sent", "review_status": "sent"}


def test_work_ops_tool_routes_status_and_run_intents():
    from distr.core.agent.tool_intents import forced_tool_names_for_text
    from distr.core.agent.tools.system.work_ops_tool import WorkOpsTool

    assert "work_ops" in forced_tool_names_for_text("What's coming in from work intake?")
    assert "work_ops" in forced_tool_names_for_text("Run ticket 42")
    assert "work_ops" in forced_tool_names_for_text("Send it to the client")

    tool = WorkOpsTool()
    monkey_calls = {}

    def fake_status(ticket_id=None):
        monkey_calls["status"] = ticket_id
        return {"spoken_summary": "Work status: idle", "action": "status", "success": True}

    import distr.core.kanban.work_ops as ops

    ops.work_status = fake_status  # type: ignore
    out = tool._run(action="status")
    assert "Work status" in out
    assert monkey_calls["status"] is None
