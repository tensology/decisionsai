"""Regression coverage for terminal workflow audit and ticket writeback."""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base


def _make_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _seed_terminal_run(factory):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
    from distr.core.kanban.result_packet import create_initial_result_packet_for_run

    session = factory()
    try:
        board = KanbanBoard(name="Decisions", in_use=True)
        session.add(board)
        session.flush()

        lane = KanbanLane(board_id=board.id, name="Current", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Update docs note",
            description="Original ticket body.",
            priority="medium",
            position=0,
        )
        session.add(ticket)
        session.flush()

        workflow = AutoWorkflow(name="Docs Workflow", description="Update a docs note.")
        session.add(workflow)
        session.flush()

        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Update note",
            position=0,
            action_type="agent_instruction",
            instruction="Update the docs note.",
            status="passed",
        )
        session.add(step)
        session.flush()

        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            board_id=board.id,
            ticket_id=ticket.id,
            current_step_id=step.id,
            status="running",
            run_data=json.dumps(
                {
                    "risk_profile": {"level": "low", "signals": [], "risk_type": "standard"},
                    "result_packet": create_initial_result_packet_for_run(
                        ticket_id=ticket.id,
                        board_id=board.id,
                        board_name=board.name,
                        project_id=None,
                        project_name=None,
                        execution_lane="cursor",
                    ),
                }
            ),
        )
        session.add(run)
        session.flush()

        session.add(
            AutoWorkflowStepResult(
                step_id=step.id,
                run_id=run.id,
                agent_response="Updated the docs note and checked the output.",
                status="passed",
            )
        )
        ids = {
            "board_id": board.id,
            "ticket_id": ticket.id,
            "workflow_id": workflow.id,
            "step_id": step.id,
            "run_id": run.id,
        }
        session.commit()
        return ids
    finally:
        session.close()


def test_complete_run_persists_terminal_packet_ticket_note_and_audit_entry():
    from distr.core.db.kanban import KanbanTicket, KanbanTicketAuditEntry
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.dispatcher import complete_run

    factory = _make_factory()
    ids = _seed_terminal_run(factory)

    def get_session():
        return _session_ctx(factory)

    with patch("distr.core.workflow.dispatcher.get_session", get_session), patch(
        "distr.core.workflow.dispatcher.increment_workflow_updated", MagicMock()
    ), patch("distr.core.workflow.dispatcher.record_workflow_chat_event", MagicMock()), patch(
        "distr.gui.web.kanban_events.increment_kanban_updated", MagicMock()
    ), patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge", MagicMock()):
        assert complete_run(ids["run_id"], "completed") is True

    with get_session() as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).first()
        ticket = session.query(KanbanTicket).filter(KanbanTicket.id == ids["ticket_id"]).first()
        audit_entries = (
            session.query(KanbanTicketAuditEntry)
            .filter(KanbanTicketAuditEntry.ticket_id == ids["ticket_id"])
            .order_by(KanbanTicketAuditEntry.id.asc())
            .all()
        )

        run_data = json.loads(run.run_data or "{}")
        packet = run_data["result_packet"]

        assert run.status == "completed"
        assert packet["status"] == "completed"
        assert packet["summary"] == f"Workflow run {ids['run_id']} finished with status: completed."
        assert packet["audit"]["final_verdict"] == "pass"
        assert f"workflow_run:{ids['run_id']}" in packet["artifacts"]["logs"]

        assert ticket.workflow_status == "completed"
        assert f"[Workflow Run #{ids['run_id']}] Status: completed" in ticket.description
        assert "Update note: passed" in ticket.description
        assert "Evidence:" in ticket.description

        assert audit_entries
        terminal = audit_entries[-1]
        assert terminal.run_id == ids["run_id"]
        assert terminal.status == "completed"
        assert terminal.final_verdict == "pass"
