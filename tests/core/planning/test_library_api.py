"""Section E backend: the side panel's library, by screen."""

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes.development import planning as planning_routes


@pytest.fixture()
def client():
    app = FastAPI()
    router = APIRouter()
    planning_routes.register_routes(router, templates=None)
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_sitemap_level_offers_page_templates(client):
    body = client.get("/api/workflows/studio/plan-workspaces/1/library?screen=sitemap").json()
    kinds = {e["kind"] for e in body["entries"]}
    assert kinds == {"page-template"}
    assert body["screen"] == "sitemap"


def test_inside_a_page_offers_widgets(client):
    body = client.get("/api/workflows/studio/plan-workspaces/1/library?screen=page").json()
    kinds = {e["kind"] for e in body["entries"]}
    assert kinds == {"widget"}


def test_flow_level_offers_page_templates(client):
    body = client.get("/api/workflows/studio/plan-workspaces/1/library?screen=flow").json()
    assert {e["kind"] for e in body["entries"]} == {"page-template"}


def test_entries_carry_what_the_panel_needs(client):
    entry = client.get("/api/workflows/studio/plan-workspaces/1/library?screen=page").json()["entries"][0]
    for key in ("name", "kind", "title", "wire", "tags", "source"):
        assert key in entry


def test_an_explicit_kind_filter_wins(client):
    body = client.get("/api/workflows/studio/plan-workspaces/1/library?kind=base-template").json()
    assert {e["kind"] for e in body["entries"]} == {"base-template"}


def test_a_tag_filter_narrows_the_list(client):
    body = client.get("/api/workflows/studio/plan-workspaces/1/library?screen=page&tag=dashboard").json()
    assert [e["name"] for e in body["entries"]] == ["kpi-row"]


def test_an_unknown_workspace_still_returns_the_global_library(client):
    """A missing workspace must not empty the panel."""
    body = client.get("/api/workflows/studio/plan-workspaces/999999/library?screen=page").json()
    assert body["entries"]


def test_the_default_screen_is_a_page(client):
    body = client.get("/api/workflows/studio/plan-workspaces/1/library").json()
    assert body["screen"] == "page"
    assert {e["kind"] for e in body["entries"]} == {"widget"}
