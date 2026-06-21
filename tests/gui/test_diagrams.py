"""Tests for Mermaid diagram storage API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes import diagrams as diagrams_mod

DECISIONS_AI_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = DECISIONS_AI_ROOT / "distr/gui/web/templates"


@pytest.fixture(autouse=True)
def _isolate_diagram_history(monkeypatch, tmp_path):
    monkeypatch.setattr(diagrams_mod, "_HISTORY_FILE", tmp_path / "mermaid-history.json")
    with diagrams_mod._diagram_lock:
        diagrams_mod._diagram_store.clear()
    diagrams_mod._save_history_rows([])


def _build_diagrams_client() -> TestClient:
    app = FastAPI()
    app.include_router(diagrams_mod.create_routes(TEMPLATES_DIR), prefix="/api")
    return TestClient(app)


def test_store_and_get_diagram_roundtrip():
    diagram_id = diagrams_mod.store_diagram("flowchart TD\n  A-->B", title="Test flow")
    row = diagrams_mod.get_diagram(diagram_id)
    assert row is not None
    assert row["title"] == "Test flow"
    assert "A-->B" in row["code"]


def test_diagram_history_persisted():
    diagram_id = diagrams_mod.store_diagram("sequenceDiagram\n  A->>B: hi", title="History test")
    history = diagrams_mod.list_diagram_history()
    assert any(item.get("id") == diagram_id for item in history)


def test_expired_diagram_falls_back_to_history():
    diagram_id = diagrams_mod.store_diagram("flowchart LR\n  X-->Y", title="Expire me")
    with diagrams_mod._diagram_lock:
        diagrams_mod._diagram_store[diagram_id]["expires_at"] = 0
        diagrams_mod._purge_expired()
    row = diagrams_mod.get_diagram(diagram_id)
    assert row is not None
    assert row["title"] == "Expire me"


def test_delete_missing_diagram_returns_404():
    client = _build_diagrams_client()
    response = client.delete("/api/diagrams/not-a-real-diagram-id")
    assert response.status_code == 404


def test_delete_diagram_removes_memory_and_history():
    diagram_id = diagrams_mod.store_diagram("flowchart TD\n  A-->B", title="Delete me")
    assert diagrams_mod.delete_diagram_entry(diagram_id) is True
    assert diagrams_mod.get_diagram(diagram_id) is None
    assert diagrams_mod._load_history_rows() == []
    assert diagrams_mod._diagram_store == {}


def test_delete_diagram_endpoint_updates_history():
    client = _build_diagrams_client()
    diagram_id = diagrams_mod.store_diagram("sequenceDiagram\n  A->>B: x", title="To delete")

    delete_response = client.delete(f"/api/diagrams/{diagram_id}")
    assert delete_response.status_code == 200
    assert delete_response.json().get("success") is True

    list_response = client.get("/api/diagrams/history")
    assert diagram_id not in [item.get("id") for item in (list_response.json().get("items") or [])]

    get_response = client.get(f"/api/diagrams/{diagram_id}")
    assert get_response.status_code == 404
