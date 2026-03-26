"""
Unit tests for the global kanban settings GET/PUT API endpoints.

Tests cover:
1. GET /kanban/settings returns kanban-prefixed settings
2. PUT /kanban/settings updates settings and returns success
3. PUT /kanban/settings validates hours in [0, 23]
4. PUT /kanban/settings validates days in [0, 6]
5. PUT /kanban/settings validates monthly_day in [1, 28]
6. PUT /kanban/settings validates frequency in allowed values
7. PUT /kanban/settings deduplicates hours before saving
8. PUT /kanban/settings returns 422 for invalid values

**Validates: Requirements 10.1, 10.2, 10.5, 10.6**
"""
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes.kanban import create_routes, KanbanSettingsUpdate


# Default kanban settings used in tests
DEFAULT_KANBAN_SETTINGS = {
    "kanban_agent_enabled": False,
    "kanban_agent_frequency": "daily",
    "kanban_agent_time": "09:00",
    "kanban_agent_hours": "[]",
    "kanban_agent_days": "[]",
    "kanban_agent_monthly_day": 1,
    "kanban_agent_source_lane": "",
    "kanban_agent_done_lane": "",
    "kanban_agent_orchestrator_provider": "",
    "kanban_agent_orchestrator_model": "",
    "kanban_agent_coder_provider": "",
    "kanban_agent_coder_model": "",
    "kanban_agent_sub_provider": "",
    "kanban_agent_sub_model": "",
    "kanban_cli_tool": "",
    "kanban_cli_auth": "",
}


def _make_test_client(initial_settings=None):
    """Create a FastAPI test client with mocked settings."""
    settings_store = dict(initial_settings or DEFAULT_KANBAN_SETTINGS)

    def mock_load():
        return dict(settings_store)

    def mock_save(s):
        settings_store.clear()
        settings_store.update(s)

    app = FastAPI()
    with patch("distr.gui.web.routes.kanban.load_settings_from_db", side_effect=mock_load), \
         patch("distr.gui.web.routes.kanban.save_settings_to_db", side_effect=mock_save):
        router = create_routes()
    app.include_router(router, prefix="/api")

    # We need to keep the patches active during requests too
    client_patches = {
        "load": mock_load,
        "save": mock_save,
        "store": settings_store,
    }
    return app, client_patches


@pytest.fixture
def client():
    """Provide a test client with mocked settings persistence."""
    settings_store = dict(DEFAULT_KANBAN_SETTINGS)

    def mock_load():
        return dict(settings_store)

    def mock_save(s):
        settings_store.clear()
        settings_store.update(s)

    app = FastAPI()
    with patch("distr.gui.web.routes.kanban.load_settings_from_db", side_effect=mock_load), \
         patch("distr.gui.web.routes.kanban.save_settings_to_db", side_effect=mock_save):
        router = create_routes()
    app.include_router(router, prefix="/api")

    with patch("distr.gui.web.routes.kanban.load_settings_from_db", side_effect=mock_load), \
         patch("distr.gui.web.routes.kanban.save_settings_to_db", side_effect=mock_save):
        yield TestClient(app), settings_store


class TestGetKanbanSettings:
    """GET /kanban/settings returns kanban-prefixed global settings.

    **Validates: Requirements 10.1**
    """

    def test_returns_kanban_settings(self, client):
        test_client, store = client
        resp = test_client.get("/api/kanban/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert all(k.startswith("kanban_") for k in data.keys())
        assert data["kanban_agent_enabled"] is False
        assert data["kanban_agent_frequency"] == "daily"

    def test_parses_json_list_fields(self, client):
        test_client, store = client
        store["kanban_agent_hours"] = "[1, 5, 10]"
        store["kanban_agent_days"] = "[0, 3]"
        resp = test_client.get("/api/kanban/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kanban_agent_hours"] == [1, 5, 10]
        assert data["kanban_agent_days"] == [0, 3]

    def test_excludes_non_kanban_keys(self, client):
        test_client, store = client
        store["openai_key"] = "sk-test"
        resp = test_client.get("/api/kanban/settings")
        data = resp.json()
        assert "openai_key" not in data


class TestPutKanbanSettings:
    """PUT /kanban/settings updates global kanban settings.

    **Validates: Requirements 10.2**
    """

    def test_update_basic_fields(self, client):
        test_client, store = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_enabled": True,
            "kanban_agent_source_lane": "Backlog",
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert store["kanban_agent_enabled"] is True
        assert store["kanban_agent_source_lane"] == "Backlog"

    def test_partial_update_preserves_other_fields(self, client):
        test_client, store = client
        store["kanban_agent_frequency"] = "weekly"
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_enabled": True,
        })
        assert resp.status_code == 200
        assert store["kanban_agent_frequency"] == "weekly"


class TestPutKanbanSettingsValidation:
    """PUT /kanban/settings validates input and returns 422 for invalid values.

    **Validates: Requirements 10.5, 10.6**
    """

    def test_invalid_frequency_returns_422(self, client):
        test_client, _ = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_frequency": "biweekly",
        })
        assert resp.status_code == 422

    def test_valid_frequency_accepted(self, client):
        test_client, store = client
        for freq in ["hourly", "daily", "weekly", "fortnightly", "monthly"]:
            resp = test_client.put("/api/kanban/settings", json={
                "kanban_agent_frequency": freq,
            })
            assert resp.status_code == 200
            assert store["kanban_agent_frequency"] == freq

    def test_hours_out_of_range_returns_422(self, client):
        test_client, _ = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_hours": [0, 24],
        })
        assert resp.status_code == 422

    def test_hours_negative_returns_422(self, client):
        test_client, _ = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_hours": [-1, 5],
        })
        assert resp.status_code == 422

    def test_valid_hours_accepted(self, client):
        test_client, store = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_hours": [0, 12, 23],
        })
        assert resp.status_code == 200
        assert json.loads(store["kanban_agent_hours"]) == [0, 12, 23]

    def test_days_out_of_range_returns_422(self, client):
        test_client, _ = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_days": [0, 7],
        })
        assert resp.status_code == 422

    def test_valid_days_accepted(self, client):
        test_client, store = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_days": [0, 3, 6],
        })
        assert resp.status_code == 200
        assert json.loads(store["kanban_agent_days"]) == [0, 3, 6]

    def test_monthly_day_below_range_returns_422(self, client):
        test_client, _ = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_monthly_day": 0,
        })
        assert resp.status_code == 422

    def test_monthly_day_above_range_returns_422(self, client):
        test_client, _ = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_monthly_day": 29,
        })
        assert resp.status_code == 422

    def test_valid_monthly_day_accepted(self, client):
        test_client, store = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_monthly_day": 15,
        })
        assert resp.status_code == 200
        assert store["kanban_agent_monthly_day"] == 15


class TestPutKanbanSettingsDeduplication:
    """PUT /kanban/settings deduplicates hours before saving.

    **Validates: Requirements 1.5**
    """

    def test_duplicate_hours_are_deduplicated(self, client):
        test_client, store = client
        resp = test_client.put("/api/kanban/settings", json={
            "kanban_agent_hours": [9, 9, 12, 12, 15],
        })
        assert resp.status_code == 200
        saved_hours = json.loads(store["kanban_agent_hours"])
        assert saved_hours == [9, 12, 15]
        assert len(saved_hours) == len(set(saved_hours))
