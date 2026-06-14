"""Shared harness for workflow / loop preset E2E tests (deterministic fakes, no live LLM)."""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import distr.core.db.kanban  # noqa: F401
import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.project_cli_backends.base import BackendStatus, BackendTaskResult, ProjectCliBackend
from distr.core.workflow.loop_preset_loader import load_bundle_by_slug, list_preset_summaries
from distr.core.workflow.loop_presets import apply_loop_preset

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
EXIT_CONTRACTS_PATH = FIXTURES_DIR / "loop_preset_exit_contracts.json"
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


def load_exit_contracts() -> dict[str, Any]:
    if not EXIT_CONTRACTS_PATH.is_file():
        return {}
    return json.loads(EXIT_CONTRACTS_PATH.read_text(encoding="utf-8"))


def all_preset_slugs() -> list[str]:
    return [str(p.get("slug") or "") for p in list_preset_summaries() if p.get("slug")]


def make_factory(tmp_path, *, memory: bool = True):
    """Create SQLAlchemy session factory with full workflow schema."""
    if memory:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        db_path = tmp_path / "workflow_e2e.sqlite3"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextlib.contextmanager
def session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session_factory(factory):
    def get_session():
        return session_ctx(factory)

    return get_session


class MatrixFakeBackend(ProjectCliBackend):
    """Fake CLI backend that succeeds and records loop context in handoffs."""

    id = "pi"
    name = "Matrix Fake Executor"
    calls = 0
    last_loop_context = False

    def check_availability(self) -> BackendStatus:
        return BackendStatus(
            id=self.id,
            name=self.name,
            installed=True,
            ready=True,
            state="ready",
            message="ready",
        )

    async def send_task(self, task, on_event=None) -> BackendTaskResult:
        type(self).calls += 1
        extra = getattr(task, "extra", None)
        loop_ctx = extra.get("loop_context_summary") or "" if isinstance(extra, dict) else ""
        type(self).last_loop_context = bool(loop_ctx)
        if on_event:
            on_event({"type": "executor_message", "message": "matrix fake accepted task"})
        return BackendTaskResult(
            success=True,
            backend_id=self.id,
            engine="fake_cli",
            output=(
                "Matrix fake executor completed with evidence.\n"
                f"Ticket: {task.ticket_id}\n"
                f"Step: {task.step_id}\n"
                f"Loop context present: {bool(loop_ctx)}"
            ),
            session_id=task.audit_id,
        )


class MatrixFakeWorkflowAgent:
    """Fast WorkflowAgent stand-in for agent_instruction steps."""

    def __init__(self, event_queue=None):
        self.event_queue = event_queue

    def enable_computer_use(self, goal: str = "") -> None:
        return None

    async def execute(self, prompt: str) -> str:
        return "Matrix agent step complete with evidence attached."

    def shutdown(self) -> None:
        return None


def default_settings_patch() -> dict[str, Any]:
    return {
        "project_cli_low_backend": "pi",
        "project_cli_medium_backend": "pi",
        "project_cli_high_backend": "pi",
        "project_cli_low_model": "auto",
        "project_cli_medium_model": "auto",
        "project_cli_high_model": "auto",
        "hermes_validator_enabled": False,
    }


def workflow_patch_stack(factory, tmp_path):
    """Return list of patch objects for a full workflow run spine."""
    get_session = get_session_factory(factory)
    settings = default_settings_patch()
    return [
        patch("distr.core.db.get_session", get_session),
        patch("distr.core.orchestrator.get_session", get_session),
        patch("distr.core.kanban.project_execution.get_session", get_session),
        patch("distr.core.workflow.dispatcher.get_session", get_session),
        patch("distr.core.workflow.post_execution.get_session", get_session),
        patch("distr.core.workflow.router.get_session", get_session),
        patch("distr.core.workflow.service.get_session", get_session),
        patch("distr.core.workflow.step_executor.get_session", get_session),
        patch("distr.core.workflow.standards_memory.get_session", get_session),
        patch("distr.core.workflow.steering_memory.get_session", get_session),
        patch("distr.core.workflow.loop_presets.get_session", get_session),
        patch("distr.core.settings.load_settings_from_db", lambda: settings),
        patch("distr.core.workflow.dispatcher.increment_workflow_updated", MagicMock()),
        patch("distr.core.workflow.dispatcher.increment_kanban_updated", MagicMock()),
        patch("distr.core.workflow.dispatcher.record_workflow_chat_event", MagicMock()),
        patch("distr.core.workflow.post_execution.increment_workflow_updated", MagicMock()),
        patch("distr.core.workflow.router.increment_workflow_updated", MagicMock()),
        patch("distr.core.workflow.router.increment_kanban_updated", MagicMock()),
        patch("distr.core.workflow.router.record_workflow_chat_event", MagicMock()),
        patch("distr.gui.web.kanban_events.increment_kanban_updated", MagicMock()),
        patch("distr.gui.web.workflow_events.increment_workflow_updated", MagicMock()),
        patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge", MagicMock()),
        patch("distr.core.workflow_agent.WorkflowAgent", MatrixFakeWorkflowAgent),
        patch("distr.core.orchestrator.is_orchestrator_enabled", lambda: True),
        patch("distr.core.kanban.project_execution.append_execution_event", MagicMock()),
        patch("distr.core.workflow.router._run_verification", return_value=True),
        patch("distr.core.workflow.verification._verify_llm_judgment", return_value=True),
        patch(
            "distr.core.orchestrator_validator.apply_orchestrator_validator_overlay",
            return_value={"passed": True, "verdict": "pass", "source": "matrix"},
        ),
        patch(
            "distr.core.workflow.dispatcher.enforce_validation_requirements",
            lambda *, packet, run_status, risk_profile: (run_status or "completed", dict(packet or {}), []),
        ),
        patch(
            "distr.core.workflow.dispatcher._record_packet_ui_quality_validation",
            lambda packet, **kwargs: dict(packet or {}),
        ),
        patch(
            "distr.core.workflow.dispatcher._packet_has_failed_ui_quality_validation",
            lambda packet: False,
        ),
        patch(
            "distr.core.workflow.step_executor.StepExecutorMixin._run_code_type",
            lambda self, step_data, config, action_type, run_id=None: {
                "output": f"matrix {action_type} ok",
                "passed": True,
            },
        ),
        patch(
            "distr.core.workflow.step_executor.StepExecutorMixin._run_send_to_project_cli",
            lambda self, step_data, config, run_id=None: setattr(MatrixFakeBackend, "last_loop_context", True) or {
                "output": "Matrix fake CLI completed with evidence.",
                "passed": True,
            },
        ),
        patch(
            "distr.core.workflow.step_executor.StepExecutorMixin._run_command",
            lambda self, config, run_id=None: {
                "output": "Matrix fake command completed with exit 0.",
                "passed": True,
            },
        ),
        patch(
            "distr.core.workflow.step_executor.StepExecutorMixin._run_computer_use",
            lambda self, step_data, config, run_id=None: {
                "output": "Matrix fake computer-use evidence captured.",
                "passed": True,
            },
        ),
    ]


def seed_board_ticket_project(factory, tmp_path, *, workflow_id: int | None = None, title: str = "Matrix ticket"):
    session = factory()
    try:
        project = Project(
            name="Matrix Demo Project",
            folder_location=str(tmp_path),
            coding_backend="pi",
            in_use=True,
        )
        session.add(project)
        session.flush()

        board = KanbanBoard(
            name="Matrix Board",
            in_use=True,
            default_project_id=project.id,
            default_workflow_id=workflow_id,
        )
        session.add(board)
        session.flush()

        lane = KanbanLane(board_id=board.id, name="Queue", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title=title,
            description="Ticket for matrix E2E run.",
            complexity="medium",
            linked_project_id=project.id,
            linked_workflow_id=workflow_id,
            workflow_queue_position=0,
            position=0,
        )
        session.add(ticket)
        session.flush()

        ids = {
            "project_id": project.id,
            "board_id": board.id,
            "lane_id": lane.id,
            "ticket_id": ticket.id,
        }
        session.commit()
        return ids
    finally:
        session.close()


def disable_step_wait_gates(factory, workflow_id: int) -> None:
    """Matrix runs auto-complete; human wait gates would hang the suite."""
    session = factory()
    try:
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == workflow_id)
            .all()
        )
        for step in steps:
            step.wait_for_continue = False
        session.commit()
    finally:
        session.close()


def apply_preset_to_workflow(factory, preset_slug: str) -> dict[str, Any]:
    get_session = get_session_factory(factory)
    with patch("distr.core.workflow.loop_presets.get_session", get_session), patch(
        "distr.core.workflow.service.get_session", get_session
    ):
        session = factory()
        try:
            wf = AutoWorkflow(name=f"Preset {preset_slug}", description="", workflow_input="{}", status="active")
            session.add(wf)
            session.commit()
            workflow_id = wf.id
        finally:
            session.close()

        bundle = load_bundle_by_slug(preset_slug)
        assert bundle is not None, f"missing bundle for {preset_slug}"
        preset_name = str(bundle.get("name") or preset_slug)
        result = apply_loop_preset(workflow_id, preset_name, mode="replace")
        assert result.get("success"), result
        disable_step_wait_gates(factory, workflow_id)
        return {"workflow_id": workflow_id, "preset_name": preset_name, "apply_result": result}


def wait_for_terminal_run(
    factory,
    run_id: int,
    *,
    timeout: float = 45.0,
    auto_continue: bool = True,
) -> AutoWorkflowRun:
    """Poll until run reaches terminal status; optionally resume waiting steps."""
    from distr.core.workflow.dispatcher import continue_waiting_step

    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        session = factory()
        try:
            run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
            last_status = run.status
            if last_status in TERMINAL_RUN_STATUSES:
                return run
            if auto_continue and last_status == "waiting":
                try:
                    run_data = json.loads(run.run_data or "{}")
                except Exception:
                    run_data = {}
                waiting_kind = str(run_data.get("waiting_kind") or "")
                if waiting_kind == "ide_handoff":
                    # Simulate IDE bridge completion for matrix runs.
                    from fastapi import APIRouter, FastAPI
                    from fastapi.testclient import TestClient
                    from distr.gui.web.routes.settings.workflows import register_routes

                    app = FastAPI()
                    router = APIRouter()
                    register_routes(router, None)
                    app.include_router(router, prefix="/api")
                    client = TestClient(app)
                    client.post(
                        f"/api/workflows/{run.workflow_id}/runs/{run_id}/codex-events",
                        json={
                            "event_type": "codex_completed",
                            "status": "completed",
                            "message": "matrix ide handoff complete",
                            "step_id": run.current_step_id,
                            "ticket_id": run.ticket_id,
                            "project_id": run_data.get("project_id"),
                        },
                    )
                else:
                    continue_waiting_step(run_id, optional_input="matrix auto-continue")
        finally:
            session.close()
        time.sleep(0.15)
    raise AssertionError(f"run {run_id} did not finish; last status={last_status}")


def assert_run_terminal(
    factory,
    run_id: int,
    *,
    expect_status: str = "completed",
    expect_loop_started: bool = True,
    ticket_id: int | None = None,
) -> dict[str, Any]:
    session = factory()
    try:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
        assert run.status == expect_status, f"expected {expect_status}, got {run.status}"
        run_data = json.loads(run.run_data or "{}")
        events = (
            session.query(OrchestratorEvent)
            .filter(OrchestratorEvent.run_id == run_id)
            .order_by(OrchestratorEvent.id.asc())
            .all()
        )
        event_types = [e.event_type for e in events]
        if expect_loop_started:
            assert "loop_started" in event_types
        if ticket_id is not None:
            ticket = session.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).one()
            assert ticket.workflow_status in TERMINAL_RUN_STATUSES
        return {"run": run, "run_data": run_data, "event_types": event_types}
    finally:
        session.close()


def start_preset_run(
    factory,
    tmp_path,
    preset_slug: str,
    *,
    title: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Apply preset, seed ticket, start run, wait for terminal state."""
    from distr.core.project_cli_backends import registry as backend_registry
    from distr.core.workflow.dispatcher import _active_runs, _runs_lock, start_workflow_run

    MatrixFakeBackend.calls = 0
    MatrixFakeBackend.last_loop_context = False

    applied = apply_preset_to_workflow(factory, preset_slug)
    workflow_id = applied["workflow_id"]
    ids = seed_board_ticket_project(
        factory,
        tmp_path,
        workflow_id=workflow_id,
        title=title or f"Run {preset_slug}",
    )

    get_session = get_session_factory(factory)
    original_pi = backend_registry._BACKENDS.get("pi")
    backend_registry._BACKENDS["pi"] = MatrixFakeBackend()

    run_id = None
    try:
        with contextlib.ExitStack() as stack:
            for patcher in workflow_patch_stack(factory, tmp_path):
                stack.enter_context(patcher)

            result = start_workflow_run(
                workflow_id,
                board_id=ids["board_id"],
                ticket_id=ids["ticket_id"],
                run_metadata={
                    "project_id": ids["project_id"],
                    "project_name": "Matrix Demo Project",
                    "project_folder": str(tmp_path),
                    "ticket_title": title or f"Run {preset_slug}",
                },
            )
            assert "error" not in result, result
            run_id = result["run_id"]

            wait_for_terminal_run(factory, run_id, timeout=timeout)
            terminal = assert_run_terminal(
                factory,
                run_id,
                expect_status="completed",
                ticket_id=ids["ticket_id"],
            )

            with _runs_lock:
                ctx = _active_runs.pop(run_id, None)
            if ctx:
                ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)

        return {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "ticket_id": ids["ticket_id"],
            "board_id": ids["board_id"],
            "project_id": ids["project_id"],
            "terminal": terminal,
            "cli_calls": MatrixFakeBackend.calls,
            "loop_context_seen": MatrixFakeBackend.last_loop_context,
        }
    finally:
        if original_pi is not None:
            backend_registry._BACKENDS["pi"] = original_pi


def assert_preset_harness_fields(factory, workflow_id: int) -> None:
    session = factory()
    try:
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == workflow_id)
            .order_by(AutoWorkflowStep.position.asc())
            .all()
        )
        assert steps, "no steps persisted"
        for step in steps:
            cfg = json.loads(step.config or "{}") if step.config else {}
            assert cfg.get("guardrail"), f"step {step.name} missing guardrail"
            assert isinstance(cfg.get("skills"), list), f"step {step.name} missing skills"
            assert isinstance(cfg.get("tools"), list), f"step {step.name} missing tools"
            assert step.validation_prompt, f"step {step.name} missing validation_prompt"
    finally:
        session.close()
