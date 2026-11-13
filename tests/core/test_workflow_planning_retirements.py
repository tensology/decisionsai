"""Automation-backed workflows, Development presets, and retired Plan sections (2026-11-11)."""
import contextlib
import json
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.automation import Automation
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, PlanItem
from distr.core.workflow import service as workflow_service
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "distr" / "gui" / "web" / "static" / "development"


@pytest.fixture
def workflow_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'workflows.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    monkeypatch.setattr(workflow_service, "get_session", get_session)
    import distr.core.workflow.loop_presets as loop_presets
    monkeypatch.setattr(loop_presets, "get_session", get_session)
    import distr.core.workspace_memory.lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "hook_remove_workspace", lambda *args, **kwargs: True)
    import distr.core.workspace_memory.provision as provision
    monkeypatch.setattr(provision, "bootstrap_workflow", lambda *args, **kwargs: "")
    import distr.gui.web.workflow_events as workflow_events
    monkeypatch.setattr(workflow_events, "increment_workflow_updated", lambda: None)
    with get_session() as db:
        plain = AutoWorkflow(name="Plain workflow", status="draft", workflow_type="manual")
        backed = AutoWorkflow(name="Bank reminder", status="draft", workflow_type="manual")
        linked_only = AutoWorkflow(name="Linked only", status="draft", workflow_type="manual")
        db.add_all([plain, backed, linked_only])
        db.flush()
        db.add(AutoWorkflowStep(workflow_id=plain.id, name="Step", position=0))
        db.add(Automation(name="Email reminder", automation_type="scheduled_instruction",
                          action_config=json.dumps({"development_workflow_id": backed.id})))
        db.add(Automation(name="Tool action", automation_type="tool_action", linked_workflow_id=linked_only.id))
        ids = {"plain": plain.id, "backed": backed.id, "linked_only": linked_only.id}
    return ids, get_session


@pytest.fixture
def workflow_client(workflow_db):
    from distr.gui.web.routes.development.workflows import register_routes

    app = FastAPI()
    router = APIRouter(prefix="/api")
    register_routes(router, None)
    app.include_router(router)
    with TestClient(app) as client:
        yield client, workflow_db[0], workflow_db[1]


def test_automation_backed_workflows_are_hidden_from_the_list(workflow_db):
    ids, _ = workflow_db
    listed = {row["id"] for row in workflow_service.list_workflows(limit=50)}
    assert ids["plain"] in listed
    assert ids["backed"] not in listed
    assert ids["linked_only"] not in listed
    assert workflow_service.get_workflow(ids["backed"])["automation_backed"] is True
    assert workflow_service.get_workflow(ids["plain"])["automation_backed"] is False


def test_delete_route_refuses_automation_backed_workflow(workflow_client):
    client, ids, get_session = workflow_client
    response = client.delete(f"/api/workflows/{ids['backed']}")
    assert response.status_code == 409
    assert "Email reminder" in response.json()["detail"]
    assert "Automations" in response.json()["detail"]
    with get_session() as db:
        assert db.get(AutoWorkflow, ids["backed"]) is not None


def test_development_presets_offer_development_and_keep_content_parked(workflow_client):
    client, _, get_session = workflow_client
    presets = client.get("/api/workflows/development-presets").json()["presets"]
    slugs = [preset["slug"] for preset in presets]
    # 2026-11-12 split: slim Development leads, then the two separately approved deploys.
    assert slugs[:3] == ["development-implement-verify", "deploy-to-development", "deploy-to-production"]
    assert presets[0]["name"] == "Development" and presets[0]["step_count"] == 7
    assert "development-ticket-to-implementation" not in slugs  # legacy 11-step preset is no longer offered
    assert "polish-verify-and-ship" in slugs
    assert not {"article-from-ticket", "seo-landing-page", "cold-outreach-batch", "follow-up-cadence"} & set(slugs)
    created = client.post("/api/workflows/from-preset", json={"slug": "development-implement-verify"})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["name"] == "Development"
    assert len(body["steps"]) == 7
    assert body["steps"][0]["name"] == "Understand ticket and acceptance criteria"
    assert client.post("/api/workflows/from-preset", json={"slug": "cold-outreach-batch"}).status_code == 404


def test_workflows_ui_offers_presets_and_hides_backed_delete():
    source = (STATIC / "workflows" / "index.js").read_text()
    assert "/workflows/development-presets" in source and "/workflows/from-preset" in source
    assert "automation_backed" in source


def test_plan_scaffold_and_ui_drop_retired_sections(plan_workspace_db, monkeypatch):  # noqa: F811
    from distr.core.planning import service
    from distr.core.planning.context import planning_snapshot

    monkeypatch.setattr(service, "_write_managed_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_finish_file_projection", lambda *args, **kwargs: "")
    plan = service.ensure_workspace(board_key="decisions:9", board_provider="decisions",
                                    board_name="Plan", project_id=plan_workspace_db["project_id"])
    created = {item["item_type"] for item in service.ensure_plan_scaffold(plan["id"])}
    assert {"brief", "prd", "frac", "architecture"} <= created
    assert not {"handover", "skills"} & created
    for retired in ("handover", "skills"):
        with pytest.raises(ValueError):
            service.create_item(workspace_id=plan["id"], item_type=retired)
    # A stored legacy Handover row stays untouched in the database but leaves the workspace view.
    with plan_workspace_db["session"]() as db:
        db.add(PlanItem(workspace_id=plan["id"], item_type="handover", title="Handover", content="# Handover"))
        db.commit()
    visible = {item["item_type"] for item in service.get_workspace(plan["id"])["items"]}
    assert "handover" not in visible and "brief" in visible
    with plan_workspace_db["session"]() as db:
        assert db.query(PlanItem).filter_by(workspace_id=plan["id"], item_type="handover").count() == 1
    import distr.core.db as core_db
    monkeypatch.setattr(core_db, "get_session", plan_workspace_db["session"])
    snapshot = planning_snapshot(plan_workspace_db["project_id"])
    assert snapshot["workspaces"] and snapshot["coverage"]["item_total"] >= 4

    conversation_js = (STATIC / "planning" / "conversation.js").read_text()
    assert 'id="plan-build"' in conversation_js   # plan -> tickets restored 12 Nov 2026 (Paul); uses the existing build turn
    assert "databasesButton(" not in (STATIC / "planning" / "erd" / "mount.js").read_text()
    order_line = next(line for line in (STATIC / "planning" / "documents.js").read_text().splitlines()
                      if line.startswith("export const DOCUMENT_ORDER"))
    assert "'handover'" not in order_line and "'skills'" not in order_line
