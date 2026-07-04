# Feature: workflow-step-runner-unification, Task 9.3
# Tests for workflow_type validation (HTTP 422) and audit read-only enforcement (HTTP 403)
# Validates: Requirements 1.7, 7.4
"""
Unit tests verifying that the API layer returns HTTP 422 for invalid
workflow_type values and HTTP 403 when attempting to edit, run, or delete
audit workflows.
"""
import contextlib
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowRun,
    AutoWorkflowStepResult,
    AutoWorkflowVariable,
)
from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
from distr.core.db.orchestrator import OrchestratorCorrectionAttempt, OrchestratorEvent, OrchestratorLearnedRule, OrchestratorValidationRecord
from distr.gui.web.routes.settings.workflows import register_routes


@pytest.fixture
def db_setup():
    """Create an in-memory SQLite DB that works across threads (for TestClient)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Enable WAL-like behavior for SQLite
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def session_ctx():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return engine, factory, session_ctx


@pytest.fixture
def client(db_setup):
    engine, factory, session_ctx = db_setup
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    with patch("distr.core.workflow.service.get_session", session_ctx), \
        patch("distr.core.orchestrator.get_session", session_ctx), \
        patch("distr.core.db.get_session", session_ctx):
        yield TestClient(app), factory


class TestWorkflowTypeValidation422:
    """API returns HTTP 422 for invalid workflow_type on create/update."""

    def test_create_invalid_workflow_type(self, client):
        tc, _ = client
        resp = tc.post("/api/workflows", json={
            "name": "bad-type",
            "workflow_type": "bogus",
        })
        assert resp.status_code == 422

    def test_create_valid_workflow_type(self, client):
        tc, _ = client
        resp = tc.post("/api/workflows", json={
            "name": "good-type",
            "workflow_type": "manual",
        })
        assert resp.status_code == 200
        assert resp.json()["workflow_type"] == "manual"

    def test_create_default_type(self, client):
        tc, _ = client
        resp = tc.post("/api/workflows", json={"name": "default"})
        assert resp.status_code == 200
        assert resp.json()["workflow_type"] == "manual"

    def test_create_workflow_notifies_live_workflow_ui(self, client):
        tc, _ = client
        with patch("distr.gui.web.workflow_events.increment_workflow_updated") as inc:
            resp = tc.post("/api/workflows", json={"name": "live-create"})

        assert resp.status_code == 200
        inc.assert_called_once()

    def test_update_invalid_workflow_type(self, client):
        tc, _ = client
        create_resp = tc.post("/api/workflows", json={"name": "to-update"})
        assert create_resp.status_code == 200
        wf_id = create_resp.json()["id"]
        resp = tc.patch(f"/api/workflows/{wf_id}", json={"workflow_type": "invalid_value"})
        assert resp.status_code == 422

    def test_update_valid_workflow_type(self, client):
        tc, _ = client
        create_resp = tc.post("/api/workflows", json={"name": "to-update"})
        assert create_resp.status_code == 200
        wf_id = create_resp.json()["id"]
        resp = tc.patch(f"/api/workflows/{wf_id}", json={"workflow_type": "scheduled"})
        assert resp.status_code == 200

    def test_post_ui_feedback_label_records_harness_feedback(self, client):
        tc, _ = client
        with patch("distr.core.orchestrator.record_ui_feedback_label", return_value=123) as record:
            resp = tc.post(
                "/api/workflows/4/runs/9/ui-feedback",
                json={
                    "label": "spacing off",
                    "reason": "Too much vertical looseness.",
                    "ticket_id": 12,
                    "board_id": 7,
                    "project_id": 3,
                    "screenshot_paths": ["/tmp/after.png"],
                },
            )

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["event_id"] == 123
        record.assert_called_once()
        kwargs = record.call_args.kwargs
        assert kwargs["workflow_id"] == 4
        assert kwargs["run_id"] == 9
        assert kwargs["label"] == "spacing off"
        assert kwargs["board_id"] == 7

    def test_post_ui_feedback_can_accept_screenshot_as_visual_baseline(self, client, tmp_path):
        tc, _ = client
        after_path = tmp_path / "after.png"
        after_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        with patch("distr.core.orchestrator.record_ui_feedback_label", return_value=123), \
            patch("distr.core.orchestrator.upsert_visual_baseline_screens", return_value=77) as upsert_baseline, \
            patch("distr.core.orchestrator.get_visual_baseline_set", return_value={"id": 77, "name": "Gold Admin", "screens": []}):
            resp = tc.post(
                "/api/workflows/4/runs/9/ui-feedback",
                json={
                    "label": "approved",
                    "reason": "This dashboard has the right density.",
                    "board_id": 7,
                    "screenshot_paths": [str(after_path)],
                    "save_as_visual_baseline": True,
                    "visual_baseline_name": "Gold Admin",
                    "baseline_screen_name": "Dashboard",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["visual_baseline"]["id"] == 77
        upsert_baseline.assert_called_once()
        kwargs = upsert_baseline.call_args.kwargs
        assert kwargs["name"] == "Gold Admin"
        assert kwargs["board_id"] == 7
        assert kwargs["copy_screenshots"] is True
        assert kwargs["screens"][0]["screen_name"] == "Dashboard"
        assert kwargs["screens"][0]["screenshot_path"] == str(after_path)

    def test_post_ui_feedback_accept_baseline_creates_ready_reference(self, client, tmp_path, monkeypatch):
        tc, _ = client
        after_path = tmp_path / "after.png"
        after_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        storage_dir = tmp_path / "baseline-store"
        monkeypatch.setenv("ORCHESTRATOR_VISUAL_BASELINE_DIR", str(storage_dir))

        resp = tc.post(
            "/api/workflows/4/runs/9/ui-feedback",
            json={
                "label": "approved",
                "reason": "This dashboard has the right density.",
                "board_id": 7,
                "screenshot_paths": [str(after_path)],
                "save_as_visual_baseline": True,
                "visual_baseline_name": "Gold Admin",
                "baseline_screen_name": "Dashboard",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        screen = body["visual_baseline"]["screens"][0]
        assert screen["screenshot_path"].startswith(str(storage_dir))
        assert Path(screen["screenshot_path"]).exists()

        readiness = tc.get("/api/workflows/visual-baselines/readiness?board_id=7")
        assert readiness.status_code == 200
        assert readiness.json()["visual_baseline_readiness"]["ready"] is True

    def test_post_ui_feedback_accept_baseline_returns_immediate_readiness(self, client, tmp_path, monkeypatch):
        tc, _ = client
        after_path = tmp_path / "after.png"
        after_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        storage_dir = tmp_path / "baseline-store"
        monkeypatch.setenv("ORCHESTRATOR_VISUAL_BASELINE_DIR", str(storage_dir))

        resp = tc.post(
            "/api/workflows/4/runs/9/ui-feedback",
            json={
                "label": "approved",
                "reason": "This dashboard has the right density.",
                "board_id": 7,
                "screenshot_paths": [str(after_path)],
                "save_as_visual_baseline": True,
                "visual_baseline_name": "Gold Admin",
                "baseline_screen_name": "Dashboard",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["visual_baseline"]["name"] == "Gold Admin"
        assert body["visual_baseline_readiness"]["ready"] is True
        assert body["visual_baseline_readiness"]["baseline_count"] == 1
        assert body["next_action"] == "Visual baseline is ready for UI validation."

    def test_codex_needs_input_records_contextual_agent_activity(self, client):
        tc, factory = client
        with factory() as session:
            wf = AutoWorkflow(name="QA Workflow")
            session.add(wf)
            session.flush()
            step = AutoWorkflowStep(
                workflow_id=wf.id,
                position=0,
                name="Verify browser guard",
                action_type="playwright",
                step_type="playwright",
                config=json.dumps({"tools": ["playwright", "browser_use"], "skills": ["browser-qa"]}),
            )
            session.add(step)
            session.flush()
            run = AutoWorkflowRun(
                workflow_id=wf.id,
                status="running",
                current_step_id=step.id,
                run_data=json.dumps({"project_name": "Player1Sport"}),
            )
            session.add(run)
            session.commit()
            wf_id, run_id, step_id = wf.id, run.id, step.id

        resp = tc.post(
            f"/api/workflows/{wf_id}/runs/{run_id}/codex-events",
            json={
                "event_type": "codex_needs_input",
                "status": "waiting",
                "message": "Should I block Add all until a workflow is selected?",
                "step_id": step_id,
                "project_id": 42,
                "payload": {"situation": "The Jira board button can target a stale workflow id."},
            },
        )

        assert resp.status_code == 200
        with factory() as session:
            run = session.get(AutoWorkflowRun, run_id)
            run_data = json.loads(run.run_data or "{}")
            event = (
                session.query(OrchestratorEvent)
                .filter(OrchestratorEvent.run_id == run_id)
                .order_by(OrchestratorEvent.id.desc())
                .first()
            )
            payload = json.loads(event.payload or "{}")

        assert run.status == "waiting"
        assert run_data["needs_input_context"]["project"] == "Player1Sport"
        assert run_data["needs_input_context"]["workflow"] == "QA Workflow"
        assert run_data["needs_input_context"]["step"] == "Verify browser guard"
        assert run_data["needs_input_context"]["tools"] == ["playwright", "browser_use"]
        assert run_data["worker_question_spoken"].startswith(
            "I'm working on Player1Sport in QA Workflow"
        )
        assert "Should I block Add all" in run_data["worker_question_spoken"]
        assert payload["agent_activity"]["step_type"] == "needs_input"
        assert payload["agent_activity"]["context"]["workflow"] == "QA Workflow"
        assert payload["agent_activity"]["context"]["tools"] == ["playwright", "browser_use"]

    def test_delete_inactive_run_clears_only_that_run_logs(self, client):
        tc, factory = client
        with factory() as session:
            wf = AutoWorkflow(name="Per-run cleanup")
            session.add(wf)
            session.flush()
            inactive = AutoWorkflowRun(workflow_id=wf.id, status="completed", run_data="{}")
            other = AutoWorkflowRun(workflow_id=wf.id, status="completed", run_data="{}")
            active = AutoWorkflowRun(workflow_id=wf.id, status="running", run_data="{}")
            session.add_all([inactive, other, active])
            session.flush()
            inactive_session = ProjectExecutionSession(
                workflow_id=wf.id,
                run_id=inactive.id,
                project_id=1,
                status="completed",
            )
            other_session = ProjectExecutionSession(
                workflow_id=wf.id,
                run_id=other.id,
                project_id=1,
                status="completed",
            )
            session.add_all([inactive_session, other_session])
            session.flush()
            session.add_all([
                ProjectExecutionEvent(session_id=inactive_session.id, event_type="progress"),
                ProjectExecutionEvent(session_id=other_session.id, event_type="progress"),
                OrchestratorEvent(
                    event_uid="inactive-run-event",
                    workflow_id=wf.id,
                    run_id=inactive.id,
                    source="codex",
                    event_type="worker_progress",
                ),
                OrchestratorEvent(
                    event_uid="other-run-event",
                    workflow_id=wf.id,
                    run_id=other.id,
                    source="codex",
                    event_type="worker_progress",
                ),
            ])
            session.commit()
            wf_id, inactive_id, other_id, active_id = wf.id, inactive.id, other.id, active.id

        active_resp = tc.delete(f"/api/workflows/{wf_id}/runs/{active_id}")
        assert active_resp.status_code == 409

        resp = tc.delete(f"/api/workflows/{wf_id}/runs/{inactive_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted_run"] == inactive_id
        assert body["deleted_executor_sessions"] == 1
        assert body["deleted_executor_events"] == 1
        assert body["deleted_orchestrator_events"] == 1
        with factory() as session:
            assert session.get(AutoWorkflowRun, inactive_id) is None
            assert session.get(AutoWorkflowRun, other_id) is not None
            assert session.get(AutoWorkflowRun, active_id) is not None
            assert (
                session.query(OrchestratorEvent)
                .filter(OrchestratorEvent.run_id == other_id)
                .count()
                == 1
            )

    def test_post_ui_feedback_accept_baseline_upserts_screens_into_named_set(self, client, tmp_path, monkeypatch):
        tc, _ = client
        dashboard_path = tmp_path / "dashboard.png"
        settings_path = tmp_path / "settings.png"
        dashboard_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        settings_path.write_bytes(b"\x89PNG\r\n\x1a\nsettings")
        storage_dir = tmp_path / "baseline-store"
        monkeypatch.setenv("ORCHESTRATOR_VISUAL_BASELINE_DIR", str(storage_dir))

        first = tc.post(
            "/api/workflows/4/runs/9/ui-feedback",
            json={
                "label": "approved",
                "board_id": 7,
                "screenshot_paths": [str(dashboard_path)],
                "save_as_visual_baseline": True,
                "visual_baseline_name": "Gold Admin",
                "baseline_screen_name": "Dashboard",
            },
        )
        second = tc.post(
            "/api/workflows/4/runs/10/ui-feedback",
            json={
                "label": "approved",
                "board_id": 7,
                "screenshot_paths": [str(settings_path)],
                "save_as_visual_baseline": True,
                "visual_baseline_name": "Gold Admin",
                "baseline_screen_name": "Settings",
            },
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["visual_baseline"]["id"] == second.json()["visual_baseline"]["id"]

        listed = tc.get("/api/workflows/visual-baselines?board_id=7")
        assert listed.status_code == 200
        baselines = listed.json()["visual_baselines"]
        assert len(baselines) == 1
        assert [screen["screen_name"] for screen in baselines[0]["screens"]] == ["Dashboard", "Settings"]

    def test_create_visual_baseline_stores_reference_screens(self, client):
        tc, _ = client
        resp = tc.post(
            "/api/workflows/visual-baselines",
            json={
                "name": "Gold Admin",
                "board_id": 7,
                "description": "Reference screens from the user's preferred admin UI.",
                "screens": [
                    {
                        "screen_name": "Dashboard",
                        "screenshot_path": "/gold/dashboard.png",
                        "flow_name": "daily overview",
                        "notes": "Dense, scan-friendly layout.",
                    }
                ],
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["visual_baseline"]["name"] == "Gold Admin"
        assert body["visual_baseline"]["scope"] == "board"
        assert body["visual_baseline"]["scope_id"] == 7
        assert body["visual_baseline"]["screens"][0]["screen_name"] == "Dashboard"

    def test_create_visual_baseline_can_store_durable_screenshot_copy(self, client, tmp_path, monkeypatch):
        tc, _ = client
        source_path = tmp_path / "dashboard.png"
        source_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        storage_dir = tmp_path / "baseline-store"
        monkeypatch.setenv("ORCHESTRATOR_VISUAL_BASELINE_DIR", str(storage_dir))

        resp = tc.post(
            "/api/workflows/visual-baselines",
            json={
                "name": "Gold Admin",
                "board_id": 7,
                "store_copy": True,
                "screens": [{"screen_name": "Dashboard", "screenshot_path": str(source_path)}],
            },
        )

        assert resp.status_code == 200
        screen = resp.json()["visual_baseline"]["screens"][0]
        stored_path = screen["screenshot_path"]
        assert stored_path != str(source_path)
        assert stored_path.startswith(str(storage_dir))
        assert Path(stored_path).exists()
        assert screen["metadata"]["source_screenshot_path"] == str(source_path)

    def test_list_visual_baselines_filters_by_board(self, client):
        tc, _ = client
        tc.post(
            "/api/workflows/visual-baselines",
            json={
                "name": "Gold Admin",
                "board_id": 7,
                "screens": [{"screen_name": "Dashboard", "screenshot_path": "/gold/dashboard.png"}],
            },
        )
        tc.post(
            "/api/workflows/visual-baselines",
            json={
                "name": "Other Board",
                "board_id": 8,
                "screens": [{"screen_name": "Dashboard", "screenshot_path": "/other/dashboard.png"}],
            },
        )

        resp = tc.get("/api/workflows/visual-baselines?board_id=7")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert [item["name"] for item in body["visual_baselines"]] == ["Gold Admin"]

    def test_get_visual_baseline_returns_reference_screens(self, client):
        tc, _ = client
        create_resp = tc.post(
            "/api/workflows/visual-baselines",
            json={
                "name": "Gold Admin",
                "project_id": 3,
                "screens": [{"screen_name": "Settings", "screenshot_path": "/gold/settings.png"}],
            },
        )
        baseline_id = create_resp.json()["visual_baseline"]["id"]

        resp = tc.get(f"/api/workflows/visual-baselines/{baseline_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["visual_baseline"]["scope"] == "project"
        assert body["visual_baseline"]["screens"][0]["screenshot_path"] == "/gold/settings.png"

    def test_update_step_persists_visual_baseline_config(self, client):
        tc, factory = client
        create_resp = tc.post("/api/workflows", json={"name": "ui-harness"})
        workflow_id = create_resp.json()["id"]
        session = factory()
        try:
            step = AutoWorkflowStep(
                workflow_id=workflow_id,
                position=0,
                name="Check dashboard",
                action_type="computer_use",
            )
            session.add(step)
            session.commit()
            step_id = step.id
        finally:
            session.close()

        resp = tc.patch(
            f"/api/workflows/{workflow_id}/steps/{step_id}",
            json={
                "config": {
                    "ui_quality_capture": True,
                    "visual_baseline_name": "Gold Admin",
                    "baseline_screen_name": "Dashboard",
                    "visual_diff_threshold": 0.1,
                }
            },
        )

        assert resp.status_code == 200
        session = factory()
        try:
            stored = session.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).one()
            config = json.loads(stored.config)
        finally:
            session.close()
        assert config["ui_quality_capture"] is True
        assert config["visual_baseline_name"] == "Gold Admin"
        assert config["baseline_screen_name"] == "Dashboard"
        assert config["visual_diff_threshold"] == 0.1

    def test_run_history_includes_correction_attempt_status(self, client):
        tc, factory = client
        session = factory()
        try:
            workflow = AutoWorkflow(name="UI Harness")
            session.add(workflow)
            session.flush()
            step = AutoWorkflowStep(
                workflow_id=workflow.id,
                position=0,
                name="Fix dashboard",
                action_type="agent_instruction",
            )
            session.add(step)
            session.flush()
            run = AutoWorkflowRun(
                workflow_id=workflow.id,
                current_step_id=step.id,
                status="running",
                run_data=json.dumps({
                    "result_packet": {
                        "summary": "UI validation failed.",
                        "execution": {
                            "validation_snapshots": [
                                {
                                    "validation_type": "ui_quality",
                                    "verdict": "fail",
                                    "correction_attempt_id": 1,
                                }
                            ]
                        },
                    }
                }),
            )
            session.add(run)
            session.flush()
            validation = OrchestratorValidationRecord(
                workflow_id=workflow.id,
                run_id=run.id,
                step_id=step.id,
                validation_type="ui_quality",
                verdict="fail",
            )
            session.add(validation)
            session.flush()
            session.add(
                OrchestratorCorrectionAttempt(
                    validation_record_id=validation.id,
                    workflow_id=workflow.id,
                    run_id=run.id,
                    step_id=step.id,
                    status="dispatched",
                    attempt_number=1,
                    correction_packet=json.dumps({
                        "failed_validation": {"validation_type": "ui_quality"},
                    }),
                    dispatch_result=json.dumps({
                        "auto_dispatch": True,
                        "terminal_ui_quality_gate": True,
                    }),
                )
            )
            session.commit()
            workflow_id = workflow.id
        finally:
            session.close()

        resp = tc.get(f"/api/workflows/{workflow_id}/runs")

        assert resp.status_code == 200
        run_payload = resp.json()[0]
        assert run_payload["correction_attempts"][0]["status"] == "dispatched"
        assert run_payload["correction_attempts"][0]["dispatch_result"]["auto_dispatch"] is True
        assert run_payload["correction_attempts"][0]["dispatch_result"]["terminal_ui_quality_gate"] is True

    def test_workflow_corrections_endpoint_filters_by_status(self, client):
        tc, factory = client
        session = factory()
        try:
            workflow = AutoWorkflow(name="UI Harness")
            session.add(workflow)
            session.flush()
            queued = OrchestratorCorrectionAttempt(
                workflow_id=workflow.id,
                status="queued",
                attempt_number=1,
            )
            dispatched = OrchestratorCorrectionAttempt(
                workflow_id=workflow.id,
                status="dispatched",
                attempt_number=1,
            )
            session.add_all([queued, dispatched])
            session.commit()
            workflow_id = workflow.id
        finally:
            session.close()

        resp = tc.get(f"/api/workflows/{workflow_id}/corrections?status=dispatched")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["status"] == "dispatched"

    def test_preview_scheduled_action_returns_workflow_payload(self, client):
        tc, _ = client
        resp = tc.post(
            "/api/workflows/scheduled-actions/preview",
            json={
                "title": "Press Enter",
                "schedule": {"kind": "daily", "time": "13:05"},
                "action": {"type": "keypress", "key": "Enter"},
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["workflow"]["workflow_type"] == "scheduled"
        assert body["steps"][0]["action_type"] == "computer_use"
        assert "Press enter" in body["preview"]

    def test_create_scheduled_action_persists_workflow_and_step(self, client):
        tc, factory = client
        resp = tc.post(
            "/api/workflows/scheduled-actions",
            json={
                "title": "Open dashboard",
                "schedule": {"kind": "weekdays", "time": "08:30"},
                "action": {"type": "open_app", "app_name": "Chrome"},
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        workflow_id = body["workflow_id"]

        session = factory()
        try:
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
            steps = session.query(AutoWorkflowStep).filter(AutoWorkflowStep.workflow_id == workflow_id).all()
        finally:
            session.close()

        assert wf.workflow_type == "scheduled"
        assert wf.schedule_enabled is True
        assert wf.schedule_preset == "weekly"
        assert wf.schedule_days == "1,2,3,4,5"
        assert len(steps) == 1
        assert steps[0].action_type == "computer_use"
        assert "Open Chrome" in steps[0].instruction

    def test_list_scheduled_actions_returns_only_scheduled_workflows(self, client):
        tc, _ = client
        tc.post("/api/workflows", json={"name": "Manual workflow", "workflow_type": "manual"})
        create_resp = tc.post(
            "/api/workflows/scheduled-actions",
            json={
                "title": "Type check-in",
                "schedule": {"kind": "daily", "time": "09:45"},
                "action": {"type": "type_text", "text": "Daily check-in", "press_enter": True},
            },
        )
        assert create_resp.status_code == 200

        resp = tc.get("/api/workflows/scheduled-actions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["scheduled_actions"]) == 1
        item = body["scheduled_actions"][0]
        assert item["title"] == "Type check-in"
        assert item["workflow_type"] == "scheduled"
        assert item["schedule"]["kind"] == "daily"
        assert item["action"]["type"] == "type_text"
        assert item["enabled"] is True

    def test_list_scheduled_actions_includes_basic_run_log(self, client):
        tc, factory = client
        create_resp = tc.post(
            "/api/workflows/scheduled-actions",
            json={
                "title": "Press Enter",
                "schedule": {"kind": "daily", "time": "13:05"},
                "action": {"type": "keypress", "key": "Enter"},
            },
        )
        workflow_id = create_resp.json()["workflow_id"]

        session = factory()
        try:
            session.add(AutoWorkflowRun(
                workflow_id=workflow_id,
                status="completed",
                started_at=datetime(2026, 6, 2, 13, 5),
                completed_at=datetime(2026, 6, 2, 13, 6),
                run_data=json.dumps({
                    "phase": "scheduled_action",
                    "result_packet": {"summary": "Pressed Enter successfully."},
                }),
            ))
            session.commit()
        finally:
            session.close()

        resp = tc.get("/api/workflows/scheduled-actions")

        assert resp.status_code == 200
        item = resp.json()["scheduled_actions"][0]
        assert item["run_log"][0]["status"] == "completed"
        assert item["run_log"][0]["result"] == "Pressed Enter successfully."

    def test_update_scheduled_action_can_disable_enable_and_reschedule(self, client):
        tc, factory = client
        create_resp = tc.post(
            "/api/workflows/scheduled-actions",
            json={
                "title": "Open dashboard",
                "schedule": {"kind": "daily", "time": "08:30"},
                "action": {"type": "open_app", "app_name": "Chrome"},
            },
        )
        workflow_id = create_resp.json()["workflow_id"]

        disable_resp = tc.patch(
            f"/api/workflows/scheduled-actions/{workflow_id}",
            json={"enabled": False},
        )
        assert disable_resp.status_code == 200
        assert disable_resp.json()["scheduled_action"]["enabled"] is False

        reschedule_resp = tc.patch(
            f"/api/workflows/scheduled-actions/{workflow_id}",
            json={"enabled": True, "schedule": {"kind": "weekdays", "time": "10:15"}},
        )
        assert reschedule_resp.status_code == 200
        assert reschedule_resp.json()["scheduled_action"]["enabled"] is True
        assert reschedule_resp.json()["scheduled_action"]["schedule"]["kind"] == "weekdays"

        session = factory()
        try:
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
        finally:
            session.close()

        assert wf.schedule_enabled is True
        assert wf.schedule_preset == "weekly"
        assert wf.schedule_days == "1,2,3,4,5"
        assert wf.schedule_time == "10:15"

    def test_delete_scheduled_action_cancels_workflow(self, client):
        tc, factory = client
        create_resp = tc.post(
            "/api/workflows/scheduled-actions",
            json={
                "title": "Press Enter",
                "schedule": {"kind": "daily", "time": "13:05"},
                "action": {"type": "keypress", "key": "Enter"},
            },
        )
        workflow_id = create_resp.json()["workflow_id"]

        resp = tc.delete(f"/api/workflows/scheduled-actions/{workflow_id}")

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        session = factory()
        try:
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        finally:
            session.close()
        assert wf is None

    def test_update_scheduled_action_by_title_can_disable(self, client):
        tc, _ = client
        tc.post(
            "/api/workflows/scheduled-actions",
            json={
                "title": "Open Chrome",
                "schedule": {"kind": "daily", "time": "08:30"},
                "action": {"type": "open_app", "app_name": "Chrome"},
            },
        )

        resp = tc.patch("/api/workflows/scheduled-actions/by-title?title=Chrome", json={"enabled": False})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["scheduled_action"]["title"] == "Open Chrome"
        assert body["scheduled_action"]["enabled"] is False

    def test_delete_scheduled_action_by_title_cancels_matching_workflow(self, client):
        tc, factory = client
        tc.post(
            "/api/workflows/scheduled-actions",
            json={
                "title": "Press Enter",
                "schedule": {"kind": "daily", "time": "13:05"},
                "action": {"type": "keypress", "key": "Enter"},
            },
        )
        tc.post(
            "/api/workflows/scheduled-actions",
            json={
                "title": "Open Chrome",
                "schedule": {"kind": "daily", "time": "08:30"},
                "action": {"type": "open_app", "app_name": "Chrome"},
            },
        )

        resp = tc.delete("/api/workflows/scheduled-actions/by-title?title=Enter")

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        session = factory()
        try:
            names = [wf.name for wf in session.query(AutoWorkflow).filter(AutoWorkflow.workflow_type == "scheduled").all()]
        finally:
            session.close()
        assert names == ["Open Chrome"]

    def test_create_once_scheduled_action_sets_next_run(self, client):
        tc, factory = client
        resp = tc.post(
            "/api/workflows/scheduled-actions",
            json={
                "title": "One-time note",
                "schedule": {"kind": "once", "run_at": "2026-06-03T09:30:00"},
                "action": {"type": "type_text", "text": "Follow up"},
            },
        )

        assert resp.status_code == 200
        workflow_id = resp.json()["workflow_id"]
        session = factory()
        try:
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
        finally:
            session.close()

        assert wf.schedule_preset == "once"
        assert wf.schedule_time == "2026-06-03T09:30:00"
        from distr.core.workflow.scheduler import parse_once_run_at_as_utc

        assert wf.next_run_at == parse_once_run_at_as_utc("2026-06-03T09:30:00")

    def test_visual_baseline_readiness_reports_missing_paths(self, client, tmp_path):
        tc, _ = client
        existing_path = tmp_path / "dashboard.png"
        existing_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        missing_path = tmp_path / "settings.png"
        create_resp = tc.post(
            "/api/workflows/visual-baselines",
            json={
                "name": "Gold Admin",
                "board_id": 7,
                "screens": [
                    {"screen_name": "Dashboard", "screenshot_path": str(existing_path)},
                    {"screen_name": "Settings", "screenshot_path": str(missing_path)},
                ],
            },
        )
        assert create_resp.status_code == 200

        resp = tc.get("/api/workflows/visual-baselines/readiness?board_id=7")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["visual_baseline_readiness"]["verdict"] == "fail"
        assert body["visual_baseline_readiness"]["missing_screen_count"] == 1
        assert body["visual_baseline_readiness"]["missing"][0]["screen_name"] == "Settings"
        assert body["visual_baseline_readiness"]["missing"][0]["screenshot_path"] == str(missing_path)

    def test_codex_needs_input_callback_pauses_run_and_records_intervention(self, client):
        tc, factory = client
        with factory() as db:
            wf = AutoWorkflow(name="handoff workflow", workflow_type="manual", status="active")
            db.add(wf)
            db.flush()
            step = AutoWorkflowStep(
                workflow_id=wf.id,
                position=0,
                name="Implement UI",
                action_type="send_to_project_cli",
                instruction="Fix the UI.",
                status="running",
            )
            db.add(step)
            db.flush()
            run = AutoWorkflowRun(
                workflow_id=wf.id,
                status="running",
                current_step_id=step.id,
                board_id=17,
                ticket_id=23,
                run_data=json.dumps({
                    "project_id": 31,
                    "latest_backend_handoff": {
                        "backend_id": "codex",
                        "model": "auto",
                        "handoff_event_id": 99,
                    },
                }),
            )
            db.add(run)
            db.commit()
            workflow_id = wf.id
            run_id = run.id
            step_id = step.id

        resp = tc.post(
            f"/api/workflows/{workflow_id}/runs/{run_id}/codex-events",
            json={
                "event_type": "codex_needs_input",
                "status": "waiting",
                "message": "Should I preserve the existing dense table layout?",
                "step_id": step_id,
                "ticket_id": 23,
                "project_id": 31,
                "execution_session_id": 44,
                "mistake_label": "unclear_requirement",
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["human_intervention_event_id"]

        with factory() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).one()
            run_data = json.loads(run.run_data)
            intervention = (
                db.query(OrchestratorEvent)
                .filter(OrchestratorEvent.event_type == "human_intervention_recorded")
                .one()
            )
            learned = (
                db.query(OrchestratorLearnedRule)
                .filter(OrchestratorLearnedRule.rule_type == "human_intervention")
                .one()
            )

        assert run.status == "waiting"
        assert step.status == "waiting"
        assert run_data["waiting_kind"] == "needs_human_input"
        assert run_data["human_intervention_state"] == "needs_human_input"
        assert run_data["next_action"] == "needs_human_input"
        assert run_data["worker_question"] == "Should I preserve the existing dense table layout?"
        assert run_data["latest_backend_handoff"]["human_intervention"]["state"] == "needs_human_input"
        assert json.loads(intervention.payload)["label"] == "unclear_requirement"
        assert "dense table" in learned.summary


class TestAuditWorkflowReadOnly403:
    """API returns HTTP 403 when attempting to edit, run, or delete audit workflows."""

    @staticmethod
    def _create_audit_workflow(factory) -> int:
        session = factory()
        wf = AutoWorkflow(name="Audit Trail", description="", status="active", workflow_type="audit")
        session.add(wf)
        session.commit()
        wf_id = wf.id
        session.close()
        return wf_id

    def test_update_audit_workflow_returns_403(self, client):
        tc, factory = client
        wf_id = self._create_audit_workflow(factory)
        resp = tc.patch(f"/api/workflows/{wf_id}", json={"name": "renamed"})
        assert resp.status_code == 403
        assert "read-only" in resp.json()["detail"].lower()

    def test_delete_audit_workflow_returns_403(self, client):
        tc, factory = client
        wf_id = self._create_audit_workflow(factory)
        resp = tc.delete(f"/api/workflows/{wf_id}")
        assert resp.status_code == 403
        assert "read-only" in resp.json()["detail"].lower()

    def test_delete_workflow_notifies_live_workflow_ui(self, client):
        tc, _ = client
        create_resp = tc.post("/api/workflows", json={"name": "live-delete"})
        assert create_resp.status_code == 200
        wf_id = create_resp.json()["id"]

        with patch("distr.gui.web.workflow_events.increment_workflow_updated") as inc:
            resp = tc.delete(f"/api/workflows/{wf_id}")

        assert resp.status_code == 200
        inc.assert_called_once()

    def test_run_audit_workflow_returns_403(self, client):
        tc, factory = client
        wf_id = self._create_audit_workflow(factory)
        resp = tc.post(f"/api/workflows/{wf_id}/run")
        assert resp.status_code == 403
        assert "read-only" in resp.json()["detail"].lower()

    def test_get_audit_workflow_allowed(self, client):
        tc, factory = client
        wf_id = self._create_audit_workflow(factory)
        resp = tc.get(f"/api/workflows/{wf_id}")
        assert resp.status_code == 200
        assert resp.json()["workflow_type"] == "audit"

    def test_edit_non_audit_workflow_allowed(self, client):
        tc, _ = client
        create_resp = tc.post("/api/workflows", json={"name": "normal"})
        assert create_resp.status_code == 200
        wf_id = create_resp.json()["id"]
        resp = tc.patch(f"/api/workflows/{wf_id}", json={"name": "renamed"})
        assert resp.status_code == 200
