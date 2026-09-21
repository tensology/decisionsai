"""Real HTTP routes, services and storage against a temporary project database."""
import json

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.planning import assets, conversation, service
from distr.gui.web.routes.development.planning import register_routes
from tests.core.test_planning_workspace import plan_workspace_db


@pytest.fixture
def plan_http(plan_workspace_db, monkeypatch, tmp_path):
    monkeypatch.setattr(conversation, "get_session", service.get_session)
    monkeypatch.setattr(assets, "get_session", service.get_session)
    monkeypatch.setattr(assets, "UPLOAD_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(conversation, "load_settings_from_db", lambda: {})
    monkeypatch.setattr(conversation.runtime, "create_stream",
        lambda provider, model, messages, settings, **scope:
            conversation.llm_factory.create_stream(provider, model, messages, settings))
    plan = service.ensure_workspace(board_key="decisions:9", board_provider="decisions",
                                    board_name="HTTP plan", project_id=plan_workspace_db["project_id"])
    app = FastAPI()
    router = APIRouter(prefix="/api")
    register_routes(router, None)
    app.include_router(router)
    with TestClient(app) as client:
        yield client, f"/api/workflows/studio/plan-workspaces/{plan['id']}"


def test_http_prompt_reads_uploaded_source_and_persists_artifact(plan_http, monkeypatch):
    client, base = plan_http
    upload = client.post(base + "/attachments", files={"file": ("requirements.md", b"Customer email is mandatory", "text/markdown")})
    assert upload.status_code == 200
    attachment = upload.json()
    assert client.get(attachment["url"]).content == b"Customer email is mandatory"
    calls = []
    def stream(provider, model, messages, settings):
        calls.append((provider, model, messages))
        yield json.dumps({"reply": "Recorded the email requirement", "edits": [
            {"item_type": "frac", "title": "Customer behavior", "content": "FR-001: Require customer email.", "content_format": "markdown"}]})
    monkeypatch.setattr(conversation.llm_factory, "create_stream", stream)
    response = client.post(base + "/messages", json={"message": "Record this requirement", "provider": "ollama",
                           "model_name": "selected-model", "attachments": [attachment["id"]], "tab": "requirements"})
    assert response.status_code == 200
    assert response.json()["status"] == "complete"
    assert calls[0][:2] == ("ollama", "selected-model")
    assert "Customer email is mandatory" in json.dumps(calls[0][2])
    detail = client.get(base).json()
    assert detail["items"][0]["content"] == "FR-001: Require customer email."
    messages = client.get(base + "/conversation").json()["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[-1]["artifacts"] == [detail["items"][0]["id"]]


def test_http_build_questions_are_not_reported_as_created_tasks(plan_http, monkeypatch):
    client, base = plan_http
    monkeypatch.setattr(conversation.llm_factory, "create_stream", lambda *args: iter([
        json.dumps({"reply": "", "edits": [], "tasks": [], "questions": ["Does this project need persistent data?"]})]))
    response = client.post(base + "/build", json={"provider": "ollama", "model_name": "selected-model"})
    assert response.status_code == 200
    assert response.json()["status"] == "needs_input"
    assert "created" not in response.json()
    assert "persistent data" in client.get(base + "/conversation").json()["messages"][-1]["content"]


def test_http_rejects_missing_model_before_creating_conversation(plan_http):
    client, base = plan_http
    assert client.post(base + "/messages", json={"message": "Build something", "provider": "ollama"}).status_code == 422
    assert client.get(base + "/conversation").json()["chat_id"] is None


def test_http_reports_saved_but_unsynced_artifact(plan_http, monkeypatch):
    client, base = plan_http
    monkeypatch.setattr(service, "_finish_file_projection", lambda item_id: "Disk write failed")
    monkeypatch.setattr(conversation.llm_factory, "create_stream", lambda *args: iter([json.dumps({
        "reply": "Recorded", "edits": [{"item_type": "frac", "title": "Requirements", "content": "FR-001", "content_format": "markdown"}]})]))
    response = client.post(base + "/messages", json={"message": "Record FR-001", "provider": "ollama", "model_name": "fixture-model"})
    assert response.status_code == 200
    assert response.json()["status"] == "sync_pending"
    assert "file synchronization is pending" in response.json()["messages"][-1]["content"]
    assert client.get(base).json()["items"][0]["content"] == "FR-001"


def test_http_build_creates_scoped_hierarchy_and_reuses_tickets(plan_http, monkeypatch):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, ProjectExecutionSession
    from distr.core.planning import build
    client, base = plan_http
    plan = client.get(base).json()
    monkeypatch.setattr(build, "get_session", service.get_session)
    monkeypatch.setattr(build, "_global_complexity_route", lambda level: {
        "backend": "pi", "model": "local-test-model", "model_provider": "ollama"})
    with service.get_session() as db:
        db.add(KanbanBoard(id=9, name="HTTP plan", default_project_id=plan["project_id"]))
        db.add(KanbanLane(board_id=9, name="Backlog"))
        db.commit()
    source = service.create_item(workspace_id=plan["id"], item_type="frac", title="Customer behavior",
                                 content="FR-001: Save a customer with valid email and show confirmation.")
    common = {"description": "Implement FR-001", "source_item_ids": [source["id"]],
              "acceptance_criteria": ["Valid email saves; invalid email does not"],
              "skills": ["e2e-testing"], "complexity": "medium", "role": "implementation"}
    tasks = [{**common, "key": "customer", "title": "Save customer", "parent_key": None, "depends_on": []},
             {**common, "key": "customer-test", "title": "Test customer save", "parent_key": "customer",
              "depends_on": ["customer"], "role": "testing"}]
    calls = []
    def stream(*args):
        calls.append(args)
        yield json.dumps({"reply": "Tasks generated", "edits": [], "tasks": tasks})
    monkeypatch.setattr(conversation.llm_factory, "create_stream", stream)
    first = client.post(base + "/build", json={"provider": "ollama", "model_name": "selected-model"}).json()
    assert first["created"] == 2
    parent, child = first["tasks"]
    assert child["parent_id"] == parent["id"]
    assert child["depends_on"] == [parent["id"]]
    assert child["route"]["step_role"] == "testing"
    second = client.post(base + "/build", json={"provider": "ollama", "model_name": "selected-model"}).json()
    assert second["created"] == 0 and second["reused"] == 2
    assert len(calls) == 1
    with service.get_session() as db:
        assert db.query(KanbanTicket).count() == 2
        assert db.query(ProjectExecutionSession).count() == 0


def test_http_scan_rebuilds_plan_from_linked_project(plan_http, plan_workspace_db):
    folder = plan_workspace_db["folder"]
    src = folder / "frontend" / "src"
    src.mkdir(parents=True)
    (src / "routes.jsx").write_text(
        'export const routes = [{ url: "/", name: "home" }, { url: "/fixtures", name: "fixtures" }, { url: "/match/:id", name: "match" }];\n',
        encoding="utf-8",
    )
    models = folder / "backend" / "apps" / "sport" / "models.py"
    models.parent.mkdir(parents=True)
    models.write_text(
        "from django.db import models\n\nclass Team(models.Model):\n    name = models.CharField(max_length=80)\n\nclass Fixture(models.Model):\n    home = models.ForeignKey(Team, on_delete=models.CASCADE)\n",
        encoding="utf-8",
    )
    client, base = plan_http
    service.ensure_plan_scaffold(int(base.rsplit("/", 1)[-1]))
    response = client.post(base + "/scan")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["action"] == "scanned"
    assert payload["summary"]["route_count"] >= 2
    assert payload["summary"]["model_count"] >= 2
    by_type = {item["item_type"]: item for item in payload["workspace"]["items"]}
    assert "/fixtures" in by_type["prd"]["content"]
    assert "Fixture" in by_type["architecture"]["content"]
    assert 'route="/fixtures"' in by_type["flows"]["content"]
    assert not by_type["flows"].get("is_starter")
