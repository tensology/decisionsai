"""Placing a library entry writes into the plan the canvas already reads."""

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes.development import planning as planning_routes

FLOWS = '''screen "Overview" device=desktop route="/"
  heading "Overview"

screen "Locale" device=desktop route="/locale"
  heading "Locale"
'''


@pytest.fixture()
def client(monkeypatch, tmp_path):
    root = tmp_path / "project" / "planning"
    state = {"content": FLOWS, "revision": 1}

    import distr.core.planning.service as service

    monkeypatch.setattr(service, "get_workspace", lambda workspace_id, **k: {
        "id": workspace_id,
        "root_path": str(root),
        "items": [{
            "id": 7, "item_type": "flows", "content": state["content"],
            "content_format": "wire", "revision_count": state["revision"],
        }],
    })
    monkeypatch.setattr(
        service, "update_item",
        lambda item_id, *, content=None, expected_revision=None, source="": (
            state.update(content=content, revision=int(expected_revision or 1) + 1)
            or {"id": item_id, "content": content}
        ),
    )

    app = FastAPI()
    router = APIRouter()
    planning_routes.register_routes(router, templates=None)
    app.include_router(router, prefix="/api")
    return TestClient(app), root, state


def test_the_plan_lists_the_canvas_pages(client):
    api, _, _ = client
    body = api.get("/api/workflows/studio/plan-workspaces/1/plan").json()
    assert [p["name"] for p in body["pages"]] == ["overview", "locale"]
    assert body["count"] == 2
    assert body["baseCss"]                      # the base template travels with the plan


def test_dropping_a_page_template_adds_a_page_to_the_plan(client):
    api, _, state = client
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/place",
                    json={"entry": "dashboard-page", "title": "Dashboard"}).json()
    assert body["placed"] is True
    assert body["created_page"] == "dashboard"
    assert 'screen "Dashboard"' in state["content"]
    assert body["pages"][-1]["name"] == "dashboard"


def test_dropping_a_widget_lands_inside_the_named_page(client):
    api, _, state = client
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/place",
                    json={"entry": "kpi-row", "page": "locale"}).json()
    assert body["placed"] is True
    locale_block = state["content"].split('screen "Locale"')[1]
    assert "Total" in locale_block


def test_the_same_widget_is_not_added_twice(client):
    api, _, _ = client
    api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "kpi-row", "page": "locale"})
    again = api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "kpi-row", "page": "locale"}).json()
    assert again["placed"] is False
    assert "already" in again["error"]


def test_an_unknown_entry_is_refused(client):
    api, _, _ = client
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/place", json={"entry": "no-such-thing"}).json()
    assert body["placed"] is False
    assert "not in the library" in body["error"]


def test_placing_onto_an_unknown_page_is_refused(client):
    api, _, _ = client
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/place",
                    json={"entry": "kpi-row", "page": "nope"}).json()
    assert body["placed"] is False
    assert "no page called" in body["error"]


def test_a_plan_with_no_flows_item_is_refused(client, monkeypatch):
    api, _, _ = client
    import distr.core.planning.service as service

    monkeypatch.setattr(service, "get_workspace", lambda workspace_id, **k: {"id": workspace_id, "items": []})
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/place",
                    json={"entry": "dashboard-page", "title": "D"}).json()
    assert body["placed"] is False
    assert "no flows item" in body["error"]


# ------------------------------------------------------------------ tickets

def test_tickets_are_built_into_the_planning_folder(client):
    api, root, _ = client
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/tickets", json={}).json()
    assert body["built"] is True
    assert (root / "tickets" / "index.md").is_file()
    assert api.get("/api/workflows/studio/plan-workspaces/1/plan").json()["tickets"].startswith("# Plan tickets")


def test_tickets_are_a_component_diff(client):
    api, _, _ = client
    body = api.post("/api/workflows/studio/plan-workspaces/1/plan/tickets",
                    json={"current": {"locale": ["kpi-row", "stale"]}}).json()
    actions = {(t["action"], t["component"]) for t in body["tickets"]}
    assert ("remove", "stale") in actions
