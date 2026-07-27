"""E2E coverage for the orchestrator workflow backbone."""

from __future__ import annotations

import contextlib
import json
import time
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.project_cli_backends.base import BackendStatus, BackendTaskResult, ProjectCliBackend


def _make_factory(tmp_path):
    # Import every model family needed by the workflow, executor, and orchestrator ledgers
    # before create_all so the in-memory schema matches the real spine.
    import distr.core.db.orchestrator  # noqa: F401
    import distr.core.db.kanban  # noqa: F401
    import distr.core.db.projects  # noqa: F401
    import distr.core.db.workflow  # noqa: F401

    db_path = tmp_path / "orchestrator_backbone.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


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


class ProjectExecutorFakeBackend(ProjectCliBackend):
    id = "pi"
    name = "Project Executor Fake"

    def check_availability(self) -> BackendStatus:
        return BackendStatus(
            id=self.id,
            name=self.name,
            installed=True,
            ready=True,
            state="ready",
            message="Project executor fake is ready.",
        )

    async def send_task(self, task, on_event=None) -> BackendTaskResult:
        if on_event:
            on_event({
                "type": "executor_message",
                "message": "Project executor fake accepted the workflow ticket.",
            })
        return BackendTaskResult(
            success=True,
            backend_id=self.id,
            engine="fake_cli",
            output=(
                "Project executor fake completed the ticket.\n"
                f"Ticket: {task.ticket_id}\n"
                f"Workflow: {task.workflow_id}\n"
                f"Step: {task.step_id}\n"
                "Evidence: deterministic executor output returned."
            ),
            session_id=task.audit_id,
        )


class FakeWorkflowAgent:
    def __init__(self, event_queue=None):
        self.event_queue = event_queue

    def shutdown(self) -> None:
        return None


def _seed_ticket_workflow(factory, tmp_path):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

    session = factory()
    try:
        workflow = AutoWorkflow(
            name="Orchestrator Ticket Workflow",
            description="Routes a queued ticket through the project executor, validation, and audit.",
            status="active",
        )
        session.add(workflow)
        session.flush()

        project = Project(
            name="Orchestrator Demo Project",
            description="Project used by the orchestrator backbone E2E.",
            folder_location=str(tmp_path),
            coding_backend="pi",
            in_use=True,
        )
        session.add(project)
        session.flush()

        stale_step_project = Project(
            name="Stale Step Project",
            description="A stale step-level link that must not override the ticket route.",
            folder_location=str(tmp_path / "stale"),
            coding_backend="pi",
            in_use=False,
        )
        session.add(stale_step_project)
        session.flush()

        board = KanbanBoard(
            name="Orchestrator Board",
            in_use=True,
            default_project_id=project.id,
            default_workflow_id=workflow.id,
        )
        session.add(board)
        session.flush()

        lane = KanbanLane(board_id=board.id, name="Queued", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Process ticket through project executor",
            description="This ticket should be executed, validated, audited, and written back.",
            priority="high",
            complexity="high",
            linked_project_id=project.id,
            linked_workflow_id=workflow.id,
            position=0,
        )
        session.add(ticket)
        session.flush()

        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Execute ticket in project executor",
            position=0,
            action_type="send_to_project_cli",
            instruction="Process this ticket through the configured project executor.",
            validation_type="text_match",
            validation_prompt="Project executor fake completed the ticket.",
            status="pending",
            linked_project_id=stale_step_project.id,
        )
        session.add(step)
        session.flush()

        ids = {
            "workflow_id": workflow.id,
            "project_id": project.id,
            "board_id": board.id,
            "lane_id": lane.id,
            "ticket_id": ticket.id,
            "step_id": step.id,
        }
        session.commit()
        return ids
    finally:
        session.close()


def test_ticket_workflow_uses_orchestrator_backbone(tmp_path):
    from distr.core.db.kanban import KanbanTicket, KanbanTicketAuditEntry, ProjectExecutionSession
    from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorValidationRecord
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.project_cli_backends import registry as backend_registry
    from distr.core.workflow.dispatcher import _active_runs, _runs_lock, start_workflow_run

    factory = _make_factory(tmp_path)
    ids = _seed_ticket_workflow(factory, tmp_path)

    def get_session():
        return _session_ctx(factory)

    original_pi_backend = backend_registry._BACKENDS.get("pi")
    backend_registry._BACKENDS["pi"] = ProjectExecutorFakeBackend()

    patches = [
        patch("distr.core.db.get_session", get_session),
        patch("distr.core.orchestrator.get_session", get_session),
        patch("distr.core.kanban.project_execution.get_session", get_session),
        patch("distr.core.workflow.dispatcher.get_session", get_session),
        patch("distr.core.workflow.post_execution.get_session", get_session),
        patch("distr.core.workflow.router.get_session", get_session),
        patch("distr.core.workflow.runtime_contract.get_session", get_session),
        patch("distr.core.workflow.service.get_session", get_session),
        patch("distr.core.workflow.step_executor.get_session", get_session),
        patch("distr.core.settings.load_settings_from_db", lambda: {
            "project_cli_low_backend": "pi",
            "project_cli_medium_backend": "pi",
            "project_cli_high_backend": "pi",
            "project_cli_low_model": "fake-low",
            "project_cli_medium_model": "fake-medium",
            "project_cli_high_model": "fake-high",
        }),
        patch("distr.core.workflow.dispatcher.increment_workflow_updated", MagicMock()),
        patch("distr.core.workflow.dispatcher.increment_kanban_updated", MagicMock()),
        patch("distr.core.workflow.dispatcher.record_workflow_chat_event", MagicMock()),
        patch("distr.core.workflow.post_execution.increment_workflow_updated", MagicMock()),
        patch("distr.core.workflow.router.increment_workflow_updated", MagicMock()),
        patch("distr.core.workflow.router.increment_kanban_updated", MagicMock()),
        patch("distr.core.workflow.router.record_workflow_chat_event", MagicMock()),
        patch("distr.gui.web.kanban_events.increment_kanban_updated", MagicMock()),
        patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge", MagicMock()),
        patch("distr.core.workflow_agent.WorkflowAgent", FakeWorkflowAgent),
    ]

    try:
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)

            result = start_workflow_run(
                ids["workflow_id"],
                context="Run the queued project executor ticket.",
                board_id=ids["board_id"],
                ticket_id=ids["ticket_id"],
                run_metadata={
                    "source_type": "orchestrator_backbone_e2e",
                    "board_id": ids["board_id"],
                    "board_name": "Orchestrator Board",
                    "ticket_id": ids["ticket_id"],
                    "ticket_title": "Process ticket through project executor",
                    "project_id": ids["project_id"],
                    "project_name": "Orchestrator Demo Project",
                    "project_folder": str(tmp_path),
                    "skip_run_briefing": True,
                },
            )

            assert "error" not in result, result
            run_id = result["run_id"]
            deadline = time.time() + 5.0
            execution = None
            while time.time() < deadline:
                with get_session() as session:
                    execution = (
                        session.query(ProjectExecutionSession)
                        .filter(ProjectExecutionSession.run_id == run_id)
                        .filter(ProjectExecutionSession.ticket_id == ids["ticket_id"])
                        .first()
                    )
                    if execution:
                        break
                time.sleep(0.05)

            with get_session() as session:
                run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
                ticket = session.query(KanbanTicket).filter(KanbanTicket.id == ids["ticket_id"]).one()
                assert execution is not None
                execution = session.merge(execution)
                validations = (
                    session.query(OrchestratorValidationRecord)
                    .filter(OrchestratorValidationRecord.run_id == run_id)
                    .all()
                )
                events = (
                    session.query(OrchestratorEvent)
                    .filter(OrchestratorEvent.run_id == run_id)
                    .order_by(OrchestratorEvent.id.asc())
                    .all()
                )
                ticket_audit = (
                    session.query(KanbanTicketAuditEntry)
                    .filter(KanbanTicketAuditEntry.ticket_id == ids["ticket_id"])
                    .order_by(KanbanTicketAuditEntry.id.asc())
                    .all()
                )

            event_types = [event.event_type for event in events]
            legacy_event_types = [
                (json.loads(event.payload or "{}").get("orchestration") or {}).get("legacy_event_type")
                for event in events
            ]

            assert run.status == "completed"
            assert run.ticket_id == ids["ticket_id"]
            assert run.board_id == ids["board_id"]
            assert json.loads(run.run_data or "{}")["result_packet"]["status"] == "completed"
            run_data = json.loads(run.run_data or "{}")
            assert run_data.get("execution_route", {}).get("backend")
            assert run_data.get("terminal_receipt", {}).get("status") == "completed"

            assert ticket.workflow_status == "completed"
            assert ticket.description == "This ticket should be executed, validated, audited, and written back."
            assert ticket_audit
            assert ticket_audit[-1].run_id == run_id

            assert execution.workflow_id == ids["workflow_id"]
            assert execution.step_id == ids["step_id"]
            assert execution.project_id == ids["project_id"]
            assert execution.status == "completed"
            assert execution.route_backend == "pi"
            assert execution.selected_model == "fake-high"
            assert execution.complexity == "high"

            assert validations
            assert validations[0].ticket_id == ids["ticket_id"]
            assert validations[0].execution_session_id == execution.id
            assert validations[0].verdict == "pass"
            assert validations[0].validation_type == "text_match"

            assert "run_started" in event_types
            assert "route_decided" in event_types
            assert event_types.count("worker_dispatched") >= 2
            assert "worker_progress" in event_types
            assert "worker_completed" in event_types
            assert "workflow_run_started" in legacy_event_types
            assert "execution_session_created" in legacy_event_types
            assert "execution_executor_start" in legacy_event_types
            assert "execution_executor_message" in legacy_event_types
            assert "execution_session_completed" in legacy_event_types
            assert "validation_recorded" in event_types
            assert "workflow_step_completed" in event_types
            assert "workflow_run_completed" in legacy_event_types
            assert all(
                event.ticket_id == ids["ticket_id"]
                for event in events
                if not (event.event_type == "worker_completed" and event.source == "workflow")
            ), [
                (event.event_type, event.source, event.ticket_id)
                for event in events
                if event.ticket_id != ids["ticket_id"]
            ]

            with _runs_lock:
                ctx = _active_runs.pop(run_id, None)
            if ctx:
                ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
    finally:
        if original_pi_backend is not None:
            backend_registry._BACKENDS["pi"] = original_pi_backend
