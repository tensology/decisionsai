from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import distr.core.db.kanban  # noqa: F401 - register tables with shared metadata
from distr.core.db import Base
from distr.core.db.kanban import (
    KanbanBoard,
    KanbanLane,
    KanbanTicket,
    ProjectExecutionEvent,
    ProjectExecutionSession,
)
from distr.core.db.workflow import AutoWorkflow
from distr.core.db.workflow import AutoWorkflowRun
from distr.core.workflow.dispatcher import StepDispatcher, _cleanup_orphaned_runs_on_startup


class _Query:
    def __init__(self, model):
        self.model = model

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        if self.model is AutoWorkflowRun:
            return SimpleNamespace(status="running", current_step_id=None)
        return None


class _Session:
    def query(self, *args, **kwargs):
        return _Query(args[0] if args else None)

    def commit(self):
        return None


@contextmanager
def _session():
    yield _Session()


def _factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def _real_session(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def test_startup_recovery_atomically_terminalizes_linked_execution_sessions(monkeypatch):
    factory = _factory()
    with _real_session(factory) as session:
        workflow = AutoWorkflow(name="Crash recovery")
        board = KanbanBoard(name="Crash board")
        session.add_all([workflow, board])
        session.flush()
        lane = KanbanLane(board_id=board.id, name="Doing")
        session.add(lane)
        session.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Interrupted ticket",
            workflow_status="running",
        )
        stale_terminal_ticket = KanbanTicket(
            lane_id=lane.id,
            title="Already terminal ticket",
            workflow_status="running",
        )
        session.add_all([ticket, stale_terminal_ticket])
        session.flush()
        orphan = AutoWorkflowRun(
            workflow_id=workflow.id,
            ticket_id=ticket.id,
            status="running",
        )
        completed = AutoWorkflowRun(
            workflow_id=workflow.id,
            ticket_id=stale_terminal_ticket.id,
            status="completed",
            completed_at=datetime(2026, 1, 1),
        )
        waiting = AutoWorkflowRun(workflow_id=workflow.id, status="waiting")
        session.add_all([orphan, completed, waiting])
        session.flush()
        active = ProjectExecutionSession(
            project_id=7,
            workflow_id=workflow.id,
            run_id=orphan.id,
            status="running",
        )
        historical = ProjectExecutionSession(
            project_id=7,
            workflow_id=workflow.id,
            run_id=completed.id,
            status="completed",
            completed_at=datetime(2026, 1, 1),
        )
        stale_terminal = ProjectExecutionSession(
            project_id=7,
            workflow_id=workflow.id,
            run_id=completed.id,
            status="running",
        )
        missing_run = ProjectExecutionSession(
            project_id=7,
            workflow_id=workflow.id,
            run_id=999999,
            status="waiting",
        )
        durable_waiting = ProjectExecutionSession(
            project_id=7,
            workflow_id=workflow.id,
            run_id=waiting.id,
            status="waiting",
        )
        session.add_all(
            [active, historical, stale_terminal, missing_run, durable_waiting]
        )
        session.flush()
        ids = {
            "orphan": orphan.id,
            "waiting": waiting.id,
            "active": active.id,
            "historical": historical.id,
            "stale_terminal": stale_terminal.id,
            "missing_run": missing_run.id,
            "durable_waiting": durable_waiting.id,
            "ticket": ticket.id,
            "stale_terminal_ticket": stale_terminal_ticket.id,
        }

    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.get_session",
        lambda: _real_session(factory),
    )
    _cleanup_orphaned_runs_on_startup()

    with _real_session(factory) as session:
        recovered_run = session.get(AutoWorkflowRun, ids["orphan"])
        waiting_run = session.get(AutoWorkflowRun, ids["waiting"])
        recovered_execution = session.get(ProjectExecutionSession, ids["active"])
        untouched_execution = session.get(ProjectExecutionSession, ids["historical"])
        stale_terminal = session.get(ProjectExecutionSession, ids["stale_terminal"])
        missing_run = session.get(ProjectExecutionSession, ids["missing_run"])
        durable_waiting = session.get(ProjectExecutionSession, ids["durable_waiting"])
        recovered_ticket = session.get(KanbanTicket, ids["ticket"])
        stale_terminal_ticket = session.get(KanbanTicket, ids["stale_terminal_ticket"])
        events = (
            session.query(ProjectExecutionEvent)
            .filter(ProjectExecutionEvent.session_id == ids["active"])
            .all()
        )

        assert recovered_run.status == "cancelled"
        assert recovered_run.completed_at is not None
        assert waiting_run.status == "waiting"
        assert recovered_execution.status == "cancelled"
        assert recovered_execution.completed_at is not None
        assert recovered_execution.error == "App restarted before provider completion."
        assert [(event.event_type, event.status) for event in events] == [
            ("recovered_after_restart", "cancelled")
        ]
        assert untouched_execution.status == "completed"
        assert stale_terminal.status == "cancelled"
        assert stale_terminal.completed_at is not None
        assert missing_run.status == "cancelled"
        assert missing_run.completed_at is not None
        assert durable_waiting.status == "waiting"
        assert durable_waiting.completed_at is None
        assert recovered_ticket.workflow_status == "cancelled"
        assert stale_terminal_ticket.workflow_status == "completed"


def test_unexpected_backend_exception_becomes_failed_step_result(monkeypatch):
    monkeypatch.setattr("distr.core.workflow.dispatcher.get_session", _session)
    monkeypatch.setattr("distr.core.workflow.dispatcher.build_step_preflight", lambda *_args, **_kwargs: {"ok": True, "summary": "ready"})
    monkeypatch.setattr("distr.core.workflow.dispatcher.record_workflow_chat_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("distr.core.workflow.dispatcher.emit_step_activity", lambda *_args, **_kwargs: None)

    dispatcher = StepDispatcher()
    dispatcher._load_step = Mock(return_value={"id": 7, "name": "Worker", "workflow_id": 3, "position": 0})
    dispatcher._set_run_phase = Mock()
    dispatcher._validate_before_dispatch = Mock(return_value=[])
    dispatcher._set_status = Mock()
    dispatcher._execute = Mock(side_effect=RuntimeError("adapter exploded"))
    dispatcher._record_result_and_route = Mock()

    result = dispatcher.run_in_workflow(7, 11)

    assert result["status"] == "failed"
    assert "adapter exploded" in result["error"]
    dispatcher._record_result_and_route.assert_called_once_with(
        7,
        run_id=11,
        result_text="Step execution failed unexpectedly: adapter exploded",
        passed=False,
        skip_wait=True,
    )


def test_cancelled_run_cannot_dispatch_a_late_provider_step(monkeypatch):
    class CancelledQuery(_Query):
        def first(self):
            if self.model is AutoWorkflowRun:
                return SimpleNamespace(status="cancelled", current_step_id=7)
            return None

    class CancelledSession(_Session):
        def query(self, *args, **kwargs):
            return CancelledQuery(args[0] if args else None)

    @contextmanager
    def cancelled_session():
        yield CancelledSession()

    monkeypatch.setattr("distr.core.workflow.dispatcher.get_session", cancelled_session)
    dispatcher = StepDispatcher()
    dispatcher._execute = Mock()

    result = dispatcher.run_in_workflow(7, 11)

    assert result["cancelled"] is True
    assert result["status"] == "cancelled"
    dispatcher._execute.assert_not_called()
