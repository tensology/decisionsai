"""Regression coverage for the Project -> Ticket -> Workflow context spine."""

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


def _seed_project_ticket_workflow(factory):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, KanbanTicketTodo
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

    session = factory()
    try:
        workflow = AutoWorkflow(
            name="Developer Ticket Workflow",
            description="Implement a ticket with project, ticket, and validation context.",
            status="active",
        )
        session.add(workflow)
        session.flush()

        project = Project(
            name="DecisionsAI",
            description="Desktop agentic developer workflow",
            folder_location="/repo/DecisionsAI",
            startup_instructions="pytest tests/core",
            in_use=True,
        )
        session.add(project)
        session.flush()

        board = KanbanBoard(
            name="Decisions",
            in_use=True,
            agent_source_lane="Current",
            default_project_id=project.id,
            default_workflow_id=workflow.id,
        )
        session.add(board)
        session.flush()
        project.kanban_board_id = board.id

        lane = KanbanLane(board_id=board.id, name="Current", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Fix workflow validation",
            description=(
                "Workflow validation gets stuck after computer-use steps.\n\n"
                "## Recommended Skills\n"
                "- `webapp-testing` - Needs UI regression coverage\n"
                "- `verification-before-completion` - Needs truthful validation"
            ),
            priority="high",
            linked_project_id=project.id,
            linked_workflow_id=workflow.id,
            position=0,
        )
        session.add(ticket)
        session.flush()
        session.add(
            KanbanTicketTodo(
                ticket_id=ticket.id,
                text="Add regression coverage for stuck validation",
                done=False,
                position=0,
            )
        )

        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Implement and validate",
            position=0,
            action_type="agent_instruction",
            instruction="Use the ticket and project context to implement and validate the fix.",
            validation_type="llm_judgment",
            validation_prompt="Confirm the ticket context and developer context were used.",
            max_retries=1,
            timeout_seconds=300,
            status="pending",
        )
        session.add(step)
        session.flush()

        ids = {
            "project_id": project.id,
            "board_id": board.id,
            "lane_id": lane.id,
            "ticket_id": ticket.id,
            "workflow_id": workflow.id,
            "step_id": step.id,
        }
        session.commit()
        return ids
    finally:
        session.close()


def test_project_ticket_workflow_context_is_captured_and_rendered_in_step_prompt():
    """The developer spine must survive launch metadata and reach WorkflowAgent prompts."""
    from distr.core.workflow.dispatcher import StepDispatcher, _active_runs, _runs_lock, start_workflow_run

    factory = _make_factory()
    ids = _seed_project_ticket_workflow(factory)

    def get_session():
        return _session_ctx(factory)

    no_op = MagicMock()
    patches = [
        patch("distr.core.db.get_session", get_session),
        patch("distr.core.workflow.dispatcher.get_session", get_session),
        patch("distr.core.workflow.step_executor.get_session", get_session),
        patch("distr.core.workflow.service.get_session", get_session),
        patch("distr.core.workflow.dispatcher.increment_workflow_updated", no_op),
        patch("distr.core.workflow.dispatcher.increment_kanban_updated", no_op),
        patch("distr.core.workflow.dispatcher.record_workflow_chat_event", no_op),
        patch("distr.core.workflow.dispatcher.append_ticket_audit_entry", no_op),
        patch.object(StepDispatcher, "run_in_workflow", return_value={"success": True}),
    ]

    with contextlib.ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)

        result = start_workflow_run(
            ids["workflow_id"],
            context="Ticket: Fix workflow validation",
            board_id=ids["board_id"],
            ticket_id=ids["ticket_id"],
            run_metadata={
                "source_type": "project_ticket_workflow_spine_test",
                "board_id": ids["board_id"],
                "board_name": "Decisions",
                "ticket_id": ids["ticket_id"],
                "ticket_title": "Fix workflow validation",
                "project_id": ids["project_id"],
                "project_name": "DecisionsAI",
                "project_folder": "/repo/DecisionsAI",
            },
        )

        assert "error" not in result
        run_id = result["run_id"]

        with get_session() as session:
            from distr.core.db.workflow import AutoWorkflowRun

            run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            run_data = json.loads(run.run_data or "{}")

        developer_context = run_data.get("developer_context") or {}
        assert developer_context["active_project"]["name"] == "DecisionsAI"
        assert developer_context["active_project"]["folder_location"] == "/repo/DecisionsAI"
        assert developer_context["active_board"]["name"] == "Decisions"
        assert developer_context["active_board"]["default_project_id"] == ids["project_id"]
        assert developer_context["active_board"]["default_workflow_id"] == ids["workflow_id"]
        assert developer_context["active_tickets"][0]["title"] == "Fix workflow validation"
        assert developer_context["active_tickets"][0]["linked_project_id"] == ids["project_id"]

        prompt = StepDispatcher()._build_agent_prompt(
            {
                "id": ids["step_id"],
                "workflow_id": ids["workflow_id"],
                "name": "Implement and validate",
                "instruction": "Use the ticket and project context.",
                "description": "",
            },
            run_id,
        )

        with _runs_lock:
            ctx = _active_runs.pop(run_id, None)
        if ctx:
            ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)

    assert "[KANBAN TICKET CONTEXT" in prompt
    assert "Ticket ID:" in prompt
    assert "Fix workflow validation" in prompt
    assert "Workflow validation gets stuck after computer-use steps" in prompt
    assert "Checklist:" in prompt
    assert "Add regression coverage for stuck validation" in prompt
    assert "Linked project: DecisionsAI" in prompt
    assert "Project folder: /repo/DecisionsAI" in prompt
    assert "[RESULT PACKET CONTEXT]" in prompt
    assert "Workflow run started." in prompt
    assert "Developer workflow context:" in prompt
    assert "active_project: #" in prompt
    assert "DecisionsAI (/repo/DecisionsAI)" in prompt
    assert "active_board: #" in prompt
    assert "Decisions [database]" in prompt
