"""Settings API: MCP config GET/POST (R7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

DECISIONS_AI_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = DECISIONS_AI_ROOT / "distr/gui/web/templates"


@pytest.fixture
def mcp_client(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.mcp.config.default_config_path",
        lambda: tmp_path / "mcp_config.json",
    )
    from distr.gui.web.routes.settings import create_routes

    app = FastAPI()
    app.include_router(create_routes(TEMPLATES_DIR), prefix="/api")
    return TestClient(app), tmp_path


def test_mcp_get_empty(mcp_client):
    client, _tmp = mcp_client
    r = client.get("/api/mcp")
    assert r.status_code == 200
    assert r.json() == {"servers": []}


def test_mcp_stdio_roundtrip(mcp_client):
    client, tmp = mcp_client
    body = {
        "servers": [
            {
                "name": "demo",
                "enabled": True,
                "transport": "stdio",
                "command": ["echo", "x"],
                "env": {"A": "b"},
            }
        ]
    }
    r = client.post("/api/mcp", json=body)
    assert r.status_code == 200
    assert r.json().get("success") is True
    saved = json.loads((tmp / "mcp_config.json").read_text(encoding="utf-8"))
    assert len(saved["servers"]) == 1
    assert saved["servers"][0]["name"] == "demo"
    r2 = client.get("/api/mcp")
    assert r2.status_code == 200
    rows = r2.json().get("servers") or []
    assert rows[0]["name"] == "demo"
    assert rows[0]["command"] == ["echo", "x"]
    assert rows[0]["env"] == {"A": "b"}


def test_mcp_post_invalid_stdio_missing_command(mcp_client):
    client, _tmp = mcp_client
    body = {"servers": [{"name": "bad", "transport": "stdio", "command": [], "enabled": True}]}
    r = client.post("/api/mcp", json=body)
    assert r.status_code == 400
    assert r.json().get("success") is False


def test_mcp_post_duplicate_names(mcp_client):
    client, _tmp = mcp_client
    body = {
        "servers": [
            {"name": "x", "transport": "stdio", "command": ["true"], "enabled": True},
            {"name": "x", "transport": "stdio", "command": ["true"], "enabled": True},
        ]
    }
    r = client.post("/api/mcp", json=body)
    assert r.status_code == 422
