import contextlib
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import AutoWorkflow
from distr.core.kanban import jira_intake as intake
from distr.core.kanban import jira_work_lifecycle as jlife
from distr.core.kanban.ticket_time_tracking import add_time_spent_seconds


@pytest.mark.parametrize("dispatch_result", [None, {"success": True}, {"success": False, "error": "Run blocked"}])
def test_start_execution_marks_lifecycle_and_skips_unlinked(monkeypatch, tmp_path, dispatch_result):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'exec.db'}",
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
    dispatch = Mock(return_value=dispatch_result)
    monkeypatch.setattr("distr.core.workflow.dispatcher.start_workflow_ticket_group", dispatch)

    with get_session() as session:
        board = KanbanBoard(name="Client", agent_source_lane="Backlog")
        session.add(board)
        session.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        session.add(lane)
        session.flush()
        t1 = KanbanTicket(lane_id=lane.id, title="ACME-1: a", description="d", external_id="ACME-1", source_provider="jira")
        t2 = KanbanTicket(lane_id=lane.id, title="ACME-2: b", description="d", external_id="ACME-2", source_provider="jira")
        session.add_all([t1, t2])
        if dispatch_result is not None:
            workflow = AutoWorkflow(name="Jira workflow")
            session.add(workflow)
            session.flush()
            workflow_id = workflow.id
            t1.linked_workflow_id = workflow_id
        session.flush()
        ids = [t1.id, t2.id]

    result = intake.start_execution_for_tickets(ids)
    assert len(result["started"]) == 2
    assert result["started"][1]["dispatched"] == "none"
    expected_status = "executing"
    if dispatch_result is None:
        dispatch.assert_not_called()
        assert result["started"][0]["dispatched"] == "none"
    else:
        dispatch.assert_called_once_with(workflow_id, [{"ticket_id": ids[0]}], dispatch_async=True)
        if dispatch_result["success"]:
            assert result["started"][0]["dispatched"] == "workflow"
        else:
            assert result["started"][0]["error"] == "Run blocked"
            expected_status = "execution_failed"
    with engine.connect() as conn:
        statuses = conn.execute(text("SELECT status FROM jira_work_lifecycles ORDER BY ticket_id")).scalars().all()
    assert statuses == [expected_status, "executing"]


def test_local_time_amends_without_jira(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'time.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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

    with get_session() as session:
        board = KanbanBoard(name="Client")
        session.add(board)
        session.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog", position=0)
        session.add(lane)
        session.flush()
        ticket = KanbanTicket(lane_id=lane.id, title="ACME-9", description="d", time_spent="30m")
        session.add(ticket)
        session.flush()
        ticket_id = ticket.id

    with get_session() as session:
        ticket = session.get(KanbanTicket, ticket_id)
        ticket.time_spent = add_time_spent_seconds(ticket.time_spent, 3600)
        assert ticket.time_spent == "1h 30m"
