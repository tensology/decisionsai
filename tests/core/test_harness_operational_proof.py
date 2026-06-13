"""Operational proof for the harness handoff and scheduled-action loop."""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from unittest.mock import MagicMock

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
from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorLearnedRule, OrchestratorValidationRecord
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
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


def test_harness_proves_worker_handoff_human_pause_and_scheduled_action_lifecycle(monkeypatch):
    factory = _make_factory()

    def get_session():
        return _session_ctx(factory)

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.service.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.scheduler.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.standards_memory.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.is_orchestrator_enabled", lambda: True)
    monkeypatch.setattr(
        "distr.core.kanban.project_execution.append_execution_event",
        MagicMock(),
    )
    monkeypatch.setattr(
        "distr.gui.web.routes.settings.workflows.increment_workflow_updated",
        MagicMock(),
        raising=False,
    )
    monkeypatch.setattr("distr.gui.web.workflow_events.increment_workflow_updated", MagicMock())

    with _session_ctx(factory) as db:
        workflow = AutoWorkflow(name="Harness handoff proof", workflow_type="manual", status="active")
        db.add(workflow)
        db.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            position=0,
            name="Send UI task to Codex",
            action_type="send_to_project_cli",
            status="running",
        )
        db.add(step)
        db.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="running",
            current_step_id=step.id,
            board_id=12,
            ticket_id=77,
            run_data=json.dumps(
                {
                    "project_id": 34,
                    "latest_backend_handoff": {
                        "backend_id": "codex",
                        "model": "auto",
                        "handoff_event_id": 901,
                        "human_intervention": {"state": "none"},
                    },
                }
            ),
        )
        db.add(run)
        db.flush()
        workflow_id = workflow.id
        run_id = run.id
        step_id = step.id

    client = _make_client()
    callback = client.post(
        f"/api/workflows/{workflow_id}/runs/{run_id}/codex-events",
        json={
            "event_type": "codex_needs_input",
            "status": "waiting",
            "message": "Should I preserve the dense dashboard table or simplify it?",
            "step_id": step_id,
            "ticket_id": 77,
            "project_id": 34,
            "execution_session_id": 56,
            "mistake_label": "unclear_requirement",
        },
    )

    assert callback.status_code == 200
    assert callback.json()["success"] is True
    assert callback.json()["human_intervention_event_id"]

    active = client.get(f"/api/workflows/{workflow_id}/active-run")
    assert active.status_code == 200
    active_body = active.json()
    assert active_body["status"] == "waiting"
    assert active_body["human_intervention_state"] == "needs_human_input"
    assert active_body["next_action"] == "needs_human_input"
    assert active_body["worker_question"] == "Should I preserve the dense dashboard table or simplify it?"
    assert active_body["latest_backend_handoff"]["backend_id"] == "codex"
    assert active_body["latest_backend_handoff"]["human_intervention"]["state"] == "needs_human_input"

    with _session_ctx(factory) as db:
        run_data = json.loads(db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one().run_data)
        intervention = db.query(OrchestratorEvent).filter(OrchestratorEvent.event_type == "human_intervention_recorded").one()
        learned = db.query(OrchestratorLearnedRule).filter(OrchestratorLearnedRule.rule_type == "human_intervention").one()

    assert run_data["waiting_kind"] == "needs_human_input"
    assert run_data["next_action"] == "needs_human_input"
    assert json.loads(intervention.payload)["label"] == "unclear_requirement"
    assert "dense dashboard table" in learned.summary

    terminal_callback = client.post(
        f"/api/workflows/{workflow_id}/runs/{run_id}/codex-events",
        json={
            "event_type": "codex_completed",
            "status": "completed",
            "message": "Worker completed the requested edit after human steering.",
            "step_id": step_id,
            "ticket_id": 77,
            "project_id": 34,
            "execution_session_id": 56,
        },
    )

    assert terminal_callback.status_code == 200
    assert terminal_callback.json()["success"] is True
    active_after_terminal = client.get(f"/api/workflows/{workflow_id}/active-run")
    assert active_after_terminal.status_code == 200
    terminal_body = active_after_terminal.json()
    assert terminal_body["status"] == "running"
    assert terminal_body["human_intervention_state"] == "resolved"
    assert terminal_body["latest_backend_handoff"]["state"] == "completed"
    assert terminal_body["next_action"] == "continue"

    preview = client.post(
        "/api/workflows/scheduled-actions/preview",
        json={
            "title": "Proof press enter",
            "schedule": {"kind": "once", "run_at": "2026-06-02T13:05:00"},
            "action": {"type": "keypress", "key": "Enter"},
            "target_context": {"app_name": "Chrome"},
            "safety": {"require_app_in_foreground": True},
        },
    )
    assert preview.status_code == 200
    assert preview.json()["success"] is True
    assert "Press enter" in preview.json()["preview"]

    one_time = client.post(
        "/api/workflows/scheduled-actions",
        json={
            "title": "Proof press enter",
            "schedule": {"kind": "once", "run_at": "2026-06-02T13:05:00"},
            "action": {"type": "keypress", "key": "Enter"},
            "target_context": {"app_name": "Chrome"},
            "safety": {"require_app_in_foreground": True},
        },
    )
    assert one_time.status_code == 200
    one_time_workflow_id = one_time.json()["workflow_id"]

    recurring = client.post(
        "/api/workflows/scheduled-actions",
        json={
            "title": "Proof open dashboard",
            "schedule": {"kind": "weekdays", "time": "08:30", "timezone": "Africa/Johannesburg"},
            "action": {"type": "open_app", "app_name": "Chrome"},
            "target_context": {"app_name": "Chrome"},
            "safety": {"bring_app_to_front": True},
        },
    )
    assert recurring.status_code == 200
    recurring_workflow_id = recurring.json()["workflow_id"]

    listed = client.get("/api/workflows/scheduled-actions")
    assert listed.status_code == 200
    titles = {item["title"] for item in listed.json()["scheduled_actions"]}
    assert {"Proof press enter", "Proof open dashboard"}.issubset(titles)

    disabled = client.patch(f"/api/workflows/scheduled-actions/{recurring_workflow_id}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["scheduled_action"]["enabled"] is False

    rescheduled = client.patch(
        f"/api/workflows/scheduled-actions/{recurring_workflow_id}",
        json={"enabled": True, "schedule": {"kind": "daily", "time": "10:15", "timezone": "Africa/Johannesburg"}},
    )
    assert rescheduled.status_code == 200
    assert rescheduled.json()["scheduled_action"]["enabled"] is True
    assert rescheduled.json()["scheduled_action"]["schedule"]["kind"] == "daily"
    assert rescheduled.json()["scheduled_action"]["schedule"]["time"] == "10:15"

    with _session_ctx(factory) as db:
        scheduled = db.query(AutoWorkflow).filter(AutoWorkflow.id == one_time_workflow_id).one()
        scheduled.next_run_at = datetime(2026, 6, 2, 13, 5, 0)

    from distr.core.workflow.scheduler import run_scheduled_workflow

    monkeypatch.setattr("distr.core.workflow.scheduler._is_target_app_frontmost", lambda app: False, raising=False)
    assert run_scheduled_workflow(one_time_workflow_id) is True

    listed_after_skip = client.get("/api/workflows/scheduled-actions")
    assert listed_after_skip.status_code == 200
    one_time_payload = next(
        item
        for item in listed_after_skip.json()["scheduled_actions"]
        if item["workflow_id"] == one_time_workflow_id
    )
    assert one_time_payload["enabled"] is False
    assert one_time_payload["run_log"][0]["status"] == "skipped"
    assert "foreground" in one_time_payload["run_log"][0]["result"].lower()
    assert "Chrome" in one_time_payload["run_log"][0]["result"]

    cancelled = client.delete("/api/workflows/scheduled-actions/by-title?title=dashboard")
    assert cancelled.status_code == 200
    assert cancelled.json()["success"] is True

    final_list = client.get("/api/workflows/scheduled-actions")
    remaining_ids = {item["workflow_id"] for item in final_list.json()["scheduled_actions"]}
    assert recurring_workflow_id not in remaining_ids
    assert one_time_workflow_id in remaining_ids


def test_live_proof_records_ui_change_artifacts_and_feedback(monkeypatch, tmp_path):
    factory = _make_factory()

    def get_session():
        return _session_ctx(factory)

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.is_orchestrator_enabled", lambda: True)

    before_png = tmp_path / "before.png"
    after_png = tmp_path / "after.png"
    before_html = tmp_path / "before.html"
    after_html = tmp_path / "after.html"
    for path in (before_png, after_png, before_html, after_html):
        path.write_text("proof", encoding="utf-8")

    import scripts.harness_live_proof as live_proof

    monkeypatch.setattr(live_proof, "_proof_dir", lambda: tmp_path)
    monkeypatch.setattr(
        live_proof,
        "_capture_ui_proof_screenshots",
        lambda proof_dir, stamp: {
            "proof_dir": str(proof_dir),
            "before_html": str(before_html),
            "after_html": str(after_html),
            "before_screenshot": str(before_png),
            "after_screenshot": str(after_png),
        },
    )

    proof = live_proof._ui_change_proof(
        "20260602_120000",
        project_id=9,
        workflow_id=321,
        run_id=132,
        step_id=1818,
    )

    assert proof["validation_id"]
    assert proof["feedback_id"]
    assert proof["before_screenshot"] == str(before_png)
    assert proof["after_screenshot"] == str(after_png)
    assert (tmp_path / "ui-change-20260602_120000" / "flow.md").exists()

    with _session_ctx(factory) as db:
        validation_event = db.query(OrchestratorEvent).filter(OrchestratorEvent.event_type == "validation_recorded").one()
        validation = db.query(OrchestratorValidationRecord).filter(OrchestratorValidationRecord.id == proof["validation_id"]).one()
        feedback = db.query(OrchestratorEvent).filter(OrchestratorEvent.event_type == "ui_feedback_labeled").one()

    validation_event_payload = json.loads(validation_event.payload)
    validation_payload = json.loads(validation.payload)
    feedback_payload = json.loads(feedback.payload)
    snapshot = validation_payload["snapshot"]
    assert validation_event_payload["validation_record_id"] == proof["validation_id"]
    assert snapshot["validation_type"] == "ui_quality"
    assert snapshot["verdict"] == "pass"
    assert snapshot["artifacts"]["before_screenshot"] == str(before_png)
    assert feedback_payload["label"] == "approved"
    assert feedback_payload["screenshot_paths"] == [str(after_png)]


def test_live_proof_real_worker_edit_completes_run_with_result_packet(monkeypatch, tmp_path):
    factory = _make_factory()

    def get_session():
        return _session_ctx(factory)

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.dispatcher.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.service.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.is_orchestrator_enabled", lambda: True)
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.increment_workflow_updated",
        MagicMock(),
        raising=False,
    )

    with _session_ctx(factory) as db:
        workflow = AutoWorkflow(name="Harness real edit proof", workflow_type="manual", status="active")
        db.add(workflow)
        db.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            position=0,
            name="Real edit",
            action_type="send_to_project_cli",
            status="waiting",
        )
        db.add(step)
        db.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="waiting",
            current_step_id=step.id,
            board_id=26060201,
            ticket_id=2606020201,
            run_data=json.dumps(
                {
                    "source_type": "harness_live_proof",
                    "project_id": 9,
                    "waiting_kind": "needs_human_input",
                    "human_intervention_state": "needs_human_input",
                    "next_action": "needs_human_input",
                    "latest_backend_handoff": {"backend_id": "cursor", "state": "waiting"},
                }
            ),
        )
        db.add(run)
        db.flush()
        ids = {"workflow_id": workflow.id, "run_id": run.id, "step_id": step.id}

    import scripts.harness_live_proof as live_proof

    monkeypatch.setattr(live_proof, "_proof_dir", lambda: tmp_path)
    edit = live_proof._write_real_edit_artifacts("20260602_130000")
    terminal = live_proof._store_real_edit_result_packet(
        ids=ids,
        project_id=9,
        backend_id="cursor",
        edit=edit,
    )

    assert terminal["run_status"] == "completed"
    assert terminal["result_packet_status"] == "completed"
    assert terminal["final_verdict"] == "pass"
    assert terminal["next_action_decision"]["action"] == "continue"
    assert "worker_surface.diff" in edit["diff"]

    with _session_ctx(factory) as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == ids["run_id"]).one()
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == ids["step_id"]).one()
        data = json.loads(run.run_data or "{}")

    packet = data["result_packet"]
    assert run.status == "completed"
    assert step.status == "completed"
    assert data["human_intervention_state"] == "resolved"
    assert data["latest_backend_handoff"]["state"] == "completed"
    assert packet["changes"]["files_changed"] == [edit["target_file"]]
    assert packet["artifacts"]["diffs_or_patches"] == [edit["diff"]]
    assert packet["execution"]["validation_snapshots"][0]["validation_type"] == "file_edit"
