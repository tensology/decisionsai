"""Regression tests for ticket workflows that route implementation work to Codex."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.project_cli_backends.base import BackendTaskResult


def _make_factory():
    # Import model modules before create_all so all tables are registered.
    import distr.core.db.kanban  # noqa: F401
    import distr.core.db.projects  # noqa: F401
    import distr.core.db.workflow  # noqa: F401

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


def _seed_codex_game_ticket(factory, project_dir: Path) -> dict[str, int]:
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep

    with _session_ctx(factory) as session:
        project = Project(
            name="Arcade Prototype",
            description="Small browser games and UI experiments.",
            folder_location=str(project_dir),
            coding_backend="codex",
            coding_backend_model="gpt-5.3-codex-spark",
            startup_instructions="python3 -m http.server 4173",
            in_use=True,
        )
        session.add(project)
        session.flush()

        workflow = AutoWorkflow(
            name="Codex Development Regression",
            description="Implement a ticket through the selected project CLI backend, then validate artifacts.",
            status="active",
        )
        session.add(workflow)
        session.flush()

        board = KanbanBoard(
            name="Regression Lab",
            source="database",
            default_project_id=project.id,
            default_workflow_id=workflow.id,
            agent_source_lane="Current",
            agent_done_lane="QA / Assess",
            send_to_cli=False,
            in_use=True,
        )
        session.add(board)
        session.flush()

        lane = KanbanLane(board_id=board.id, name="Current", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Build Memory Sprint mini-game",
            description=(
                "Create a single-file browser mini-game called Memory Sprint. "
                "It should have a visible title, score counter, restart button, and enough HTML/CSS/JS "
                "for a user to click cards and see progress."
            ),
            priority="high",
            linked_project_id=project.id,
            linked_workflow_id=workflow.id,
            send_to_cli=False,
            position=0,
        )
        session.add(ticket)
        session.flush()

        implement_step = AutoWorkflowStep(
            workflow_id=workflow.id,
            position=0,
            name="Implement mini-game with Codex",
            action_type="send_to_project_cli",
            step_type="send_to_project_cli",
            instruction=(
                "Implement ticket #{{ticket_id}} in the Arcade Prototype project. "
                "Create index.html for a polished Memory Sprint card game with score and restart controls. "
                "Return changed files and validation notes."
            ),
            linked_project_id=project.id,
            validation_type="none",
            timeout_seconds=30,
            status="pending",
        )
        validate_step = AutoWorkflowStep(
            workflow_id=workflow.id,
            position=1,
            name="Validate generated game artifact",
            action_type="run_command",
            step_type="run_command",
            instruction="Check that the generated game file exists and includes expected UI copy.",
            config=json.dumps({
                "command": (
                    "test -f index.html && "
                    "grep -q 'Memory Sprint' index.html && "
                    "grep -q 'restart' index.html"
                ),
                "working_directory": str(project_dir),
                "timeout_seconds": 10,
            }),
            validation_type="none",
            timeout_seconds=10,
            status="pending",
        )
        session.add_all([implement_step, validate_step])
        session.flush()
        implement_step.on_pass_goto = validate_step.id
        implement_step.on_fail_goto = -1
        validate_step.on_pass_goto = -1
        validate_step.on_fail_goto = -1

        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            board_id=board.id,
            ticket_id=ticket.id,
            status="running",
            current_step_id=implement_step.id,
            run_data=json.dumps({
                "project_id": project.id,
                "ticket_id": ticket.id,
                "result_packet": {
                    "summary": "Ticket asks Codex to build a practical mini-game artifact.",
                    "artifacts": {"files_changed": []},
                    "audit": {"final_verdict": "cannot_determine"},
                },
            }),
        )
        session.add(run)
        session.flush()

        return {
            "project_id": project.id,
            "workflow_id": workflow.id,
            "board_id": board.id,
            "ticket_id": ticket.id,
            "implement_step_id": implement_step.id,
            "validate_step_id": validate_step.id,
            "run_id": run.id,
        }


def test_workflow_ticket_routes_to_codex_backend_and_advances_to_validation(tmp_path):
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
    from distr.core.workflow.dispatcher import StepDispatcher

    factory = _make_factory()
    ids = _seed_codex_game_ticket(factory, tmp_path)

    def get_session():
        return _session_ctx(factory)

    captured = {}

    async def fake_run_project_task(
        project,
        instruction,
        *,
        chat_id=None,
        audit_id=None,
        run_id=None,
        workflow_id=None,
        step_id=None,
        on_event=None,
        origin="cli",
        ticket_id=None,
        ticket_complexity="medium",
        backend_id_override=None,
        model_override=None,
    ):
        captured["project_id"] = project.id
        captured["backend"] = project.coding_backend
        captured["model"] = project.coding_backend_model
        captured["origin"] = origin
        captured["ticket_id"] = ticket_id
        captured["ticket_complexity"] = ticket_complexity
        captured["backend_id_override"] = backend_id_override
        captured["model_override"] = model_override
        captured["audit_id"] = audit_id
        captured["run_id"] = run_id
        captured["workflow_id"] = workflow_id
        captured["step_id"] = step_id
        captured["instruction"] = instruction
        Path(project.folder_location, "index.html").write_text(
            """<!doctype html>
<html>
<head><title>Memory Sprint</title></head>
<body>
  <main>
    <h1>Memory Sprint</h1>
    <p id="score">Score: 0</p>
    <button id="restart">restart</button>
    <section class="grid"><button>A</button><button>A</button></section>
  </main>
  <script>document.getElementById('restart').addEventListener('click', () => location.reload());</script>
</body>
</html>
""",
            encoding="utf-8",
        )
        return BackendTaskResult(
            success=True,
            backend_id="codex",
            engine="codex",
            output="Changed files: index.html\nValidation: Memory Sprint UI created.",
            session_id=audit_id,
        )

    no_op = MagicMock()
    patches = [
        patch("distr.core.db.get_session", get_session),
        patch("distr.core.workflow.dispatcher.get_session", get_session),
        patch("distr.core.workflow.step_executor.get_session", get_session),
        patch("distr.core.workflow.post_execution.get_session", get_session),
        patch("distr.core.workflow.router.get_session", get_session),
        patch("distr.core.workflow.dispatcher.increment_workflow_updated", no_op),
        patch("distr.core.workflow.dispatcher.increment_kanban_updated", no_op),
        patch("distr.core.workflow.post_execution.increment_workflow_updated", no_op),
        patch("distr.core.workflow.router.increment_workflow_updated", no_op),
        patch("distr.core.workflow.dispatcher.record_workflow_chat_event", no_op),
        patch("distr.core.workflow.router.record_workflow_chat_event", no_op),
        patch("distr.core.workflow.dispatcher.append_ticket_audit_entry", no_op),
        patch("distr.core.workflow.router.append_ticket_audit_entry", no_op),
        patch("distr.core.project_cli_backends.run_project_task", fake_run_project_task),
    ]

    with contextlib.ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)

        result = StepDispatcher().run_in_workflow(ids["implement_step_id"], ids["run_id"])

    assert result["success"] is True
    assert captured["project_id"] == ids["project_id"]
    assert captured["backend"] == "codex"
    assert captured["model"] == "gpt-5.3-codex-spark"
    assert captured["origin"] == "workflow"
    assert captured["audit_id"] == ids["implement_step_id"]
    assert "RESULT PACKET CONTEXT" in captured["instruction"]
    assert "Memory Sprint" in captured["instruction"]

    with get_session() as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).first()
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == ids["workflow_id"])
            .order_by(AutoWorkflowStep.position.asc())
            .all()
        )
        results = (
            session.query(AutoWorkflowStepResult)
            .filter(AutoWorkflowStepResult.run_id == ids["run_id"])
            .order_by(AutoWorkflowStepResult.created_at.asc())
            .all()
        )

        assert run.status == "completed"
        assert [step.status for step in steps] == ["passed", "passed"]
        assert len(results) == 2
        assert "Project CLI backend: codex" in results[0].agent_response
        assert "Changed files: index.html" in results[0].agent_response
        assert results[1].status == "passed"
