"""Placing a library entry persists the plan into the PROJECT'S planning folder."""

import json

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes.development import planning as planning_routes


@pytest.fixture()
def client(monkeypatch, tmp_path):
    root = tmp_path / "project" / "planning"

    import distr.core.planning.service as service

    monkeypatch.setattr(service, "get_workspace", lambda workspace_id, **k: {"id": workspace_id, "root_path": str(root)})

    app = FastAPI()
    router = APIRouter()
    planning_routes.register_routes(router, templates=None)
    app.include_router(router, prefix="/api")
    return TestClient(app), root


def test_an_empty_plan_starts_empty(client):
    api, root = client
    body = api.get("/api/workflows/studio/plan-workspaces/1/plan").json()
    assert body["root"].endswith("planning")
    assert body["pages"] == 0


def test_dropping_a_page_template_creates_a_page(client):
    api, root = client
    body = api.post(
        "/api/workflows/studio/plan-workspaces/1/plan/place",
        json={"entry": "dashboard-page", "title": "Dashboard", "route": "/"},
    ).json()
    assert body["placed"] is True
    assert body["created_page"] == "dashboard"
    # persisted into the planning folder, and readable back
    assert (root / "sitemap.json").is_file()
    assert (root / "sitemap.wire").is_file()
    assert api.get("/api/workflows/studio/plan-workspaces/1/plan").json()["pages"] == 1


def test_dropping_a_widget_onto_a_page(client):
    api, root = client
    api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "settings-page", "title": "Settings"})
    body = api.post(
        "/api/workflows/studio/plan-workspaces/1/plan/place",
        json={"entry": "kpi-row", "page": "settings"},
    ).json()
    assert body["placed"] is True
    plan = api.get("/api/workflows/studio/plan-workspaces/1/plan").json()
    assert "kpi-row" in json.dumps(plan["sitemap"])


def test_the_same_widget_is_not_added_twice(client):
    api, _ = client
    api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "settings-page", "title": "Settings"})
    api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "kpi-row", "page": "settings"})
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "kpi-row", "page": "settings"}).json()
    assert body["placed"] is False
    assert "already" in body["error"]


def test_an_unknown_entry_is_refused(client):
    api, _ = client
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "no-such-thing"}).json()
    assert body["placed"] is False
    assert "not in the library" in body["error"]


def test_placing_onto_an_unknown_page_is_refused(client):
    api, _ = client
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "kpi-row", "page": "nope"}).json()
    assert body["placed"] is False
    assert "not in the plan" in body["error"]


def test_a_workspace_without_a_planning_folder_is_refused(client, monkeypatch):
    api, _ = client
    import distr.core.planning.service as service

    monkeypatch.setattr(service, "get_workspace", lambda workspace_id, **k: {"id": workspace_id, "root_path": ""})
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "kpi-row"}).json()
    assert body["placed"] is False
    assert "no planning folder" in body["error"]


# ------------------------------------------------------------------ tickets

def test_tickets_are_built_into_the_planning_folder(client):
    api, root = client
    api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "dashboard-page", "title": "Dashboard"})
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/tickets", json={}).json()

    assert body["built"] is True
    assert body["count"] > 0
    assert (root / "tickets" / "index.md").is_file()
    assert "`kpi-row`" in (root / "tickets" / "index.md").read_text()

    # and the plan route surfaces the index
    plan = api.get("/api/workflows/studio/plan-workspaces/1/plan").json()
    assert plan["tickets"].startswith("# Plan tickets")


def test_tickets_are_a_component_diff(client):
    api, _ = client
    api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "dashboard-page", "title": "Dashboard"})
    body = api.post(
        "/api/workflows/studio/plan-workspaces/1/plan/tickets",
        json={"current": {"dashboard": ["kpi-row", "app-shell", "data-table", "stale"]}},
    ).json()
    actions = {(t["action"], t["component"]) for t in body["tickets"]}
    assert ("remove", "stale") in actions
    # present and unchanged is nothing to do
    assert ("add", "kpi-row") not in actions
