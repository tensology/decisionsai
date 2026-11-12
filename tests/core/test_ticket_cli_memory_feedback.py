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
from distr.core.workspace_memory.paths import HANDOFF_FILE, WORKER_HANDOFF_FILE, companion_root
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


def test_persist_worker_feedback_writes_ticket_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(wm_paths, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr("distr.core.mempalace.wiring.dual_write_workspace_event", lambda **_: None)
    monkeypatch.setattr("distr.core.workspace_memory.feedback_sync._resolve_linked_workflow_id", lambda _: None)
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


def test_project_worker_feedback_preserves_project_notes_and_remains_in_pickup(tmp_path, monkeypatch):
    from distr.core.workspace_memory.feedback_sync import persist_worker_feedback
    from distr.core.workspace_memory.pickup_handoff import build_pickup_brief, read_ledger_tail
    from distr.core.workspace_memory.reader import load_workspace_context

    monkeypatch.setattr(wm_paths, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr("distr.core.mempalace.wiring.dual_write_workspace_event", lambda **_: None)
    monkeypatch.setattr("distr.core.workspace_memory.feedback_sync._resolve_linked_workflow_id", lambda _: None)
    monkeypatch.setattr("distr.core.workspace_memory.sync.sync_projection_for_project", lambda *a, **kw: {})
    notes = companion_root("projects", 7) / "memory" / HANDOFF_FILE
    notes.parent.mkdir(parents=True)
    original = b"# Handoff\n\nReview the rollback plan before launch.\n\n"
    notes.write_bytes(original)

    for ticket_id, summary in [(42, "First ticket completed."), (None, "Standalone worker completed.")]:
        assert persist_worker_feedback(
            output=summary, event_type="cli_completed", source="pi", ticket_id=ticket_id, project_id=7,
        )["handoff"]
        assert notes.read_bytes() == original
        pickup = build_pickup_brief(entity_type="projects", entity_id=7)
        assert "Review the rollback plan before launch." in pickup
        assert summary in pickup

    ticket = companion_root("tickets", 42) / "memory" / HANDOFF_FILE
    assert "First ticket completed." in ticket.read_text()
    messages = [event["message"] for event in read_ledger_tail("projects", 7)]
    assert "First ticket completed." in messages
    assert "Standalone worker completed." in messages
    context = load_workspace_context(project_id=7, ensure=False, include_pickup_brief=True)
    assert "Review the rollback plan before launch." in context.handoff_preview
    assert "Standalone worker completed." in context.handoff_full
    assert "Standalone worker completed." in context.pickup_brief
    assert "First ticket completed." not in context.pickup_brief


def test_project_memory_endpoint_and_projection_keep_both_handoffs(tmp_path, monkeypatch):
    from distr.core.db.projects import Project
    from distr.core.workspace_memory.lifecycle import handoff_cli_session
    from distr.core.workspace_memory.pickup_handoff import build_pickup_brief, read_handoff_preview, write_handoff
    from distr.core.workspace_memory.template import empty_handoff_md
    from distr.gui.web.routes.workspace_memory import create_routes

    repo = tmp_path / "project"
    repo.mkdir()
    factory = _make_factory()
    with _session_ctx(factory) as session:
        session.add(Project(id=7, name="Workspace handoff", folder_location=str(repo)))
        session.add(Project(id=8, name="New project"))
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(wm_paths, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr("distr.core.mempalace.wiring.dual_write_workspace_event", lambda **_: None)
    monkeypatch.setattr("distr.core.workspace_memory.provision.bootstrap_project", lambda *_: None)
    monkeypatch.setenv("DECISIONS_ENABLE_REPO_PROJECTION", "1")

    write_handoff("projects", 7, body="Keep the pending release decision visible.\n\n_No handoff recorded yet_")
    handoff_cli_session(project_id=7, summary="Verified the migration in staging.")
    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)
    response = client.get("/api/projects/7/workspace-memory")
    assert response.status_code == 200, response.text
    memory = response.json()
    assert "Keep the pending release decision visible." in memory["handoff_preview"]
    assert any(entry["message"] == "Verified the migration in staging." for entry in memory["ledger_tail"])
    projection = repo / ".decisions" / "memory"
    assert "Keep the pending release decision visible." in (projection / HANDOFF_FILE).read_text()
    assert "Verified the migration in staging." in (projection / WORKER_HANDOFF_FILE).read_text()

    empty_notes = companion_root("projects", 8) / "memory" / HANDOFF_FILE
    empty_notes.parent.mkdir(parents=True)
    empty_notes.write_text(empty_handoff_md())
    handoff_cli_session(project_id=8, summary="First result for project eight.")
    assert "First result for project eight." in read_handoff_preview("projects", 8)
    assert read_handoff_preview("projects", 8, max_chars=16).startswith("# Latest")
    response = client.get("/api/projects/8/workspace-memory")
    assert response.status_code == 200, response.text
    assert "First result for project eight." in response.json()["handoff_preview"]
    assert "Verified the migration in staging." not in response.text
    pickup = build_pickup_brief(entity_type="projects", entity_id=8)
    assert "First result for project eight." in pickup
    assert "No handoff recorded yet" not in pickup
    assert "No handoff recorded yet" in empty_notes.read_text()
    assert "Verified the migration in staging." in (projection / WORKER_HANDOFF_FILE).read_text()


def test_feedback_reports_partial_handoff_when_project_write_fails(tmp_path, monkeypatch):
    from distr.core.workspace_memory.feedback_sync import persist_worker_feedback
    from distr.core.workspace_memory.pickup_handoff import read_ledger_tail

    monkeypatch.setattr(wm_paths, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr("distr.core.mempalace.wiring.dual_write_workspace_event", lambda **_: None)
    monkeypatch.setattr("distr.core.workspace_memory.feedback_sync._resolve_linked_workflow_id", lambda _: None)
    monkeypatch.setattr("distr.core.workspace_memory.sync.sync_projection_for_project", lambda *a, **kw: {})
    project_handoff = companion_root("projects", 7) / "memory" / WORKER_HANDOFF_FILE
    project_handoff.mkdir(parents=True)
    result = persist_worker_feedback(output="Finished the check.", event_type="cli_completed", ticket_id=42, project_id=7)

    assert result["handoff"] is False
    assert result["ticket_ledger"] and result["project_ledger"]
    assert "Finished the check." in (companion_root("tickets", 42) / "memory" / HANDOFF_FILE).read_text()
    assert read_ledger_tail("projects", 7)[-1]["message"] == "Finished the check."
    assert project_handoff.is_dir()


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
    monkeypatch.setattr(wm_paths, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr("distr.core.mempalace.wiring.dual_write_workspace_event", lambda **_: None)
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
        from distr.core.workspace_memory.pickup_handoff import write_handoff

        notes = Path(write_handoff("projects", 11, body="Retain the project acceptance criteria."))
        before = notes.read_bytes()
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
    assert notes.read_bytes() == before
    worker_handoff = companion_root("projects", 11) / "memory" / WORKER_HANDOFF_FILE
    assert "duplicate UI labels" in worker_handoff.read_text(encoding="utf-8")

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
