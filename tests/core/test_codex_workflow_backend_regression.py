"""Regression tests for ticket workflows that route implementation work to Codex."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.project_cli_backends.base import BackendTaskResult
from distr.core.project_cli_backends.harness import HarnessHandle, HarnessStatus


def _make_factory(*, expire_on_commit=True):
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
    return sessionmaker(bind=engine, expire_on_commit=expire_on_commit)


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
            config=json.dumps({
                "execution_route": {
                    "enabled": True,
                    "mode": "scoped",
                    "scoped_model_key": "codex:gpt-5.3-codex-spark",
                    "route_snapshot": {
                        "backend_id": "codex",
                        "model": "gpt-5.3-codex-spark",
                        "name": "Codex Spark",
                    },
                },
            }),
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
                "skip_human_checkpoints": True,
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


@pytest.mark.parametrize(("queued", "change", "shared_step"), [
    (False, None, False), (True, None, False), (True, "provider-preflight", False),
    (True, "worker-timing", False), (True, "worker-timing", True), (True, "backend-start", False),
    (True, "backend-superseded", False), (True, "backend-unchanged", False),
    (True, "backend-cancelled", False), (True, "backend-late-result", False),
    (True, "late-callback", False), (True, "during-validation", False), (True, "late-terminal", False),
    (True, "board-default-during-validation", False), (True, "backend-active", False),
    (True, "backend-active-superseded", False),
    (True, "ide-early", False),
])
def test_workflow_ticket_routes_to_codex_backend_and_advances_to_validation(tmp_path, queued, change, shared_step):
    from distr.core.db.kanban import KanbanBoard, KanbanTicket, ProjectExecutionEvent, ProjectExecutionSession
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
    from distr.core.project_cli_backends import registry
    from distr.core.project_cli_backends.timing import resolve_worker_timing
    from distr.core.workflow.dispatcher import (
        StepDispatcher,
        complete_run,
        _active_runs,
        _initializing_runs,
        _runs_lock,
    )

    # This test owns an isolated in-memory database whose IDs can overlap with
    # run contexts left by earlier modules in the same pytest process.
    with _runs_lock:
        _active_runs.clear()
        _initializing_runs.clear()

    factory = _make_factory(expire_on_commit=change != "ide-early")
    ids = _seed_codex_game_ticket(factory, tmp_path)
    if queued:
        with _session_ctx(factory) as session:
            run = session.get(AutoWorkflowRun, ids["run_id"])
            run.run_data = json.dumps({**json.loads(run.run_data), "source_type": "workflow_queue"})
    if change and change not in ("backend-unchanged", "backend-superseded", "backend-cancelled", "backend-active-superseded", "ide-early"):
        with _session_ctx(factory) as session:
            replacement = Project(name="Reassigned work")
            session.add(replacement)
            session.flush()
            replacement_id = replacement.id
            if change == "board-default-during-validation":
                session.get(KanbanTicket, ids["ticket_id"]).linked_project_id = None

    def get_session():
        return _session_ctx(factory)

    captured = {}

    def move_ticket():
        with get_session() as session:
            ticket = session.get(KanbanTicket, ids["ticket_id"])
            ticket.linked_project_id = replacement_id
            ticket.workflow_status = "cancelled"
            if shared_step:
                session.add(AutoWorkflowRun(
                    workflow_id=ids["workflow_id"], board_id=ids["board_id"],
                    current_step_id=ids["implement_step_id"], status="running",
                ))

    def preflight(*_, **__):
        if change == "provider-preflight":
            move_ticket()
        return None

    def worker_timing(**kwargs):
        if change == "worker-timing":
            move_ticket()
        return resolve_worker_timing(**kwargs)

    def write_artifact(folder):
        Path(folder, "index.html").write_text(
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

    async def fake_dispatch_harness(context):
        project = context.project
        captured["project_id"] = project.id
        captured["backend"] = context.backend_id
        captured["model"] = context.model
        captured["origin"] = context.origin
        captured["audit_id"] = context.step_id
        captured["instruction"] = context.instruction
        write_artifact(project.folder_location)
        if change == "ide-early":
            with get_session() as session:
                run = session.get(AutoWorkflowRun, ids["run_id"])
                attempt = ProjectExecutionSession(project_id=project.id, run_id=run.id, workflow_id=run.workflow_id,
                    step_id=context.step_id, ticket_id=run.ticket_id, route_type="ide_bridge", status="completed")
                session.add(attempt)
                session.flush()
                attempt_id = attempt.id
                run.run_data = json.dumps({**json.loads(run.run_data), "ide_handoff_pending": True,
                    "execution_session_id": attempt_id, "early_ide_completion": {
                        "execution_session_id": attempt_id, "step_id": context.step_id,
                        "output": "Project CLI backend: codex\nChanged files: index.html\nStatus: completed",
                    }})
            return HarnessHandle(backend_id="codex", execution_session_id=attempt_id,
                status=HarnessStatus.WAITING_HUMAN, result=BackendTaskResult(
                    True, "codex", "codex", output="IDE opened", waits_for_human=True,
                ))
        backend_result = BackendTaskResult(
            success=True,
            backend_id="codex",
            engine="codex",
            output="Changed files: index.html\nValidation: Memory Sprint UI created.",
            session_id=context.step_id,
        )
        return HarnessHandle(
            backend_id=context.backend_id,
            result=backend_result,
            status=HarnessStatus.DONE,
            evidence={"output": backend_result.output, "engine": backend_result.engine},
        )

    no_op = MagicMock()
    provision = MagicMock()
    chat_event = MagicMock()
    patches = [
        patch("distr.core.mempalace.hub.call_tool", return_value={}),
        patch("distr.core.db.get_session", get_session),
        patch("distr.core.workflow.dispatcher.get_session", get_session),
        patch("distr.core.workflow.step_executor.get_session", get_session),
        patch("distr.core.workflow.post_execution.get_session", get_session),
        patch("distr.core.workflow.router.get_session", get_session),
        patch("distr.core.workflow.run_briefing.get_session", get_session),
        patch("distr.core.workflow.dispatcher.increment_workflow_updated", no_op),
        patch("distr.core.workflow.dispatcher.increment_kanban_updated", no_op),
        patch("distr.core.workflow.post_execution.increment_workflow_updated", no_op),
        patch("distr.core.workflow.router.increment_workflow_updated", no_op),
        patch("distr.core.workflow.dispatcher.record_workflow_chat_event", chat_event),
        patch("distr.core.workflow.router.record_workflow_chat_event", no_op),
        patch("distr.core.workflow.dispatcher.append_ticket_audit_entry", no_op),
        patch("distr.core.workflow.router.append_ticket_audit_entry", no_op),
        patch(
            "distr.core.project_cli_backends.registry._first_executable",
            return_value="/usr/bin/codex",
        ),
        patch("distr.core.project_cli_backends.provider_preflight.preflight_provider_route", preflight),
        patch("distr.core.project_cli_backends.timing.resolve_worker_timing", worker_timing),
        patch("distr.core.workflow.skill_provision.provision_workflow_skills", provision),
        patch("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress", no_op),
        patch("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_step_started", no_op),
    ]
    spawn = AsyncMock(side_effect=RuntimeError("Simulated process boundary"))
    handoffs = MagicMock(return_value=111)
    affected_process, other_process = MagicMock(), MagicMock()
    other_process.returncode = None
    live_status = MagicMock()
    if change and change.startswith("backend-"):
        def prepare_env(*_):
            if change == "backend-start":
                move_ticket()
            elif change == "backend-superseded":
                with get_session() as session:
                    run = session.get(AutoWorkflowRun, ids["run_id"])
                    run.current_step_id = ids["validate_step_id"]
            elif change == "backend-cancelled":
                with get_session() as session:
                    run = session.get(AutoWorkflowRun, ids["run_id"])
                    run.status = "cancelled"
                    session.query(ProjectExecutionSession).filter_by(run_id=run.id).one().status = "cancelled"
            return {}

        async def simulated_worker(*_, cwd, **__):
            write_artifact(cwd)
            report = json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Changed files: index.html\nStatus: completed"},
            })
            async def finish():
                if change == "backend-late-result":
                    move_ticket()
                return 0

            return MagicMock(
                stdout=MagicMock(read=AsyncMock(side_effect=[report.encode(), b""])),
                wait=finish,
            )

        if change in ("backend-unchanged", "backend-late-result"):
            spawn.side_effect = simulated_worker
        if change in ("backend-active", "backend-active-superseded"):
            async def quiet_read(*_):
                if change == "backend-active":
                    move_ticket()
                else:
                    with get_session() as session:
                        run = session.get(AutoWorkflowRun, ids["run_id"])
                        run.current_step_id = ids["validate_step_id"]
                    registry._register_oneshot_process(
                        ids["project_id"], "codex", other_process, board_id=ids["board_id"],
                        execution_id=ids["run_id"], execution_kind="workflow",
                    )
                await asyncio.sleep(0.2)
                return b""

            affected_process.stdout.read = AsyncMock(side_effect=quiet_read)
            affected_process.wait = AsyncMock(return_value=0)
            spawn.side_effect = None
            spawn.return_value = affected_process
            patches.append(patch.object(registry, "_QUEUED_WORKER_SCOPE_INTERVAL", 0.01, create=True))
            patches.append(patch.object(registry, "_ONE_SHOT_PROCESSES", {
                registry._oneshot_key(ids["project_id"], "codex", ids["board_id"], 999, "workflow"): other_process,
            }))
            patches.append(patch("distr.core.project_cli_backends.live_sessions.set_live_session_running", live_status))
        patches.extend([
            patch("distr.core.project_cli_backends.registry.CodexBackend._task_subprocess_env", prepare_env),
            patch("distr.core.project_cli_backends.registry.asyncio.create_subprocess_exec", spawn),
            patch("distr.core.kanban.project_execution.get_session", get_session),
            patch("distr.core.kanban.project_execution.ensure_project_execution_tables", no_op),
            patch("distr.core.orchestrator.record_backend_handoff", handoffs),
        ])
    else:
        patches.append(patch("distr.core.project_cli_backends.harness.dispatch_harness", fake_dispatch_harness))

    moved_during_validation = change in ("during-validation", "board-default-during-validation")
    stale_result = moved_during_validation or change in ("backend-late-result", "late-callback", "late-terminal", "backend-active")
    finalize = MagicMock()
    if stale_result:
        patches.append(patch("distr.core.workflow.dispatcher._finalize_terminal_run", finalize))
    if moved_during_validation:
        def verify(*_, **__):
            if change == "board-default-during-validation":
                with get_session() as session:
                    session.get(KanbanBoard, ids["board_id"]).default_project_id = replacement_id
            else:
                move_ticket()
            return True

        patches.append(patch("distr.core.workflow.router._run_verification", verify))

    with contextlib.ExitStack() as stack:
        for patcher in patches:
            stack.enter_context(patcher)

        worker = StepDispatcher()
        if change == "late-callback":
            move_ticket()
            worker._record_result_and_route(ids["implement_step_id"], ids["run_id"], "Status: completed", True)
        elif change == "late-terminal":
            with get_session() as session:
                pending = session.get(AutoWorkflowRun, ids["run_id"])
                pending.status = "completed"
                pending.run_data = json.dumps({**json.loads(pending.run_data), "queue_terminal_pending": "completed"})
            move_ticket()
            assert complete_run(ids["run_id"], "completed") is False
        else:
            result = worker.run_in_workflow(ids["implement_step_id"], ids["run_id"])

    if change in ("backend-active", "backend-active-superseded"):
        spawn.assert_awaited_once()
        affected_process.terminate.assert_called_once()
        other_process.terminate.assert_not_called()
        assert live_status.call_args_list and all(call.args[2] is True for call in live_status.call_args_list)
    if change == "backend-active-superseded":
        assert result == {"success": False, "status": "running", "ignored": True}
        with get_session() as session:
            run = session.get(AutoWorkflowRun, ids["run_id"])
            assert (run.status, run.current_step_id) == ("running", ids["validate_step_id"])
        return
    if stale_result:
        finalize.assert_not_called()
        if change == "backend-active":
            assert result.get("status") == "failed", result
        if moved_during_validation:
            assert result.get("status") == "failed" and "result was withheld" in result["error"], result
            assert not any(call.args[1] == "step_completed" for call in chat_event.call_args_list)
        if change == "backend-late-result":
            spawn.assert_awaited_once()
            assert result.get("status") == "failed", result
        with get_session() as session:
            run = session.get(AutoWorkflowRun, ids["run_id"])
            ticket = session.get(KanbanTicket, ids["ticket_id"])
            assert run.status == "failed"
            warning = "stopped while its worker was active" if change == "backend-active" else "result was withheld"
            assert warning in json.loads(run.run_data)["terminal_warning"]
            if change == "board-default-during-validation":
                assert ticket.linked_project_id is None and ticket.lane.board.default_project_id == replacement_id
            else:
                assert (ticket.linked_project_id, ticket.workflow_status) == (replacement_id, "cancelled")
            assert not session.query(AutoWorkflowStepResult).filter_by(run_id=run.id).count()
            if change == "backend-late-result":
                attempt = session.query(ProjectExecutionSession).filter_by(run_id=run.id).one()
                assert attempt.status == "failed"
                assert "Changed files: index.html" not in attempt.output_packet
                assert [call.kwargs["status"] for call in handoffs.call_args_list] == ["prepared", "failed"]
            if change == "backend-active":
                attempt = session.query(ProjectExecutionSession).filter_by(run_id=run.id).one()
                assert attempt.status == "failed"
                events = session.query(ProjectExecutionEvent).filter_by(session_id=attempt.id).all()
                assert any(event.event_type == "active_scope_changed" for event in events)
                assert all(event.event_type != "agent_end" for event in events)
        return

    if change and change not in ("backend-unchanged", "ide-early"):
        spawn.assert_not_called()
        assert not captured
        if change == "backend-cancelled":
            assert result == {"success": False, "status": "cancelled", "ignored": True}
            with get_session() as session:
                run = session.get(AutoWorkflowRun, ids["run_id"])
                assert run.status == "cancelled"
                assert session.query(ProjectExecutionSession).filter_by(run_id=run.id).one().status == "cancelled"
            return
        if change == "backend-superseded":
            assert result == {"success": False, "status": "running", "ignored": True}
            with get_session() as session:
                run = session.get(AutoWorkflowRun, ids["run_id"])
                assert (run.status, run.current_step_id) == ("running", ids["validate_step_id"])
                attempt = session.query(ProjectExecutionSession).filter_by(run_id=run.id).one()
                assert attempt.status == "failed"
                assert json.loads(attempt.output_packet)["queue_step_superseded"] is True
            return
        if change == "provider-preflight":
            provision.assert_not_called()
        assert result.get("status") == "failed", result
        assert "project assignment changed" in result["error"]
        assert not (tmp_path / "index.html").exists()
        with get_session() as session:
            run = session.get(AutoWorkflowRun, ids["run_id"])
            ticket = session.get(KanbanTicket, ids["ticket_id"])
            step = session.get(AutoWorkflowStep, ids["implement_step_id"])
            assert run.status == "failed" and json.loads(run.run_data)["queue_scope_changed"] is True
            assert (ticket.linked_project_id, ticket.workflow_status) == (replacement_id, "cancelled")
            assert step.status == ("running" if shared_step else "pending")
            if change == "backend-start":
                attempt = session.query(ProjectExecutionSession).filter_by(run_id=run.id).one()
                assert attempt.status == "failed"
                assert json.loads(attempt.output_packet)["queue_scope_changed"] is True
                assert "project assignment changed" in attempt.error
                assert [call.kwargs["status"] for call in handoffs.call_args_list] == ["prepared", "failed"]
        return

    assert result.get("success") is True, result
    if change == "backend-unchanged":
        spawn.assert_awaited_once()
        assert [call.kwargs["status"] for call in handoffs.call_args_list] == ["prepared", "completed"]
    else:
        assert captured["project_id"] == ids["project_id"]
        assert captured["backend"] == "codex"
        assert captured["model"] == "gpt-5.3-codex-spark"
        assert captured["origin"] == "workflow"
        assert captured["audit_id"] == ids["implement_step_id"]
        assert "RESULT PACKET CONTEXT" in captured["instruction"]
        assert "Memory Sprint" in captured["instruction"]

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with get_session() as session:
            current = session.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.id == ids["run_id"]
            ).first()
            if current and current.status == "completed":
                break
        time.sleep(0.02)

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

        diagnostic = {
            "run_status": run.status,
            "step_statuses": [step.status for step in steps],
            "results": [
                {
                    "step_id": item.step_id,
                    "status": item.status,
                    "response": item.agent_response,
                }
                for item in results
            ],
        }
        assert run.status == "completed", diagnostic
        assert [step.status for step in steps] == ["passed", "passed"]
        assert len(results) == 2
        assert "Project CLI backend: codex" in results[0].agent_response
        assert "Changed files: index.html" in results[0].agent_response
        assert results[1].status == "passed"
