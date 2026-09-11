"""Failure-boundary regressions from the September Development audit."""
import contextlib
import json
from datetime import timedelta
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from distr.core.db import Base
from distr.core.db.projects import Project
from distr.core.db.automation import Automation, AutomationRun
from distr.core.db.workflow import PlanFileWrite
from distr.core.planning import service as planning
from tests.core.test_planning_workspace import plan_workspace_db


@pytest.fixture
def automation_db(tmp_path, monkeypatch):
    import distr.core.automation_orchestrator  # Import before patching the global session provider.
    engine = create_engine(f"sqlite:///{tmp_path / 'automation.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    @contextlib.contextmanager
    def session():
        with factory() as db:
            yield db
    monkeypatch.setattr("distr.core.db.get_session", session)
    return session


@pytest.mark.parametrize("rule", [
    "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;BYHOUR=9;COUNT=3",
    "FREQ=DAILY;UNTIL=20300101T000000Z", "FREQ=DAILY;BYHOUR=9,17",
    "FREQ=MONTHLY;BYDAY=2MO", "FREQ=YEARLY;BYMONTH=9",
    "FREQ=HOURLY;BYMINUTE=15", "FREQ=DAILY;BYHOUR=24", "FREQ=DAILY;BYSECOND=30",
])
def test_lossy_recurrences_are_rejected(rule):
    from distr.core.automation.imports import schedule_from_rrule
    with pytest.raises(ValueError):
        schedule_from_rrule(rule)


def test_cron_month_day_is_preserved_and_month_filter_is_rejected():
    from distr.core.automation.schedule_import import import_schedule
    schedule = import_schedule({"cron": "15 9 20 * *", "timezone": "Africa/Johannesburg"})
    assert (schedule["kind"], schedule["days"], schedule["time"], schedule["timezone"]) == ("monthly", "20", "09:15", "Africa/Johannesburg")
    with pytest.raises(ValueError):
        import_schedule({"cron": "15 9 * 12 *"})


def test_exact_project_wins_and_multiple_project_scopes_are_unresolved(automation_db, tmp_path):
    from distr.core.automation.imports import _match_project_id
    parent = tmp_path / "repo"
    nested = parent / "nested"
    with automation_db() as db:
        a, b = Project(name="Parent", folder_location=str(parent)), Project(name="Nested", folder_location=str(nested))
        db.add_all([a, b]); db.commit()
        aid, bid = a.id, b.id
    assert _match_project_id([str(parent)]) == aid
    assert _match_project_id([str(nested / 'src')]) == bid
    assert _match_project_id([str(parent), str(nested)]) is None
    assert _match_project_id([str(tmp_path)]) is None


@pytest.mark.parametrize("response,accepted", [({"status": "failed", "summary": "Thread preparation failed"}, False), ({"status": "running", "automation_run_id": 51}, True), (None, False)])
def test_one_shot_is_claimed_once_and_requires_dispatch_acknowledgement(automation_db, monkeypatch, response, accepted):
    from distr.core.automation.scheduler import run_scheduled_automation
    from distr.core.automation.store import utc_now
    due = utc_now() - timedelta(minutes=1)
    with automation_db() as db:
        row = Automation(name="One shot", status="active", schedule_enabled=True, schedule_preset="once", next_run_at=due)
        db.add(row); db.commit(); identity = row.id
    payload = {"id": f"auto_{identity}", "record_id": identity}
    calls = []
    def dispatch(*args, **kwargs):
        calls.append(kwargs)
        assert run_scheduled_automation(payload) is False
        with automation_db() as db:
            assert db.query(AutomationRun).filter_by(status="dispatching").count() == 1
        return response
    monkeypatch.setattr("distr.core.automation_orchestrator.dispatch_automation_to_current_chat", dispatch)
    assert run_scheduled_automation(payload) is accepted
    assert len(calls) == 1
    with automation_db() as db:
        row = db.get(Automation, identity)
        attempt = db.query(AutomationRun).one()
        assert not row.schedule_enabled
        assert attempt.status == ("dispatch_accepted" if accepted else "failed")
        assert json.loads(attempt.run_data)["due_at"] == due.isoformat()
        if not accepted:
            assert row.status == "paused"
            assert row.next_run_at == due


def make_item(fixture):
    workspace = planning.ensure_workspace(board_key="decisions:durability", board_provider="decisions", board_name="Durability", project_id=fixture["project_id"])
    item = planning.create_item(workspace_id=workspace["id"], item_type="brief", content="Original")
    return workspace, item


def test_database_commit_failure_cannot_change_the_project_file(plan_workspace_db, monkeypatch):
    workspace, item = make_item(plan_workspace_db)
    original = Session.commit
    def fail_pending(db):
        if any(isinstance(row, PlanFileWrite) for row in db.new):
            raise RuntimeError("Injected commit failure")
        return original(db)
    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail_pending)
        with pytest.raises(RuntimeError, match="commit failure"):
            planning.update_item(item["id"], content="New version", expected_revision=1)
    assert Path(item["file_path"]).read_text() == "Original"
    assert planning.get_workspace(workspace["id"])["items"][0]["content"] == "Original"
    assert len(planning.list_revisions(item["id"])) == 1


def test_failed_file_projection_recovers_after_the_committed_revision(plan_workspace_db, monkeypatch):
    workspace, item = make_item(plan_workspace_db)
    def fail(*args):
        raise OSError("Injected rename failure")
    with monkeypatch.context() as patch:
        patch.setattr("distr.core.planning.files.os.replace", fail)
        updated = planning.update_item(item["id"], content="Committed revision", expected_revision=1)
        assert updated["file_sync_error"]
        assert Path(item["file_path"]).read_text() == "Original"
    recovered = planning.get_workspace(workspace["id"])["items"][0]
    assert recovered["file_sync_error"] == ""
    assert recovered["revision_count"] == 2
    assert Path(item["file_path"]).read_text() == "Committed revision"


def test_recovery_preserves_external_edits_made_after_commit(plan_workspace_db, monkeypatch):
    workspace, item = make_item(plan_workspace_db)
    with monkeypatch.context() as patch:
        patch.setattr("distr.core.planning.files.os.replace", lambda *args: (_ for _ in ()).throw(OSError("offline")))
        planning.update_item(item["id"], content="Committed revision", expected_revision=1)
    Path(item["file_path"]).write_text("External authored edit")
    recovered = planning.get_workspace(workspace["id"])["items"][0]
    assert recovered["file_sync_error"]
    assert recovered["content"] == "Committed revision"
    assert Path(item["file_path"]).read_text() == "External authored edit"


def test_discovery_does_not_replace_a_curated_overview(plan_workspace_db):
    workspace, _ = make_item(plan_workspace_db)
    first = planning.discover_project(workspace["id"], instruction="Scan project")["item"]
    planning.update_item(first["id"], content="Curated overview", expected_revision=1)
    result = planning.discover_project(workspace["id"], instruction="Scan again")
    assert result["action"] == "proposed"
    assert result["item"]["id"] != first["id"]
    items = planning.get_workspace(workspace["id"])["items"]
    assert next(row for row in items if row["id"] == first["id"])["content"] == "Curated overview"


def test_external_file_import_requires_the_reviewed_hash_and_revision(plan_workspace_db):
    workspace, item = make_item(plan_workspace_db)
    path = Path(item["file_path"])
    path.write_text("External authored edit")
    review = planning.review_file(item["id"])
    path.write_text("A newer external edit")
    with pytest.raises(ValueError, match="file changed"):
        planning.reconcile_file(item["id"], expected_revision=review["expected_revision"], file_hash=review["file_hash"], action="import")
    review = planning.review_file(item["id"])
    imported = planning.reconcile_file(item["id"], expected_revision=review["expected_revision"], file_hash=review["file_hash"], action="import")
    assert imported["content"] == path.read_text() == "A newer external edit"
    assert imported["status"] == "draft"
    assert planning.list_revisions(item["id"])[-1]["content"] == "Original"


def test_missing_file_requires_explicit_restore_and_cannot_overwrite_new_file(plan_workspace_db):
    workspace, item = make_item(plan_workspace_db)
    path = Path(item["file_path"])
    path.unlink()
    with pytest.raises(ValueError, match="changed outside"):
        planning.update_item(item["id"], content="Replacement", expected_revision=1)
    assert planning.review_file(item["id"])["file_content"] is None
    restored = planning.reconcile_file(item["id"], expected_revision=1, file_hash=None, action="restore")
    assert not restored["file_sync_error"]
    assert path.read_text() == "Original"
    with pytest.raises(ValueError, match="file changed"):
        planning.reconcile_file(item["id"], expected_revision=1, file_hash=None, action="restore")


def test_reports_http_route_reads_scoped_records_and_enforces_limit(automation_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from distr.core.reports import service
    from distr.gui.web.routes.development import register_routes
    from distr.core.automation.store import utc_now
    monkeypatch.setattr(service, "get_session", automation_db)
    with automation_db() as db:
        definition = Automation(name="Report fixture")
        db.add(definition); db.flush()
        db.add(AutomationRun(automation_id=definition.id, status="completed", started_at=utc_now() - timedelta(seconds=50), completed_at=utc_now()))
        db.add(AutomationRun(automation_id=definition.id, status="dispatch_accepted"))
        db.commit()
    app = FastAPI()
    from fastapi import APIRouter
    router = APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    response = client.get("/api/workflows/studio/reports?limit=10")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["duration_seconds"] == 50
    assert client.get("/api/workflows/studio/reports?limit=201").status_code == 422


def test_restart_pauses_unacknowledged_first_class_and_legacy_dispatches(automation_db, monkeypatch):
    from distr.core.automation.scheduler import reconcile_automation_startup_state
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
    monkeypatch.setattr("distr.core.automation.store.migrate_legacy_automation_workflows", lambda: 0)
    with automation_db() as db:
        definition = Automation(name="Interrupted", status="active", schedule_enabled=True)
        legacy = AutoWorkflow(name="Legacy interrupted", schedule_enabled=True)
        db.add_all([definition, legacy]); db.flush()
        db.add(AutomationRun(automation_id=definition.id, status="dispatching"))
        db.add(AutoWorkflowRun(workflow_id=legacy.id, status="dispatching"))
        db.commit()
    assert reconcile_automation_startup_state()["recovered_runs"] == 2
    with automation_db() as db:
        assert not db.query(Automation).one().schedule_enabled
        assert not db.query(AutoWorkflow).one().schedule_enabled
        for model in (AutomationRun, AutoWorkflowRun):
            row = db.query(model).one()
            assert row.status == "failed"
            assert json.loads(row.run_data)["retry_policy"] == "manual_review"
    assert reconcile_automation_startup_state()["recovered_runs"] == 0


def test_dispatch_acknowledgement_preserves_a_concurrent_schedule_edit(automation_db, monkeypatch):
    from distr.core.automation.scheduler import run_scheduled_automation
    from distr.core.automation.store import utc_now
    edited_due = utc_now() + timedelta(days=3)
    with automation_db() as db:
        row = Automation(name="Editable", status="active", schedule_enabled=True, next_run_at=utc_now() - timedelta(minutes=1))
        db.add(row); db.commit(); identity = row.id
    def dispatch(*args, **kwargs):
        with automation_db() as db:
            db.get(Automation, identity).next_run_at = edited_due
            db.commit()
        return {"status": "running"}
    monkeypatch.setattr("distr.core.automation_orchestrator.dispatch_automation_to_current_chat", dispatch)
    assert run_scheduled_automation({"record_id": identity})
    with automation_db() as db:
        assert db.get(Automation, identity).next_run_at == edited_due
