"""E2E: workflow → IDE handoff → human iteration → Hermes learning."""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.project_cli_backends.base import BackendStatus, BackendTaskResult, ProjectCliBackend


def _make_factory(tmp_path):
    import distr.core.db.hermes  # noqa: F401
    import distr.core.db.kanban  # noqa: F401
    import distr.core.db.projects  # noqa: F401
    import distr.core.db.workflow  # noqa: F401

    db_path = tmp_path / "hermes_ide.sqlite3"
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


class HermesFakeIdeBackend(ProjectCliBackend):
    id = "vscode_ide"
    name = "Hermes Fake IDE"

    def check_availability(self) -> BackendStatus:
        return BackendStatus(
            id=self.id,
            name=self.name,
            installed=True,
            ready=True,
            state="ready",
            message="Fake IDE ready.",
            path="/usr/bin/false",
        )

    async def send_task(self, task, on_event=None) -> BackendTaskResult:
        try:
            from distr.core.hermes import emit_event

            emit_event(
                source=self.id,
                event_type="ide_work_packet_created",
                status="waiting",
                workflow_id=task.workflow_id,
                run_id=task.run_id,
                step_id=task.step_id or task.audit_id,
                ticket_id=task.ticket_id,
                board_id=task.board_id,
                project_id=task.project_id,
                execution_session_id=task.execution_session_id,
                summary=f"IDE work packet created for {self.name}.",
                payload={"ticket_path": ".tickets/decisionsai_test.md", "editor": self.id},
            )
        except Exception:
            pass
        return BackendTaskResult(
            success=True,
            backend_id=self.id,
            engine="ide_ticket",
            output="Created IDE work packet for Hermes Fake IDE: .tickets/decisionsai_test.md",
            session_id=task.audit_id,
        )


class HermesFakeWorkflowAgent:
    def shutdown(self) -> None:
        return None


def _seed_ide_workflow(factory, tmp_path):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

    session = factory()
    try:
        workflow = AutoWorkflow(
            name="IDE Iteration Workflow",
            description="Routes ticket to IDE and waits for human iteration.",
            status="active",
        )
        session.add(workflow)
        session.flush()

        project = Project(
            name="IDE Demo Project",
            description="Project for IDE iteration E2E.",
            folder_location=str(tmp_path),
            coding_backend="vscode_ide",
            in_use=True,
        )
        session.add(project)
        session.flush()

        board_policy = {
            "complexity_routing": {
                "medium": {"backend": "vscode_ide", "model": "auto"},
            }
        }
        board = KanbanBoard(
            name="IDE Board",
            in_use=True,
            default_project_id=project.id,
            default_workflow_id=workflow.id,
            hermes_policy=json.dumps(board_policy),
        )
        session.add(board)
        session.flush()

        lane = KanbanLane(board_id=board.id, name="Queued", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title="IDE iteration ticket",
            description="Should hand off to IDE, wait, then validate after continue.",
            priority="medium",
            complexity="medium",
            linked_project_id=project.id,
            linked_workflow_id=workflow.id,
            position=0,
        )
        session.add(ticket)
        session.flush()

        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Send to IDE",
            position=0,
            action_type="send_to_project_cli",
            instruction="Implement the requested change in the IDE.",
            validation_type="text_match",
            validation_prompt="IDE iteration complete with tests passing.",
            status="pending",
            linked_project_id=project.id,
        )
        session.add(step)
        session.flush()

        ids = {
            "workflow_id": workflow.id,
            "project_id": project.id,
            "board_id": board.id,
            "ticket_id": ticket.id,
            "step_id": step.id,
        }
        session.commit()
        return ids
    finally:
        session.close()


def test_ide_workflow_handoff_learns_from_iteration(tmp_path):
    from distr.core.db.hermes import HermesEvent, HermesLearnedRule
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.project_cli_backends import registry as backend_registry
    from distr.core.workflow.dispatcher import continue_waiting_step, start_workflow_run

    factory = _make_factory(tmp_path)
    ids = _seed_ide_workflow(factory, tmp_path)

    def get_session():
        return _session_ctx(factory)

    original_backend = backend_registry._BACKENDS.get("vscode_ide")
    backend_registry._BACKENDS["vscode_ide"] = HermesFakeIdeBackend()

    patches = [
        patch("distr.core.db.get_session", get_session),
        patch("distr.core.hermes.get_session", get_session),
        patch("distr.core.kanban.project_execution.get_session", get_session),
        patch("distr.core.workflow.dispatcher.get_session", get_session),
        patch("distr.core.workflow.post_execution.get_session", get_session),
        patch("distr.core.workflow.router.get_session", get_session),
        patch("distr.core.workflow.service.get_session", get_session),
        patch("distr.core.workflow.step_executor.get_session", get_session),
        patch("distr.core.settings.load_settings_from_db", lambda: {
            "hermes_enabled": True,
            "project_cli_low_backend": "pi",
            "project_cli_medium_backend": "vscode_ide",
            "project_cli_high_backend": "codex",
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
        patch("distr.core.workflow_agent.WorkflowAgent", HermesFakeWorkflowAgent),
    ]

    try:
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)

            start_result = start_workflow_run(
                ids["workflow_id"],
                context="Run IDE iteration ticket.",
                board_id=ids["board_id"],
                ticket_id=ids["ticket_id"],
                run_metadata={
                    "board_id": ids["board_id"],
                    "ticket_id": ids["ticket_id"],
                    "project_id": ids["project_id"],
                },
            )
            assert start_result.get("success") is True or "run_id" in start_result
            run_id = start_result["run_id"]

            with get_session() as session:
                run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                assert run is not None
                assert run.status == "waiting"
                run_data = json.loads(run.run_data or "{}")
                assert run_data.get("waiting_kind") == "ide_handoff"
                assert run_data.get("ide_handoff_pending") is not True

                events = (
                    session.query(HermesEvent)
                    .filter(HermesEvent.board_id == ids["board_id"])
                    .order_by(HermesEvent.id.asc())
                    .all()
                )
                event_types = [row.event_type for row in events]
                assert "ide_work_packet_created" in event_types
                assert "execution_session_created" in event_types

            feedback = "IDE iteration complete with tests passing."
            continue_result = continue_waiting_step(run_id, feedback)
            assert continue_result.get("success") is True or continue_result.get("action") in {
                "next_step",
                "end_run",
            }

            with get_session() as session:
                run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                assert run.status in {"completed", "running", "waiting"}

                events = (
                    session.query(HermesEvent)
                    .filter(HermesEvent.board_id == ids["board_id"])
                    .order_by(HermesEvent.id.asc())
                    .all()
                )
                event_types = [row.event_type for row in events]
                assert "ide_iteration_completed" in event_types
                assert "validation_recorded" in event_types

                rules = (
                    session.query(HermesLearnedRule)
                    .filter(HermesLearnedRule.scope == "board")
                    .filter(HermesLearnedRule.scope_id == ids["board_id"])
                    .all()
                )
                assert any(row.rule_type == "ide_iteration" for row in rules)
    finally:
        if original_backend is not None:
            backend_registry._BACKENDS["vscode_ide"] = original_backend
