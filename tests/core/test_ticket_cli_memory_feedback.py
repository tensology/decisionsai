"""Ticket CLI dispatch + feedback persistence into workspace memory."""

from __future__ import annotations

import contextlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import distr.core.db.kanban  # noqa: F401
import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.projects  # noqa: F401
import distr.core.db.workflow  # noqa: F401
import distr.core.workspace_memory.paths as wm_paths
from distr.core.db import Base
from distr.core.db.orchestrator import OrchestratorLearnedRule
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.workspace_memory.paths import HANDOFF_FILE, companion_root
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


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


def test_persist_worker_feedback_writes_ticket_handoff(tmp_path):
    wm_paths.WORKSPACES_ROOT = tmp_path / "workspaces"
    with patch("distr.core.workspace_memory.sync.sync_projection_for_project", return_value={"ok": True}):
        from distr.core.workspace_memory.feedback_sync import persist_worker_feedback

        result = persist_worker_feedback(
            message="Do not duplicate status badges on the kanban row UI.",
            event_type="cursor_completed",
            source="cursor",
            ticket_id=42,
            project_id=7,
            board_id=3,
        )
    assert result.get("handoff") is True
    handoff = companion_root("tickets", 42) / "memory" / HANDOFF_FILE
    assert handoff.is_file()
    assert "duplicate status badges" in handoff.read_text(encoding="utf-8")


def test_capture_feedback_as_memory_uses_linked_workflow_not_audit(monkeypatch):
    factory = _make_factory()

    def get_session():
        return _session_ctx(factory)

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.standards_memory.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.is_orchestrator_enabled", lambda: False)

    with _session_ctx(factory) as session:
        audit = AutoWorkflow(name="Audit", workflow_type="project_cli", status="active")
        linked = AutoWorkflow(name="Dogfood", workflow_type="manual", status="active")
        session.add_all([audit, linked])
        session.flush()
        from distr.core.workflow.standards_memory import capture_feedback_as_memory

        captured = capture_feedback_as_memory(
            "Always validate UI changes with Playwright before marking tickets complete.",
            workflow_id=int(audit.id),
            linked_workflow_id=int(linked.id),
            board_id=9,
        )
        assert captured is True
        rule = (
            session.query(OrchestratorLearnedRule)
            .filter(OrchestratorLearnedRule.scope == "board")
            .filter(OrchestratorLearnedRule.scope_id == 9)
            .first()
        )
        assert rule is not None
        assert "Playwright" in (rule.summary or "")


def test_codex_bridge_completion_persists_workspace_handoff(monkeypatch, tmp_path):
    wm_paths.WORKSPACES_ROOT = tmp_path / "workspaces"
    factory = _make_factory()

    with _session_ctx(factory) as session:
        workflow = AutoWorkflow(name="Development", workflow_type="project_cli", status="active")
        session.add(workflow)
        session.flush()
        step = AutoWorkflowStep(
            workflow_id=workflow.id,
            name="Ticket #5",
            position=0,
            action_type="send_to_project_cli",
            status="waiting",
        )
        session.add(step)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            status="waiting",
            current_step_id=step.id,
            ticket_id=5,
            board_id=2,
            run_data=json.dumps({
                "project_id": 11,
                "ticket_dispatch": True,
                "waiting_kind": "ide_handoff",
            }),
        )
        session.add(run)
        session.flush()
        workflow_id = workflow.id
        run_id = run.id
        step_id = step.id

    def get_session():
        return _session_ctx(factory)

    monkeypatch.setattr("distr.core.db.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.get_session", get_session)
    monkeypatch.setattr("distr.core.workflow.standards_memory.get_session", get_session)
    monkeypatch.setattr("distr.core.orchestrator.is_orchestrator_enabled", lambda: False)
    monkeypatch.setattr("distr.gui.web.workflow_events.increment_workflow_updated", MagicMock())
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.continue_waiting_step",
        MagicMock(return_value={"success": True}),
    )

    from distr.gui.web.routes.settings.workflows import register_routes

    app = FastAPI()
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    with patch("distr.core.workspace_memory.sync.sync_projection_for_project", return_value={"ok": True}):
        response = client.post(
            f"/api/workflows/{workflow_id}/runs/{run_id}/codex-events",
            json={
                "event_type": "cursor_completed",
                "status": "completed",
                "output": "Fixed duplicate UI labels and validated in browser.",
                "ticket_id": 5,
                "project_id": 11,
                "step_id": step_id,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body.get("auto_continue") is None
    handoff = companion_root("tickets", 5) / "memory" / HANDOFF_FILE
    assert handoff.is_file()
    assert "duplicate UI labels" in handoff.read_text(encoding="utf-8")

    with _session_ctx(factory) as session:
        run_row = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
        assert run_row.status == "completed"


def test_create_ticket_cli_dispatch_run_sets_bridge_fields():
    factory = _make_factory()
    with _session_ctx(factory) as session:
        wf = AutoWorkflow(name="Audit", workflow_type="project_cli", status="in_progress")
        session.add(wf)
        session.flush()
        step = AutoWorkflowStep(workflow_id=wf.id, position=0, name="T", status="running")
        session.add(step)
        session.flush()
        from distr.core.kanban.ticket_cli_memory import create_ticket_cli_dispatch_run

        run_id = create_ticket_cli_dispatch_run(
            session,
            audit_workflow_id=int(wf.id),
            step_id=int(step.id),
            ticket_id=99,
            project_id=4,
            board_id=1,
            ide_mode=True,
            backend_id="cursor_ide",
        )
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).one()
        data = json.loads(run.run_data or "{}")
        assert data.get("ticket_dispatch") is True
        assert data.get("waiting_kind") == "ide_handoff"
        assert run.status == "waiting"


def test_ide_event_persists_handoff_when_no_workflow_bridge():
    tmp = tempfile.TemporaryDirectory()
    wm_paths.WORKSPACES_ROOT = Path(tmp.name) / "workspaces"
    try:
        from distr.core.db.kanban import ProjectExecutionSession
        from distr.core.db.projects import Project

        factory = _make_factory()
        with _session_ctx(factory) as session:
            project = Project(name="Sandbox", folder_location="/tmp/sandbox")
            session.add(project)
            session.flush()
            row = ProjectExecutionSession(
                project_id=project.id,
                ticket_id=8,
                route_type="ide_bridge",
                route_backend="cursor",
                status="running",
                input_packet="{}",
            )
            session.add(row)
            session.flush()
            session_id = int(row.id)
            project_id = int(project.id)

        def get_session():
            return _session_ctx(factory)

        with patch("distr.core.db.get_session", get_session), patch(
            "distr.core.ide_bridge.get_session",
            get_session,
        ), patch("distr.core.workspace_memory.sync.sync_projection_for_project", return_value={"ok": True}), patch(
            "distr.core.ide_bridge.ChatService.add_user_message",
            MagicMock(),
        ), patch(
            "distr.core.ide_bridge.ChatService.append_assistant_notice",
            MagicMock(),
        ):
            from distr.core.ide_bridge import record_ide_event

            record_ide_event(
                source="cursor",
                cwd="/tmp/sandbox",
                project_id=project_id,
                session_id=session_id,
                event_type="cursor_completed",
                output_text="Removed duplicate metadata columns from the settings table UI.",
            )

        handoff = companion_root("tickets", 8) / "memory" / HANDOFF_FILE
        assert handoff.is_file()
        assert "duplicate metadata" in handoff.read_text(encoding="utf-8")
    finally:
        tmp.cleanup()
