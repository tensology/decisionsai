"""E2E: ticket through loop workflow → CLI handoff → bridge callback → loop exit."""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock, patch

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.project_cli_backends.base import BackendStatus, BackendTaskResult, ProjectCliBackend
from distr.core.workflow.loop_catalog import ELORM_LOOP_KICKOFFS
from distr.core.workflow.planning import loop_contract_to_context_rules, parse_loop_contract
from distr.core.workflow.router import StepRouter
from distr.gui.web.routes.settings.workflows import register_routes


def _make_factory(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


class LoopFakeBackend(ProjectCliBackend):
    id = "pi"
    name = "Loop Fake Executor"
    calls = 0

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
        return BackendTaskResult(
            success=True,
            backend_id=self.id,
            engine="fake_cli",
            output=(
                "Review complete: removed debug logging from diff.\n"
                f"Ticket: {task.ticket_id}\n"
                f"Loop context present: {bool(loop_ctx)}\n"
                "Evidence: minimal diff applied."
            ),
            session_id=task.audit_id,
        )


class LoopFakeWorkflowAgent:
    evaluate_calls = 0

    def __init__(self, event_queue=None):
        self.event_queue = event_queue

    async def execute(self, prompt: str) -> str:
        if "Decide if exit condition is met from prior step results." in prompt:
            type(self).evaluate_calls += 1
            if type(self).evaluate_calls < 2:
                return "Exit condition NOT met: lint still reports one unused import."
            return "Exit condition met: no slop found and checks would pass."
        return "Loop pass complete. Summary: standards met, ready to close."

    def shutdown(self) -> None:
        return None


def _seed_loop_workflow(factory, tmp_path):
    desloppify = next(e for e in ELORM_LOOP_KICKOFFS if e["name"] == "De-Sloppify Pass")
    contract = parse_loop_contract(desloppify["kickoff"])
    loop_input = {
        "goal": contract["goal"],
        "max_iterations": 2,
        "check_command": contract["check_command"],
        "exit_when": contract["exit_when"],
        "guardrails": contract.get("guardrails") or [],
    }
    context_rules = loop_contract_to_context_rules({**contract, **loop_input})

    session = factory()
    try:
        workflow = AutoWorkflow(
            name="De-Sloppify Pass",
            description=desloppify["kickoff"],
            workflow_type="instruction",
            status="active",
            context_rules=context_rules,
            workflow_input=json.dumps(loop_input),
        )
        session.add(workflow)
        session.flush()

        project = Project(
            name="Loop Demo Project",
            folder_location=str(tmp_path),
            coding_backend="pi",
            in_use=True,
        )
        session.add(project)
        session.flush()

        board = KanbanBoard(name="Loop Board", in_use=True, default_project_id=project.id)
        session.add(board)
        session.flush()
        lane = KanbanLane(board_id=board.id, name="Queue", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title="De-sloppify recent auth changes",
            description="Run the de-sloppify loop on the auth module diff.",
            complexity="medium",
            linked_project_id=project.id,
            linked_workflow_id=workflow.id,
            position=0,
        )
        session.add(ticket)
        session.flush()

        steps_data = [
            ("Review diff for slop", "send_to_project_cli", "Review diff and fix slop with minimal diffs.", {}),
            ("Run lint and tests", "run_command", "echo 'lint ok && tests ok'", {"command": "echo 'lint ok && tests ok'"}),
            ("Evaluate loop exit", "agent_instruction", "Decide if exit condition is met from prior step results.", {"validation": "Exit condition met"}),
            ("Report outcome", "agent_instruction", "Give a short status: passed, max iterations, or blockers.", {"validation": "Loop pass complete"}),
        ]
        step_ids = []
        for i, (name, action_type, instruction, cfg) in enumerate(steps_data):
            validation_type = "none"
            validation_prompt = ""
            if action_type == "agent_instruction" and cfg.get("validation"):
                validation_type = "text_match"
                validation_prompt = cfg["validation"]
            step = AutoWorkflowStep(
                workflow_id=workflow.id,
                name=name,
                position=i,
                action_type=action_type,
                instruction=instruction,
                step_type=action_type,
                config=json.dumps({k: v for k, v in cfg.items() if k != "validation"}) if cfg else None,
                validation_type=validation_type,
                validation_prompt=validation_prompt,
                status="pending",
            )
            session.add(step)
            session.flush()
            step_ids.append(step.id)

        evaluate = session.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_ids[2]).one()
        evaluate.on_fail_goto = step_ids[0]
        session.flush()

        ids = {
            "workflow_id": workflow.id,
            "project_id": project.id,
            "board_id": board.id,
            "ticket_id": ticket.id,
            "step_ids": step_ids,
        }
        session.commit()
        return ids
    finally:
        session.close()


def test_loop_ticket_handoff_bridge_and_exit(tmp_path):
    from distr.core.project_cli_backends import registry as backend_registry
    from distr.core.workflow.dispatcher import _active_runs, _runs_lock, start_workflow_run

    factory = _make_factory(tmp_path)
    ids = _seed_loop_workflow(factory, tmp_path)
    LoopFakeBackend.calls = 0
    LoopFakeWorkflowAgent.evaluate_calls = 0

    def get_session():
        return _session_ctx(factory)

    original_pi = backend_registry._BACKENDS.get("pi")
    backend_registry._BACKENDS["pi"] = LoopFakeBackend()

    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    patches = [
        patch("distr.core.db.get_session", get_session),
        patch("distr.core.orchestrator.get_session", get_session),
        patch("distr.core.kanban.project_execution.get_session", get_session),
        patch("distr.core.workflow.dispatcher.get_session", get_session),
        patch("distr.core.workflow.post_execution.get_session", get_session),
        patch("distr.core.workflow.router.get_session", get_session),
        patch("distr.core.workflow.service.get_session", get_session),
        patch("distr.core.workflow.step_executor.get_session", get_session),
        patch("distr.core.workflow.standards_memory.get_session", get_session),
        patch("distr.core.settings.load_settings_from_db", lambda: {
            "project_cli_low_backend": "pi",
            "project_cli_medium_backend": "pi",
            "project_cli_high_backend": "pi",
            "project_cli_low_model": "auto",
            "project_cli_medium_model": "auto",
            "project_cli_high_model": "auto",
            "hermes_validator_enabled": False,
        }),
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
        patch("distr.core.workflow_agent.WorkflowAgent", LoopFakeWorkflowAgent),
        patch("distr.core.orchestrator.is_orchestrator_enabled", lambda: True),
        patch("distr.core.kanban.project_execution.append_execution_event", MagicMock()),
    ]

    try:
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)

            result = start_workflow_run(
                ids["workflow_id"],
                board_id=ids["board_id"],
                ticket_id=ids["ticket_id"],
                run_metadata={
                    "project_id": ids["project_id"],
                    "project_name": "Loop Demo Project",
                    "project_folder": str(tmp_path),
                    "ticket_title": "De-sloppify recent auth changes",
                },
            )
            assert "error" not in result, result
            run_id = result["run_id"]

            import time

            deadline = time.time() + 30
            final_status = None
            while time.time() < deadline:
                with get_session() as session:
                    run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
                    final_status = run.status
                    if final_status in {"completed", "failed", "cancelled"}:
                        break
                time.sleep(0.2)

            with get_session() as session:
                run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
                run_data = json.loads(run.run_data or "{}")
                events = (
                    session.query(OrchestratorEvent)
                    .filter(OrchestratorEvent.run_id == run_id)
                    .order_by(OrchestratorEvent.id.asc())
                    .all()
                )
                final_run_status = run.status

            assert final_run_status == "completed", (
                f"run stuck: {final_run_status}, cli_calls={LoopFakeBackend.calls}, "
                f"evaluate_calls={LoopFakeWorkflowAgent.evaluate_calls}, data={run_data}"
            )
            assert run_data.get("loop_contract", {}).get("max_iterations") == 2
            assert run_data.get("loop_iteration", 0) >= 1
            assert "loop_started" in [e.event_type for e in events]
            assert LoopFakeBackend.calls >= 2
            handoffs = run_data.get("backend_handoffs") or []
            assert any(h.get("loop_context_summary") for h in handoffs if isinstance(h, dict))
            assert any(h.get("callback", {}).get("bridge_url") for h in handoffs if isinstance(h, dict))

            bridge = client.post(
                f"/api/workflows/{ids['workflow_id']}/runs/{run_id}/codex-events",
                json={
                    "event_type": "codex_completed",
                    "status": "completed",
                    "message": "Worker finished review pass.",
                    "step_id": ids["step_ids"][0],
                    "ticket_id": ids["ticket_id"],
                    "project_id": ids["project_id"],
                },
            )
            assert bridge.status_code == 200
            assert bridge.json().get("success") is True

            with _runs_lock:
                ctx = _active_runs.pop(run_id, None)
            if ctx:
                ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
    finally:
        if original_pi is not None:
            backend_registry._BACKENDS["pi"] = original_pi


def test_loop_iteration_routing_respects_max_iterations(tmp_path):
    factory = _make_factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    with get_session() as session:
        wf = AutoWorkflow(name="Loop router test", status="active")
        session.add(wf)
        session.flush()
        s0 = AutoWorkflowStep(workflow_id=wf.id, position=0, name="Work", action_type="agent_instruction", instruction="work")
        s1 = AutoWorkflowStep(workflow_id=wf.id, position=1, name="Gate", action_type="agent_instruction", instruction="gate")
        s2 = AutoWorkflowStep(workflow_id=wf.id, position=2, name="Report", action_type="agent_instruction", instruction="report")
        session.add_all([s0, s1, s2])
        session.flush()
        s1.on_fail_goto = s0.id
        run = AutoWorkflowRun(
            workflow_id=wf.id,
            status="running",
            current_step_id=s1.id,
            run_data=json.dumps({
                "loop_contract": {"max_iterations": 2, "goal": "test"},
                "loop_iteration": 1,
            }),
        )
        session.add(run)
        session.flush()
        run_id, gate_id, report_id = run.id, s1.id, s2.id

    router = StepRouter()
    with patch("distr.core.workflow.router.get_session", get_session):
        with patch("distr.core.workflow.router.increment_workflow_updated", MagicMock()):
            with patch("distr.core.workflow.router.increment_kanban_updated", MagicMock()):
                with patch("distr.core.workflow.router.record_workflow_chat_event", MagicMock()):
                    with patch("distr.core.workflow.verification._run_verification", return_value=False):
                        with patch.object(router, "_enter_wait_state", return_value={"action": "waiting"}):
                            decision = router.route(gate_id, "still failing", False, run_id, skip_wait=True)

    assert decision.get("action") == "next_step"
    assert decision.get("step_id") == report_id

    with get_session() as session:
        run_data = json.loads(session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one().run_data)
    assert run_data.get("loop_iteration") == 2
