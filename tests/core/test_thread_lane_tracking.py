"""Development thread work moves its linked ticket Backlog -> In Progress -> QA/Done."""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base, Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, KanbanTicketLaneHistory
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import DevelopmentCommand, DevelopmentWorkItem
from distr.core.kanban import thread_lane_tracking as tracking


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    published: list = []
    monkeypatch.setattr(tracking, "_publish", lambda *args: published.append(args))

    @contextmanager
    def provider():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    session = factory()
    try:
        yield session, provider, published
    finally:
        session.close()
        engine.dispose()


def _board(session, lanes=("Backlog", "In Progress", "QA", "Complete"), source="database"):
    board = KanbanBoard(name="Pizza House", source=source, default_project_id=7)
    session.add(board)
    session.flush()
    by_name = {}
    for position, name in enumerate(lanes):
        lane = KanbanLane(board_id=board.id, name=name, position=position)
        session.add(lane)
        session.flush()
        by_name[name] = lane
    return board, by_name


def _thread(session, lane, *, project_id=7, source_type="prompt", board_provider="local", link="work_item", **ticket_kw):
    chat = Chat(project_id=project_id, title="Fix the button")
    session.add(chat)
    session.flush()
    ticket = KanbanTicket(lane_id=lane.id, title="Fix the button", **ticket_kw)
    if link == "source_chat":
        ticket.source_chat_id = chat.id
    session.add(ticket)
    session.flush()
    if link == "work_item":
        session.add(DevelopmentWorkItem(
            chat_id=chat.id, identity_key=f"ticket:{ticket.id}", source_type=source_type,
            board_provider=board_provider, local_ticket_id=ticket.id, project_id=project_id,
        ))
    session.commit()
    return chat, ticket


def _lane_of(session, ticket):
    session.expire_all()
    return session.get(KanbanLane, session.get(KanbanTicket, ticket.id).lane_id).name


def _hand_move(session, ticket, at):
    session.add(KanbanTicketLaneHistory(
        ticket_id=ticket.id, from_lane="In Progress", to_lane="Backlog", actor="user",
        reason="board_move", source="http_move", created_at=at,
    ))
    session.commit()


def test_start_then_finish_moves_forward_and_records_history(db):
    session, provider, published = db
    _board_row, lanes = _board(session)
    chat, ticket = _thread(session, lanes["Backlog"])

    assert tracking.track_work_started(chat.id, _session_provider=provider) == "In Progress"
    assert _lane_of(session, ticket) == "In Progress"
    # Idempotent: a second turn start does nothing.
    assert tracking.track_work_started(chat.id, _session_provider=provider) is None

    assert tracking.track_work_finished(chat.id, _session_provider=provider) == "QA"
    assert _lane_of(session, ticket) == "QA"
    assert tracking.track_work_finished(chat.id, _session_provider=provider) is None

    rows = session.query(KanbanTicketLaneHistory).order_by(KanbanTicketLaneHistory.id).all()
    assert [(r.from_lane, r.to_lane, r.actor, r.reason, r.source) for r in rows] == [
        ("Backlog", "In Progress", "automation", "thread_work_started", "thread_lane_tracking"),
        ("In Progress", "QA", "automation", "thread_work_finished", "thread_lane_tracking"),
    ]
    assert [p[2] for p in published] == ["In Progress", "QA"]


def test_child_chat_and_source_chat_link_resolve_the_ticket(db):
    session, provider, _ = db
    _b, lanes = _board(session, lanes=("To Do", "In Progress", "QA", "Done"))
    chat, ticket = _thread(session, lanes["To Do"], link="source_chat")
    child = Chat(parent_id=chat.id, project_id=7, title="reply")
    session.add(child)
    session.commit()
    assert tracking.track_work_started(child.id, _session_provider=provider) == "In Progress"
    assert _lane_of(session, ticket) == "In Progress"


def test_finished_goes_to_done_when_board_has_no_qa_lane(db):
    session, provider, _ = db
    _b, lanes = _board(session, lanes=("Backlog", "In Progress", "Done"))
    chat, ticket = _thread(session, lanes["In Progress"])
    assert tracking.track_work_finished(chat.id, _session_provider=provider) == "Done"
    assert _lane_of(session, ticket) == "Done"


@pytest.mark.parametrize("lane_name", ["QA", "Complete"])
def test_never_moves_backwards(db, lane_name):
    session, provider, _ = db
    _b, lanes = _board(session)
    chat, ticket = _thread(session, lanes[lane_name])
    assert tracking.track_work_started(chat.id, _session_provider=provider) is None
    assert tracking.track_work_finished(chat.id, _session_provider=provider) is None
    assert _lane_of(session, ticket) == lane_name


def test_finish_does_not_jump_from_backlog(db):
    session, provider, _ = db
    _b, lanes = _board(session)
    chat, ticket = _thread(session, lanes["Backlog"])
    assert tracking.track_work_finished(chat.id, _session_provider=provider) is None
    assert _lane_of(session, ticket) == "Backlog"


def test_recent_hand_move_is_never_overridden(db):
    session, provider, _ = db
    _b, lanes = _board(session)
    chat, ticket = _thread(session, lanes["Backlog"])
    _hand_move(session, ticket, utc_now_naive() - timedelta(minutes=5))
    assert tracking.track_work_started(chat.id, _session_provider=provider) is None
    assert _lane_of(session, ticket) == "Backlog"


def test_old_hand_move_does_not_block_a_new_start(db):
    session, provider, _ = db
    _b, lanes = _board(session)
    chat, ticket = _thread(session, lanes["Backlog"])
    _hand_move(session, ticket, utc_now_naive() - timedelta(hours=2))
    assert tracking.track_work_started(chat.id, _session_provider=provider) == "In Progress"


def test_hand_move_during_a_long_turn_blocks_finish(db):
    session, provider, _ = db
    _b, lanes = _board(session)
    chat, ticket = _thread(session, lanes["In Progress"])
    now = utc_now_naive()
    _hand_move(session, ticket, now - timedelta(hours=1))
    assert tracking.track_work_finished(
        chat.id, started_at=now - timedelta(hours=2), now=now, _session_provider=provider,
    ) is None
    assert _lane_of(session, ticket) == "In Progress"


def test_queued_instruction_defers_finish(db):
    session, provider, _ = db
    _b, lanes = _board(session)
    chat, ticket = _thread(session, lanes["In Progress"])
    session.add(DevelopmentCommand(chat_id=chat.id, content="also fix the footer", status="queued"))
    session.commit()
    assert tracking.track_work_finished(chat.id, _session_provider=provider) is None
    assert _lane_of(session, ticket) == "In Progress"


@pytest.mark.parametrize(
    "board_source, thread_kw",
    [
        ("database", {"external_source": "tensology"}),
        ("database", {"source_provider": "jira"}),
        ("jira", {}),
        ("database", {"board_provider": "jira"}),
        ("database", {"source_type": "automation"}),
        ("database", {"project_id": None}),
    ],
)
def test_client_synced_automation_and_projectless_threads_are_skipped(db, board_source, thread_kw):
    session, provider, _ = db
    _b, lanes = _board(session, source=board_source)
    chat, ticket = _thread(session, lanes["Backlog"], **thread_kw)
    assert tracking.track_work_started(chat.id, _session_provider=provider) is None
    assert _lane_of(session, ticket) == "Backlog"


def test_missing_target_lane_is_not_created(db):
    session, provider, _ = db
    board, lanes = _board(session, lanes=("Backlog", "Complete"))
    chat, ticket = _thread(session, lanes["Backlog"])
    assert tracking.track_work_started(chat.id, _session_provider=provider) is None
    assert session.query(KanbanLane).filter_by(board_id=board.id).count() == 2


def test_errors_never_reach_the_turn():
    def broken():
        raise RuntimeError("database locked")

    assert tracking.track_work_started(1, _session_provider=broken) is None
    assert tracking.track_work_finished(1, _session_provider=broken) is None


def test_cli_pane_submit_starts_work_for_its_own_thread_only(db):
    session, provider, _ = db
    _b, lanes = _board(session)
    chat, ticket = _thread(session, lanes["Backlog"])
    tabs = {"tabs": [{"tab_id": f"thread-{chat.id}", "process_id": "pty-1"}]}
    loader = lambda _project_id: tabs  # noqa: E731

    assert tracking.track_cli_pane_submit(99, "pty-1", _load_tabs=loader, _session_provider=provider) is None
    assert tracking.track_cli_pane_submit(7, "pty-2", _load_tabs=loader, _session_provider=provider) is None
    assert _lane_of(session, ticket) == "Backlog"
    assert tracking.track_cli_pane_submit(7, "pty-1", _load_tabs=loader, _session_provider=provider) == "In Progress"


def test_shared_cli_pane_process_is_ambiguous():
    tabs = {"tabs": [
        {"tab_id": "thread-1", "process_id": "pty-1"},
        {"tab_id": "thread-2", "process_id": "pty-1"},
    ]}
    assert tracking.thread_for_cli_pane(7, "pty-1", _load_tabs=lambda _p: tabs) is None


def test_harness_tracks_start_before_the_turn_and_finish_only_for_delivered_work():
    from distr.core.workflow import development_harness

    source = inspect.getsource(development_harness._run_execution)
    start = source.index("track_work_started(chat_id)")
    assert start < source.index("result = await execute_turn(")
    assert "if not audit_only:" in source[start - 200:start]
    assert (
        'if status == "completed" and success and not waiting and not audit_only and git_change_count:'
        in source
    )
    assert "track_work_finished(chat_id, started_at=lane_tracking_started_at)" in source


def test_cli_pane_websocket_hooks_only_pi_cli_enter():
    from distr.gui.web.routes.development import terminals

    source = inspect.getsource(terminals)
    assert 'if "\\r" in inp and getattr(sess, "purpose", "") == "pi_cli":' in source
