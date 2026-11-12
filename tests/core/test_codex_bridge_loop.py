"""Codex/IDE bridge events should become Hermes learning signal."""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock

import pytest
import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
import distr.core.ide_bridge  # noqa: F401
import distr.core.kanban.project_execution  # noqa: F401
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorLearnedRule
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowVariable
from distr.gui.web.routes.settings.workflows import register_routes


def _make_factory():
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


def _make_client():
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_continue_route_queues_worker_without_waiting_for_dispatch(monkeypatch):
    factory = _make_factory()
    with _session_ctx(factory) as session:
        workflow = AutoWorkflow(name="Queued continuation", status="active")
        session.add(workflow)
        session.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, status="waiting")
        session.add(run)
        session.flush()
        workflow_id = workflow.id
        run_id = run.id

    def get_session():
        return _session_ctx(factory)

    continued = MagicMock(return_value={"success": True, "status": "running"})
    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.dispatcher.continue_waiting_step", continued)

    response = _make_client().post(
        f"/api/workflows/{workflow_id}/runs/{run_id}/continue",
        json={"input": "yes, go ahead"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "starting"
    continued.assert_called_once_with(run_id, "yes, go ahead")


@pytest.mark.parametrize("change", ["project", "session", "terminal", "cancelled-attempt", "damaged-context",
                                   "before-resume", "valid-resume", "early", "early-moved",
                                   "early-failed", "waiting-failed", "failed-fallback", None])
def test_queued_ide_completion_uses_the_current_ticket_and_attempt(monkeypatch, change):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, ProjectExecutionSession
    from distr.core.db.projects import Project
    from distr.core.workflow import dispatcher

    early = change in ("early", "early-moved", "early-failed")
    failed_report = change in ("early-failed", "waiting-failed", "failed-fallback")
    factory = _make_factory()
    with factory.begin() as db:
        project, replacement = Project(name="Original"), Project(name="Replacement")
        workflow = AutoWorkflow(name="Queued IDE")
        db.add_all([project, replacement, workflow])
        db.flush()
        board = KanbanBoard(name="Queue", default_project_id=project.id)
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name="In Progress")
        step = AutoWorkflowStep(workflow_id=workflow.id, name="IDE work",
                                status="running" if early else "waiting", on_pass_goto=-1, on_fail_goto=-1)
        db.add_all([lane, step])
        db.flush()
        if change == "failed-fallback":
            fallback = AutoWorkflowStep(workflow_id=workflow.id, name="Recover", position=1, status="pending")
            db.add(fallback)
            db.flush()
            step.on_fail_goto = fallback.id
        ticket = KanbanTicket(lane_id=lane.id, title="Queued work", linked_workflow_id=workflow.id,
                             linked_project_id=replacement.id if change == "project" else project.id)
        db.add(ticket)
        db.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, board_id=board.id, ticket_id=ticket.id,
                              current_step_id=step.id, status="running" if early else
                              "cancelled" if change == "terminal" else "waiting")
        db.add(run)
        db.flush()
        attempt = ProjectExecutionSession(project_id=project.id, workflow_id=workflow.id, run_id=run.id,
            step_id=step.id, ticket_id=ticket.id, route_type="ide_bridge", route_backend="cursor",
            status="cancelled" if change == "cancelled-attempt" else "waiting")
        db.add(attempt)
        db.flush()
        run.run_data = json.dumps({"source_type": "workflow_queue", "project_id": project.id,
            "execution_session_id": attempt.id, "skip_human_checkpoints": True,
            **({} if early else {"waiting_kind": "ide_handoff"})})
        if early:
            run.run_data = json.dumps({**json.loads(run.run_data),
                "latest_backend_handoff": {"execution_session_id": attempt.id, "state": "prepared"}})
        if change == "damaged-context":
            run.run_data = "{damaged"

    def get_session():
        return _session_ctx(factory)

    real_continue = dispatcher.continue_waiting_step

    def continue_run(run_id, text, **kwargs):
        with get_session() as db:
            assert db.get(AutoWorkflowRun, run_id).status == "waiting"
            assert db.get(AutoWorkflowStep, step.id).status == "waiting"
            if change == "before-resume":
                db.get(KanbanTicket, ticket.id).linked_project_id = replacement.id
        if change in ("before-resume", "valid-resume"):
            return real_continue(run_id, text, **kwargs)

    continued = MagicMock(side_effect=continue_run)
    event_log, feedback = MagicMock(), MagicMock()
    real_append = distr.core.kanban.project_execution.append_execution_event
    if early:
        event_log.side_effect = real_append
    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr(dispatcher, "continue_waiting_step", continued)
    monkeypatch.setattr(dispatcher, "get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.router.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.post_execution.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.run_briefing.get_session", get_session)
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_step_finished", lambda **_: None)
    finalized, dispatched = MagicMock(), MagicMock()
    monkeypatch.setattr(dispatcher, "_finalize_terminal_run", finalized)
    if change == "failed-fallback":
        monkeypatch.setattr(dispatcher.StepDispatcher, "run_in_workflow", dispatched)
    monkeypatch.setattr("distr.core.mempalace.hub.call_tool", lambda *_, **__: {})
    monkeypatch.setattr(dispatcher, "increment_workflow_updated", lambda: None)
    monkeypatch.setattr("distr.core.kanban.project_execution.append_execution_event", event_log)
    monkeypatch.setattr("distr.core.kanban.project_execution.get_session", get_session)
    monkeypatch.setattr("distr.core.kanban.project_execution.ensure_project_execution_tables", lambda: None)
    monkeypatch.setattr("distr.core.orchestration_events.emit_orchestration_event", lambda **_: None)
    monkeypatch.setattr("distr.core.workspace_memory.feedback_sync.persist_worker_feedback", feedback)
    payload = {"event_type": "cursor_completed", "status": "completed", "output": "Worker result",
        "step_id": step.id, "ticket_id": ticket.id, "project_id": project.id,
        "execution_session_id": attempt.id + 1 if change == "session" else attempt.id}
    if failed_report:
        verifier = MagicMock(return_value=True)
        monkeypatch.setattr("distr.core.workflow.router._run_verification", verifier)
        payload.update(event_type="cursor_failed", status="failed", output="Editor could not finish.")
    with _make_client() as client:
        url = f"/api/workflows/{workflow.id}/runs/{run.id}/codex-events"
        response = client.post(url, json=payload)
        if failed_report:
            from distr.core.db.workflow import AutoWorkflowStepResult

            assert response.status_code == 200, response.json()
            if early:
                assert response.json().get("buffered") is True
                with get_session() as db:
                    saved = db.get(AutoWorkflowRun, run.id)
                    saved.run_data = json.dumps({**json.loads(saved.run_data), "ide_handoff_pending": True})
                dispatcher.StepDispatcher()._record_result_and_route(step.id, run.id, "Opened Cursor.", True)
            client.post(url, json=payload)
            continued.assert_not_called()
            verifier.assert_not_called()
            with get_session() as db:
                saved = db.get(AutoWorkflowRun, run.id)
                if change == "failed-fallback":
                    assert saved.status == "running" and saved.current_step_id == fallback.id
                    dispatched.assert_called_once_with(fallback.id, run.id)
                    finalized.assert_not_called()
                else:
                    assert saved.status == "failed"
                    finalized.assert_called_once_with(run.id, workflow.id, "failed")
                    assert "queue_terminal_pending" not in json.loads(saved.run_data)
                results = db.query(AutoWorkflowStepResult).filter_by(run_id=run.id).all()
                assert [(row.status, row.agent_response) for row in results] == [("failed", "Editor could not finish.")]
            return
        if early:
            from distr.core.db.workflow import AutoWorkflowStepResult
            from distr.core.project_cli_backends.registry import _update_run_handoff_state

            assert response.status_code == 200 and response.json().get("buffered") is True, response.json()
            assert client.post(url, json=payload).json().get("duplicate") is True
            event_log.assert_called_once()
            assert event_log.call_args.args[1] == "ide_completion_buffered"
            feedback.assert_not_called()
            continued.assert_not_called()
            assert real_append(attempt.id, "ide_handoff_dispatched", status="running") is False
            assert real_append(attempt.id, "ide_handoff_waiting", status="waiting") is False
            _update_run_handoff_state(run_id=run.id, packet={"execution_session_id": attempt.id}, state="waiting_ide")
            monkeypatch.setattr("distr.core.workflow.post_execution.get_session", get_session)
            with get_session() as db:
                saved = db.get(AutoWorkflowRun, run.id)
                assert db.get(ProjectExecutionSession, attempt.id).status == "completed"
                assert json.loads(saved.run_data)["latest_backend_handoff"]["state"] == "result_received"
                saved.run_data = json.dumps({**json.loads(saved.run_data), "ide_handoff_pending": True})
                if change == "early-moved":
                    db.get(KanbanTicket, ticket.id).linked_project_id = replacement.id
            dispatcher.StepDispatcher()._record_result_and_route(step.id, run.id, "Opened Cursor.", True)
            with get_session() as db:
                saved = db.get(AutoWorkflowRun, run.id)
                results = db.query(AutoWorkflowStepResult).filter_by(run_id=run.id).all()
                if change == "early":
                    assert saved.status == "completed"
                    finalized.assert_called_once_with(run.id, workflow.id, "completed")
                    assert len(results) == 1 and results[0].agent_response == "Worker result"
                    assert "early_ide_completion" not in json.loads(saved.run_data)
                    assert json.loads(saved.run_data)["latest_backend_handoff"]["state"] == "completed"
                else:
                    assert saved.status == "failed" and not results
                    assert "Worker result" not in saved.run_data
            if change == "early":
                assert dispatcher.complete_run(run.id, "completed") is False
                finalized.assert_called_once()
            return
        if change in ("before-resume", "valid-resume"):
            assert response.status_code == 200, response.json()
            continued.assert_called_once()
            with get_session() as db:
                saved = db.get(AutoWorkflowRun, run.id)
                assert saved.status == ("failed" if change == "before-resume" else "completed")
                assert ("feedback" in json.loads(saved.run_data)) == (change == "valid-resume")
            return
        if not change:
            assert response.status_code == 200, response.json()
            assert client.post(url, json=payload).status_code == 200
            continued.assert_called_once()
            event_log.assert_called_once()
            return
    assert response.status_code == 409, response.json()
    continued.assert_not_called()
    event_log.assert_not_called()
    feedback.assert_not_called()
    with get_session() as db:
        current = db.get(AutoWorkflowRun, run.id)
        assert current.status == {"project": "failed", "session": "waiting", "terminal": "cancelled",
                                  "cancelled-attempt": "waiting", "damaged-context": "waiting"}[change]
        assert "Worker result" not in current.run_data
        if change == "damaged-context":
            assert current.run_data == "{damaged"
        assert db.get(ProjectExecutionSession, attempt.id).status == {
            "project": "failed", "cancelled-attempt": "cancelled",
        }.get(change, "waiting")


def test_codex_bridge_user_steer_is_recorded_and_captured_as_standard(monkeypatch):
    factory = _make_factory()

    with _session_ctx(factory) as session:
        workflow = AutoWorkflow(name="Codex Bridge Workflow", status="active")
        session.add(workflow)
        session.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Send to Codex",
            position=0,
            action_type="send_to_project_cli",
            status="running",
        )
        session.add(step)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="running",
            current_step_id=step.id,
            ticket_id=77,
            board_id=12,
            run_data=json.dumps({"project_id": 34, "execution_session_id": 56}),
        )
        session.add(run)
        session.flush()
        workflow_id = workflow.id
        run_id = run.id
        step_id = step.id

    def get_session():
        return _session_ctx(factory)

    append_execution_event = MagicMock()
    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.standards_memory.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.is_orchestrator_enabled", lambda: True)
    monkeypatch.setattr(
        "distr.core.kanban.project_execution.append_execution_event",
        append_execution_event,
    )
    monkeypatch.setattr(
        "distr.gui.web.routes.settings.workflows.increment_workflow_updated",
        MagicMock(),
        raising=False,
    )
    monkeypatch.setattr("distr.gui.web.workflow_events.increment_workflow_updated", MagicMock())

    client = _make_client()
    message = (
        "User correction: require browser validation evidence before marking "
        "frontend tickets complete."
    )
    response = client.post(
        f"/api/workflows/{workflow_id}/runs/{run_id}/codex-events",
        json={
            "event_type": "user_steer",
            "status": "observed",
            "message": message,
            "execution_session_id": 56,
            "step_id": step_id,
            "ticket_id": 77,
            "project_id": 34,
            "payload": {"source": "codex_plugin"},
            "evidence": {"note": "human corrected the run"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["event_type"] == "needs_input"
    assert body["legacy_event_type"] == "user_steer"
    assert body["captured_standard"] is False

    append_execution_event.assert_called_once()
    assert append_execution_event.call_args.args[:2] == (56, "user_steer")

    with _session_ctx(factory) as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        run_data = json.loads(run.run_data or "{}")
        assert run_data["last_codex_bridge_state"]["event_type"] == "user_steer"
        assert run_data["codex_bridge_events"][-1]["message"] == message
        assert run_data["live_agent_context"]["latest_user_steer"] == message
        assert run_data["live_agent_context"]["execution_session_id"] == 56
        assert run_data["live_agent_context"]["recent_events"][-1]["event_type"] == "user_steer"

        event = session.query(OrchestratorEvent).filter(OrchestratorEvent.event_type == "needs_input").one()
        assert event.source == "codex"
        assert event.status == "observed"
        assert event.workflow_id == workflow_id
        assert event.run_id == run_id
        assert event.step_id == step_id
        assert event.ticket_id == 77
        assert event.board_id == 12
        assert event.project_id == 34
        payload = json.loads(event.payload or "{}")
        assert payload["orchestration"]["legacy_event_type"] == "user_steer"

        assert (
            session.query(AutoWorkflowVariable)
            .filter(AutoWorkflowVariable.workflow_id == workflow_id)
            .filter(AutoWorkflowVariable.name == "Adaptive Quality Memory")
            .count()
        ) == 0
        candidate = session.query(OrchestratorLearnedRule).one()
        assert candidate.enabled == 0
        assert candidate.evidence_count == 1
        assert "browser validation evidence" in candidate.summary

    timeline = client.get(f"/api/workflows/{workflow_id}/runs/{run_id}/timeline")
    assert timeline.status_code == 200
    events = timeline.json()["events"]
    steer_event = next(
        event
        for event in events
        if event["event_type"] == "needs_input"
        and event["legacy_event_type"] == "user_steer"
    )
    assert "Hermes" not in steer_event["notification"]["text"]


def test_cursor_completed_auto_continues_waiting_ide_handoff(monkeypatch):
    factory = _make_factory()

    with _session_ctx(factory) as session:
        workflow = AutoWorkflow(name="Cursor IDE Handoff", status="active")
        session.add(workflow)
        session.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Send to Cursor",
            position=0,
            action_type="send_to_project_cli",
            status="running",
        )
        session.add(step)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="waiting",
            current_step_id=step.id,
            ticket_id=88,
            board_id=13,
            run_data=json.dumps({
                "project_id": 41,
                "execution_session_id": 27,
                "waiting_kind": "ide_handoff",
                "ide_handoff_pending": True,
            }),
        )
        session.add(run)
        session.flush()
        workflow_id = workflow.id
        run_id = run.id
        step_id = step.id

    def get_session():
        return _session_ctx(factory)

    continue_waiting_step = MagicMock(return_value={"success": True, "status": "running"})
    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.standards_memory.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.is_orchestrator_enabled", lambda: False)
    monkeypatch.setattr(
        "distr.gui.web.routes.settings.workflows.increment_workflow_updated",
        MagicMock(),
        raising=False,
    )
    monkeypatch.setattr("distr.gui.web.workflow_events.increment_workflow_updated", MagicMock())
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.continue_waiting_step",
        continue_waiting_step,
    )

    client = _make_client()
    response = client.post(
        f"/api/workflows/{workflow_id}/runs/{run_id}/codex-events",
        json={
            "event_type": "cursor_completed",
            "status": "completed",
            "message": "IDE work finished.",
            "output": "Updated files and ran tests.",
            "execution_session_id": 27,
            "step_id": step_id,
            "ticket_id": 88,
            "project_id": 41,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["auto_continue"] == {"success": True, "status": "starting"}
    continue_waiting_step.assert_called_once_with(run_id, "Updated files and ran tests.")
