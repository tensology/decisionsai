"""The ambient-suggestion API: list, dismiss, mute, unmute."""

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from distr.core.initiative import suggestion_store as store
from distr.gui.web.routes.settings import initiative as initiative_routes


@pytest.fixture()
def client(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setattr(store, "engine", engine)
    store.ensure_tables()

    app = FastAPI()
    router = APIRouter()
    initiative_routes.register_routes(router, templates=None)
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_lists_suggestions_newest_first(client):
    store.add(kind="idea", text="First", confidence=0.6)
    store.add(kind="risk", text="Second", confidence=0.9)
    body = client.get("/api/initiative/suggestions").json()
    assert [s["text"] for s in body["suggestions"]] == ["Second", "First"]
    assert body["muted_kinds"] == []


def test_list_is_empty_when_there_is_nothing(client):
    body = client.get("/api/initiative/suggestions").json()
    assert body == {"suggestions": [], "muted_kinds": []}


def test_dismiss_removes_it_from_the_list(client):
    sid = store.add(kind="idea", text="dismiss me", confidence=0.7)
    assert client.post(f"/api/initiative/suggestions/{sid}/dismiss").json()["success"] is True
    assert client.get("/api/initiative/suggestions").json()["suggestions"] == []
    # dismissing again is a 404, not a crash
    assert client.post(f"/api/initiative/suggestions/{sid}/dismiss").status_code == 404


def test_mute_then_unmute(client):
    assert client.post("/api/initiative/suggestions/mute", json={"kind": "naming"}).json()["success"] is True
    assert client.get("/api/initiative/suggestions").json()["muted_kinds"] == ["naming"]
    assert store.is_muted("naming") is True

    body = client.post("/api/initiative/suggestions/unmute", json={"kind": "naming"}).json()
    assert body["success"] is True
    assert body["muted_kinds"] == []
    assert store.is_muted("naming") is False


def test_mute_requires_a_kind(client):
    assert client.post("/api/initiative/suggestions/mute", json={"kind": "   "}).status_code == 400
    assert client.post("/api/initiative/suggestions/unmute", json={"kind": ""}).status_code == 400


def test_include_dismissed_is_opt_in(client):
    sid = store.add(kind="idea", text="gone", confidence=0.5)
    store.dismiss(sid)
    assert client.get("/api/initiative/suggestions").json()["suggestions"] == []
    rows = client.get("/api/initiative/suggestions?include_dismissed=true").json()["suggestions"]
    assert [r["text"] for r in rows] == ["gone"]


def test_a_huge_limit_is_clamped(client):
    for i in range(5):
        store.add(kind="idea", text=f"idea {i}", confidence=0.5)
    body = client.get("/api/initiative/suggestions?limit=999999999").json()
    assert len(body["suggestions"]) == 5  # clamped, not an error


def test_list_survives_a_broken_store(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(store, "recent", boom)
    assert client.get("/api/initiative/suggestions").json()["suggestions"] == []
