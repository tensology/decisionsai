"""Tests for KanbanTicketTool merge_ticket — uses an in-memory board, never live tickets."""

from __future__ import annotations

import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.agent.services.llm.tool_execution_policy import is_mutating_tool_call
from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketInput, KanbanTicketTool
from distr.core.agent.tool_intents import forced_tool_names_for_text
from distr.core.db import Base
from distr.core.db.kanban import (
    KanbanBoard,
    KanbanLane,
    KanbanTicket,
    KanbanTicketFile,
    KanbanTicketLink,
    KanbanTicketTodo,
)
from distr.core.kanban.ticket_merge import TicketMergeError, merge_ticket


def _memory_session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
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

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    return get_session


def _board_with_tickets(
    session,
    *,
    project_id=10,
    keep_title="Keep me",
    dup_title="Duplicate",
    extra_lane=False,
):
    board = KanbanBoard(name="Merge Board", default_project_id=project_id)
    session.add(board)
    session.flush()
    backlog = KanbanLane(board_id=board.id, name="Backlog", position=0)
    session.add(backlog)
    session.flush()
    keep = KanbanTicket(
        lane_id=backlog.id,
        title=keep_title,
        description="Keep description.",
        context_notes="Keep notes.",
        priority="high",
        linked_project_id=project_id,
    )
    duplicate = KanbanTicket(
        lane_id=backlog.id,
        title=dup_title,
        description="Duplicate description.",
        context_notes="Duplicate notes.",
        priority="medium",
        source_label="celery-alert",
        linked_project_id=project_id,
    )
    session.add_all([keep, duplicate])
    session.flush()
    session.add(KanbanTicketLink(ticket_id=duplicate.id, title="Runbook", url="https://example.test/runbook"))
    session.add(
        KanbanTicketFile(
            ticket_id=duplicate.id,
            filename="trace.log",
            file_path="/tmp/merge-test-trace.log",
            description="log",
        )
    )
    session.add(KanbanTicketTodo(ticket_id=duplicate.id, text="Confirm the worker", done=False, position=0))
    child = KanbanTicket(
        lane_id=backlog.id,
        title="Child of duplicate",
        description="spawned",
        linked_project_id=project_id,
        parent_ticket_id=duplicate.id,
    )
    session.add(child)
    session.flush()
    return board, keep, duplicate, child


def test_schema_exposes_merge_ticket_fields():
    parsed = KanbanTicketInput.model_validate(
        {"action": "merge_ticket", "keep_id": 4, "duplicate_id": 9, "allow_cross_project": True}
    )
    assert parsed.action == "merge_ticket"
    assert parsed.keep_id == 4
    assert parsed.duplicate_id == 9
    assert parsed.allow_cross_project is True
    assert "merge_ticket" in KanbanTicketTool.model_fields["description"].default
    assert is_mutating_tool_call("create_ticket", {"action": "merge_ticket", "keep_id": 4, "duplicate_id": 9})
    assert "create_ticket" in forced_tool_names_for_text("merge those two tickets")


def test_merge_same_id_is_refused(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        _, keep, _, _ = _board_with_tickets(session)
        keep_id = keep.id
    try:
        with get_session() as session:
            merge_ticket(session, keep_id, keep_id)
        raise AssertionError("expected TicketMergeError")
    except TicketMergeError as exc:
        assert exc.code == "same_id"
    message = KanbanTicketTool()._run(action="merge_ticket", keep_id=keep_id, duplicate_id=keep_id)
    assert "must be different" in message


def test_merge_missing_id_is_refused(monkeypatch):
    get_session = _memory_session(monkeypatch)
    _memory_session  # session factory must be patched even for this call
    with get_session() as session:
        _, keep, _, _ = _board_with_tickets(session)
        keep_id = keep.id
    try:
        with get_session() as session:
            merge_ticket(session, keep_id, 0)
        raise AssertionError("expected TicketMergeError")
    except TicketMergeError as exc:
        assert exc.code == "missing_id"
    message = KanbanTicketTool()._run(action="merge_ticket", keep_id=keep_id)
    assert "keep ticket id" in message.lower() or "duplicate ticket id" in message.lower()


def test_merge_missing_ticket_is_refused(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        _, keep, _, _ = _board_with_tickets(session)
        keep_id = keep.id
    try:
        with get_session() as session:
            merge_ticket(session, keep_id, 99999)
        raise AssertionError("expected TicketMergeError")
    except TicketMergeError as exc:
        assert exc.code == "not_found"


def test_merge_cross_project_is_refused_unless_allowed(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        board_a = KanbanBoard(name="Project A", default_project_id=21)
        board_b = KanbanBoard(name="Project B", default_project_id=22)
        session.add_all([board_a, board_b])
        session.flush()
        lane_a = KanbanLane(board_id=board_a.id, name="Backlog", position=0)
        lane_b = KanbanLane(board_id=board_b.id, name="Backlog", position=0)
        session.add_all([lane_a, lane_b])
        session.flush()
        keep = KanbanTicket(lane_id=lane_a.id, title="A", linked_project_id=21)
        duplicate = KanbanTicket(lane_id=lane_b.id, title="B", linked_project_id=22)
        session.add_all([keep, duplicate])
        session.flush()
        keep_id, dup_id = keep.id, duplicate.id

    try:
        with get_session() as session:
            merge_ticket(session, keep_id, dup_id)
        raise AssertionError("expected TicketMergeError")
    except TicketMergeError as exc:
        assert exc.code == "cross_project"

    with get_session() as session:
        result = merge_ticket(session, keep_id, dup_id, allow_cross_project=True)
        assert result["already_merged"] is False
        archived = session.query(KanbanTicket).filter_by(id=dup_id).one()
        assert archived.merged_into_ticket_id == keep_id


def test_merge_copies_content_repoints_and_archives(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        _, keep, duplicate, child = _board_with_tickets(session)
        keep_id, dup_id, child_id = keep.id, duplicate.id, child.id

    message = KanbanTicketTool()._run(action="merge_ticket", keep_id=keep_id, duplicate_id=dup_id)
    assert f"Merged ticket #{dup_id} into #{keep_id}" in message
    assert "archived" in message.lower()
    assert "deleted ticket" not in message.lower()

    with get_session() as session:
        kept = session.query(KanbanTicket).filter_by(id=keep_id).one()
        archived = session.query(KanbanTicket).filter_by(id=dup_id).one()
        child = session.query(KanbanTicket).filter_by(id=child_id).one()
        assert archived.merged_into_ticket_id == keep_id
        assert "Merged from #%s" % dup_id in (kept.description or "")
        assert "Merged from #%s" % dup_id in (kept.context_notes or "")
        assert "Duplicate description." in (kept.description or "")
        assert "Duplicate notes." in (kept.context_notes or "")
        assert "Merged into #%s" % keep_id in (archived.description or "")
        assert "Merged into #%s" % keep_id in (archived.context_notes or "")
        assert kept.source_label == "celery-alert"
        urls = {link.url for link in session.query(KanbanTicketLink).filter_by(ticket_id=keep_id)}
        assert "https://example.test/runbook" in urls
        files = {file.filename for file in session.query(KanbanTicketFile).filter_by(ticket_id=keep_id)}
        assert "trace.log" in files
        todos = {todo.text for todo in session.query(KanbanTicketTodo).filter_by(ticket_id=keep_id)}
        assert "Confirm the worker" in todos
        assert child.parent_ticket_id == keep_id
        # Duplicate row still exists (not hard-deleted) and still has its original attachments.
        assert session.query(KanbanTicket).filter_by(id=dup_id).one()
        assert session.query(KanbanTicketFile).filter_by(ticket_id=dup_id, filename="trace.log").one()


def test_merge_is_idempotent(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        _, keep, duplicate, _ = _board_with_tickets(session)
        keep_id, dup_id = keep.id, duplicate.id

    first = KanbanTicketTool()._run(action="merge_ticket", keep_id=keep_id, duplicate_id=dup_id)
    second = KanbanTicketTool()._run(action="merge_ticket", keep_id=keep_id, duplicate_id=dup_id)
    assert "Merged ticket" in first
    assert "already merged" in second.lower()

    with get_session() as session:
        kept = session.query(KanbanTicket).filter_by(id=keep_id).one()
        assert (kept.description or "").count("Merged from #%s" % dup_id) == 1
        assert (kept.context_notes or "").count("Merged from #%s" % dup_id) == 1
        assert session.query(KanbanTicketLink).filter_by(ticket_id=keep_id).count() == 1
        assert session.query(KanbanTicketFile).filter_by(ticket_id=keep_id).count() == 1
        assert session.query(KanbanTicketTodo).filter_by(ticket_id=keep_id).count() == 1


def test_list_tickets_hides_merged_duplicate(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        board, keep, duplicate, _ = _board_with_tickets(session)
        board_id, keep_id, dup_id = board.id, keep.id, duplicate.id

    KanbanTicketTool()._run(action="merge_ticket", keep_id=keep_id, duplicate_id=dup_id)
    listed = KanbanTicketTool()._run(action="list_tickets", board_id=board_id)
    assert f"#{keep_id} " in listed
    assert f"#{dup_id} " not in listed
    opened = KanbanTicketTool()._run(action="get_ticket", ticket_id=dup_id)
    assert f"Merged into #{keep_id}" in opened


def test_merge_from_text_into_pattern(monkeypatch):
    get_session = _memory_session(monkeypatch)
    with get_session() as session:
        _, keep, duplicate, _ = _board_with_tickets(session)
        keep_id, dup_id = keep.id, duplicate.id

    message = KanbanTicketTool()._run(
        action="merge_ticket",
        text=f"merge ticket #{dup_id} into #{keep_id}",
    )
    assert f"Merged ticket #{dup_id} into #{keep_id}" in message
