from contextlib import contextmanager
from datetime import datetime
import json
from unittest.mock import Mock

import pytest
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
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
from distr.core.db.workflow import AutoWorkflowRun
from distr.core.workflow.dispatcher import (
    StepDispatcher,
    _cleanup_orphaned_runs_on_startup,
    _enter_controlled_restart_recovery,
    continue_waiting_step,
)
from distr.core.workflow.step_executor import _is_interpreter_shutdown_error


def test_executor_shutdown_error_is_only_suppressed_during_interpreter_teardown(monkeypatch):
    error = RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr("distr.core.workflow.step_executor.sys.is_finalizing", lambda: False)
    assert _is_interpreter_shutdown_error(error) is False

    monkeypatch.setattr("distr.core.workflow.step_executor.sys.is_finalizing", lambda: True)
    assert _is_interpreter_shutdown_error(error) is True


@pytest.mark.parametrize(("checkpoint", "moved"), [
    ("buffered", False), ("buffered", True), ("terminal", False), ("terminal", True),
    ("failure", False), ("damaged", False), ("unconfirmed", False),
])
def test_restart_recovers_saved_queued_outcome_without_relaunching(monkeypatch, checkpoint, moved):
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflowStepResult
    from distr.core.workflow import dispatcher

    factory = _factory()
    with factory.begin() as db:
        project, replacement = Project(name="Original"), Project(name="Replacement")
        workflow = AutoWorkflow(name="IDE restart recovery")
        db.add_all([project, replacement, workflow])
        db.flush()
        board = KanbanBoard(name="Board", default_project_id=project.id)
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="In Progress")
        step = AutoWorkflowStep(workflow_id=workflow.id, name="IDE work", status="running", on_pass_goto=-1)
        db.add_all([lane, step])
        db.flush()
        ticket = KanbanTicket(lane_id=lane.id, title="Queued ticket", linked_workflow_id=workflow.id,
                             linked_project_id=replacement.id if moved else project.id,
                             workflow_status="running")
        db.add(ticket)
        db.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, board_id=board.id, ticket_id=ticket.id,
            current_step_id=step.id, status={"terminal": "completed", "failure": "waiting"}.get(checkpoint, "running"))
        db.add(run)
        db.flush()
        attempt = ProjectExecutionSession(project_id=project.id, workflow_id=workflow.id,
            run_id=run.id, step_id=step.id, ticket_id=ticket.id, route_type="ide_bridge",
            status="failed" if checkpoint == "failure" else "completed")
        db.add(attempt)
        db.flush()
        data = {"source_type": "workflow_queue", "project_id": project.id, "execution_session_id": attempt.id}
        if checkpoint == "terminal":
            data["queue_terminal_pending"] = "completed"
        else:
            data["early_ide_completion"] = {
                "execution_session_id": attempt.id + (checkpoint == "damaged"), "step_id": step.id,
                "output": "Verified editor result.", "passed": checkpoint != "failure",
            }
            if checkpoint == "failure":
                data["waiting_kind"] = "ide_handoff"
                step.on_fail_goto = -1
        run.run_data = json.dumps(data)

    def get_session():
        return _real_session(factory)

    for module in ("dispatcher", "router", "post_execution"):
        monkeypatch.setattr(f"distr.core.workflow.{module}.get_session", get_session)
    monkeypatch.setattr(dispatcher, "increment_workflow_updated", lambda: None)
    monkeypatch.setattr("distr.core.mempalace.hub.call_tool", lambda *_, **__: {})
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_step_finished",
                        lambda **_: None)
    finalized, relaunched = Mock(), Mock(return_value={"success": True})
    monkeypatch.setattr(dispatcher, "_finalize_terminal_run", finalized)
    monkeypatch.setattr(StepDispatcher, "run_in_workflow", relaunched)
    for _ in range(2):
        _cleanup_orphaned_runs_on_startup()
        dispatcher._auto_resume_interrupted_runs(
            background=False,
            unconfirmed_checkpoint_steps={(run.id, step.id)} if checkpoint == "unconfirmed" else None,
        )

    relaunched.assert_not_called()
    with get_session() as db:
        saved = db.get(AutoWorkflowRun, run.id)
        results = db.query(AutoWorkflowStepResult).filter_by(run_id=run.id).all()
        metadata = json.loads(saved.run_data)
        if checkpoint in {"damaged", "unconfirmed"}:
            assert saved.status == "waiting"
            assert metadata["early_ide_completion"] == data["early_ide_completion"]
            assert not results
            finalized.assert_not_called()
            return
        status = "failed" if moved or checkpoint == "failure" else "completed"
        assert saved.status == status
        expected_result = [("Verified editor result.", "failed" if checkpoint == "failure" else "passed")]
        assert [(row.agent_response, row.status) for row in results] == (
            expected_result if checkpoint in {"buffered", "failure"} and not moved else []
        )
        if moved:
            finalized.assert_not_called()
            reassigned = db.get(KanbanTicket, ticket.id)
            assert reassigned.linked_project_id == replacement.id
            assert reassigned.workflow_status == "running"
        else:
            assert "queue_terminal_pending" not in metadata
            assert "early_ide_completion" not in metadata
            finalized.assert_called_once_with(run.id, workflow.id, status)


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


@pytest.fixture
def dispatch_run(monkeypatch):
    factory = _factory()
    monkeypatch.setattr("distr.core.workflow.dispatcher.get_session", lambda: _real_session(factory))

    def seed(status):
        with _real_session(factory) as session:
            workflow = AutoWorkflow(name="Dispatch guard")
            session.add(workflow)
            session.flush()
            step = AutoWorkflowStep(workflow_id=workflow.id, name="Worker")
            session.add(step)
            session.flush()
            run = AutoWorkflowRun(workflow_id=workflow.id, current_step_id=step.id, status=status)
            session.add(run)
            session.flush()
            return workflow.id, step.id, run.id

    return seed


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

        assert recovered_run.status == "waiting"
        assert "No saved current step" in __import__("json").loads(recovered_run.run_data)["restart_recovery"]["blocked_reason"]
        assert recovered_run.completed_at is None
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
        assert recovered_ticket.workflow_status == "waiting"
        assert stale_terminal_ticket.workflow_status == "completed"


@pytest.mark.parametrize("worker_started", [False, True])
def test_startup_recovery_preserves_run_with_durable_current_step(monkeypatch, worker_started):
    factory = _factory()
    with _real_session(factory) as session:
        workflow = AutoWorkflow(name="Recoverable workflow")
        board = KanbanBoard(name="Recovery board")
        session.add_all([workflow, board])
        session.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            position=0,
            name="Implement saved work",
            instruction="Resume from durable context",
        )
        lane = KanbanLane(board_id=board.id, name="In Progress")
        session.add_all([step, lane])
        session.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Recover after restart",
            workflow_status="running",
        )
        session.add(ticket)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            ticket_id=ticket.id,
            current_step_id=step.id,
            status="running",
            run_data='{"project_id": 7}',
        )
        session.add(run)
        session.flush()
        execution = ProjectExecutionSession(
            project_id=7,
            workflow_id=workflow.id,
            run_id=run.id,
            step_id=step.id,
            status="running",
        )
        session.add(execution)
        session.flush()
        if worker_started:
            session.add(ProjectExecutionEvent(session_id=execution.id, event_type="executor_start"))
        ids = {"run": run.id, "ticket": ticket.id, "execution": execution.id}

    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.get_session",
        lambda: _real_session(factory),
    )
    _cleanup_orphaned_runs_on_startup()

    with _real_session(factory) as session:
        run = session.get(AutoWorkflowRun, ids["run"])
        ticket = session.get(KanbanTicket, ids["ticket"])
        execution = session.get(ProjectExecutionSession, ids["execution"])
        event = (
            session.query(ProjectExecutionEvent)
            .filter(ProjectExecutionEvent.session_id == ids["execution"])
            .filter(ProjectExecutionEvent.event_type == "restart_recovery_available")
            .one()
        )
        run_data = __import__("json").loads(run.run_data)

        assert run.status == "waiting"
        assert run.completed_at is None
        assert run_data["waiting_kind"] == "restart_recovery"
        assert run_data["restart_recovery"]["step_id"] == run.current_step_id
        assert ticket.workflow_status == "waiting"
        assert execution.status == "waiting"
        assert execution.completed_at is None
        assert event.event_type == "restart_recovery_available"

    dispatch = Mock(return_value={"success": True, "status": "started"})
    monkeypatch.setattr("distr.core.workflow.dispatcher._dispatch_workflow_step", dispatch)
    monkeypatch.setattr("distr.core.workflow.dispatcher.record_workflow_chat_event", lambda *_args, **_kwargs: None)

    resumed = continue_waiting_step(ids["run"], "continue")

    if worker_started:
        assert resumed["status_code"] == 409
        assert "Inspect its effects" in resumed["error"]
        dispatch.assert_not_called()
        with _real_session(factory) as session:
            assert session.get(AutoWorkflowRun, ids["run"]).status == "waiting"
        return

    assert resumed["success"] is True
    assert resumed["action"] == "retry_step"
    dispatch.assert_called_once()
    with _real_session(factory) as session:
        run = session.get(AutoWorkflowRun, ids["run"])
        execution = session.get(ProjectExecutionSession, ids["execution"])
        assert run.status == "running"
        assert __import__("json").loads(run.run_data)["restart_recovery"]["resumed"] is True
        assert execution.status == "cancelled"
        assert execution.completed_at is not None
        assert execution.error == "Superseded by the resumed restart-recovery attempt."


def test_controlled_restart_fault_targets_only_explicit_qualification_run(monkeypatch):
    factory = _factory()
    with _real_session(factory) as session:
        workflow = AutoWorkflow(name="Qualification restart")
        board = KanbanBoard(name="Qualification board")
        session.add_all([workflow, board])
        session.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            position=0,
            name="Saved step",
            instruction="Run the named check",
        )
        lane = KanbanLane(board_id=board.id, name="In Progress")
        session.add_all([step, lane])
        session.flush()
        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Restart qualification",
            workflow_status="running",
        )
        session.add(ticket)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            ticket_id=ticket.id,
            current_step_id=step.id,
            status="running",
            run_data=(
                '{"project_id":7,"qualification_scenario_id":"restart_recovery",'
                '"qualification_injected_failure":"process_restart"}'
            ),
        )
        session.add(run)
        session.flush()
        ids = {"run": run.id, "step": step.id, "ticket": ticket.id}

    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.get_session",
        lambda: _real_session(factory),
    )
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.record_workflow_chat_event",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setenv("DECISIONS_ENABLE_QUALIFICATION_FAULTS", "1")

    assert _enter_controlled_restart_recovery(ids["run"], ids["step"]) is True
    assert _enter_controlled_restart_recovery(ids["run"], ids["step"]) is False

    with _real_session(factory) as session:
        run = session.get(AutoWorkflowRun, ids["run"])
        ticket = session.get(KanbanTicket, ids["ticket"])
        data = __import__("json").loads(run.run_data)
        assert run.status == "waiting"
        assert ticket.workflow_status == "waiting"
        assert data["waiting_kind"] == "restart_recovery"
        assert data["qualification_failure_observed"] is True
        assert data["qualification_faults_consumed"] == [
            f"process_restart:active_step:{ids['step']}"
        ]


def test_unexpected_backend_exception_becomes_failed_step_result(monkeypatch, dispatch_run):
    workflow_id, step_id, run_id = dispatch_run("running")
    monkeypatch.setattr("distr.core.workflow.dispatcher.build_step_preflight", lambda *_args, **_kwargs: {"ok": True, "summary": "ready"})
    monkeypatch.setattr("distr.core.workflow.dispatcher.record_workflow_chat_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("distr.core.workflow.dispatcher.emit_step_activity", lambda *_args, **_kwargs: None)

    dispatcher = StepDispatcher()
    dispatcher._load_step = Mock(return_value={"id": step_id, "name": "Worker", "workflow_id": workflow_id, "position": 0})
    dispatcher._set_run_phase = Mock()
    dispatcher._validate_before_dispatch = Mock(return_value=[])
    dispatcher._set_status = Mock()
    dispatcher._execute = Mock(side_effect=RuntimeError("adapter exploded"))
    dispatcher._record_result_and_route = Mock()

    result = dispatcher.run_in_workflow(step_id, run_id)

    assert result["status"] == "failed"
    assert "adapter exploded" in result["error"]
    dispatcher._record_result_and_route.assert_called_once_with(
        step_id,
        run_id=run_id,
        result_text="Step execution failed unexpectedly: adapter exploded",
        passed=False,
        skip_wait=True,
    )


def test_cancelled_run_cannot_dispatch_a_late_provider_step(dispatch_run):
    _, step_id, run_id = dispatch_run("cancelled")
    dispatcher = StepDispatcher()
    dispatcher._execute = Mock()

    result = dispatcher.run_in_workflow(step_id, run_id)

    assert result["cancelled"] is True
    assert result["status"] == "cancelled"
    dispatcher._execute.assert_not_called()


@pytest.mark.parametrize("output", ["Finished", "Status: needs_input\nSummary: Waiting\nBlockers: Missing detail"])
def test_late_workflow_callback_leaves_stopped_run_and_newer_step_untouched(monkeypatch, output):
    from distr.core.db.workflow import AutoWorkflowStepResult
    from distr.core.workflow.router import StepRouter

    factory = _factory()
    provider = lambda: _real_session(factory)
    for module in ("post_execution", "router"):
        monkeypatch.setattr(f"distr.core.workflow.{module}.get_session", provider)
    with provider() as db:
        workflow = AutoWorkflow(name="Shared definition")
        db.add(workflow)
        db.flush()
        step = AutoWorkflowStep(workflow_id=workflow.id, name="Worker", status="running", result="Newer work")
        db.add(step)
        db.flush()
        stopped = AutoWorkflowRun(workflow_id=workflow.id, current_step_id=step.id, status="failed",
                                  run_data='{"approval_scope_changed": true}')
        newer = AutoWorkflowRun(workflow_id=workflow.id, current_step_id=step.id, status="running")
        db.add_all([stopped, newer])
        db.flush()
        step_id, stopped_id, newer_id = step.id, stopped.id, newer.id
    dispatcher = StepDispatcher()
    dispatcher._persist_compact_step_memory = Mock()
    dispatcher._append_workflow_step_audit = Mock()
    verify = Mock(return_value=True)
    monkeypatch.setattr("distr.core.workflow.router._run_verification", verify)
    dispatcher._record_result_and_route(step_id, stopped_id, output, True)
    assert StepRouter().route(step_id, output, True, stopped_id)["action"] == "ignore"
    dispatcher._persist_compact_step_memory.assert_not_called()
    dispatcher._append_workflow_step_audit.assert_not_called()
    verify.assert_not_called()
    with provider() as db:
        assert db.get(AutoWorkflowRun, stopped_id).status == "failed"
        assert db.get(AutoWorkflowRun, newer_id).status == "running"
        step = db.get(AutoWorkflowStep, step_id)
        assert (step.status, step.result) == ("running", "Newer work")
        assert db.query(AutoWorkflowStepResult).count() == 0


def test_late_provider_events_preserve_the_cancellation_receipt(monkeypatch):
    from distr.core.kanban import project_execution
    from distr.core.workflow.dispatcher import _cancel_linked_execution_sessions

    factory = _factory()
    provider = lambda: _real_session(factory)
    monkeypatch.setattr(project_execution, "get_session", provider)
    monkeypatch.setattr(project_execution, "ensure_project_execution_tables", lambda: None)
    notify = Mock()
    monkeypatch.setattr("distr.core.orchestration_events.emit_orchestration_event", notify)
    with provider() as db:
        old = ProjectExecutionSession(project_id=1, run_id=7, status="waiting", route_backend="pi")
        current = ProjectExecutionSession(project_id=2, run_id=8, status="running", route_backend="pi")
        db.add_all([old, current])
        db.flush()
        old_id, current_id = old.id, current.id
        _cancel_linked_execution_sessions(db, [7], reason="Ownership changed", event_type="approval_scope_changed")
    project_execution.append_execution_event(old_id, "message_update", status="running", message="Late progress")
    project_execution.complete_execution_session(old_id, success=True, output_packet={"summary": "Late success"})
    with provider() as db:
        old = db.get(ProjectExecutionSession, old_id)
        assert (old.status, old.error, old.output_packet) == ("cancelled", "Ownership changed", None)
        assert db.get(ProjectExecutionSession, current_id).status == "running"
        assert [event.event_type for event in old.events] == ["approval_scope_changed"]
    notify.assert_not_called()


def test_verification_cannot_publish_after_run_stops(monkeypatch, dispatch_run):
    from distr.core.db.workflow import AutoWorkflowStepResult
    from distr.core.workflow import dispatcher, router

    _, step_id, run_id = dispatch_run("running")
    monkeypatch.setattr(router, "get_session", dispatcher.get_session)

    def verify(*args, **kwargs):
        with dispatcher.get_session() as db:
            run = db.get(AutoWorkflowRun, run_id)
            run.status = "failed"
            run.run_data = '{"approval_scope_changed": true}'
        return True

    monkeypatch.setattr(router, "_run_verification", verify)
    monkeypatch.setattr(router, "_ticket_acceptance_gate", lambda *args, **kwargs: True)
    assert router.StepRouter().route(step_id, "Late verification", True, run_id)["action"] == "ignore"
    with dispatcher.get_session() as db:
        assert db.get(AutoWorkflowRun, run_id).status == "failed"
        assert db.query(AutoWorkflowStepResult).count() == 0


@pytest.mark.parametrize("raises", [False, True])
def test_synchronous_worker_return_after_stop_does_not_publish_completion(monkeypatch, dispatch_run, raises):
    from distr.core.workflow import dispatcher

    workflow_id, step_id, run_id = dispatch_run("running")
    trace = Mock()
    monkeypatch.setattr(dispatcher, "record_workflow_chat_event", trace)
    monkeypatch.setattr(dispatcher, "emit_step_activity", Mock())
    monkeypatch.setattr(dispatcher, "build_step_preflight", lambda *args: {"ok": True})
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_step_started", Mock())
    worker = StepDispatcher()
    worker._load_step = Mock(return_value={"id": step_id, "workflow_id": workflow_id, "name": "Worker", "position": 0})
    worker._validate_before_dispatch = Mock(return_value=[])
    worker._record_result_and_route = Mock()

    def execute(*args, **kwargs):
        with dispatcher.get_session() as db:
            db.get(AutoWorkflowRun, run_id).status = "failed"
        if raises:
            raise RuntimeError("Backend stopped")
        return {"output": "Late output", "passed": True}

    worker._execute = execute
    assert worker.run_in_workflow(step_id, run_id)["ignored"]
    assert [call.args[1] for call in trace.call_args_list] == ["step_started"]
    worker._record_result_and_route.assert_not_called()
