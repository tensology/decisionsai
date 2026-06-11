# Feature: kanban-cli-settings-restructure, Property 10: Hourly frequency validation rejects out-of-range hours
"""
Property 10: Hourly frequency validation rejects out-of-range hours

For any list of integers submitted as kanban_agent_hours via PUT /api/tickets/settings
where kanban_agent_frequency is 'hourly', if any integer is outside the range [0, 23],
the API should return a 422 status code. If all integers are within [0, 23], the API
should return a success response.

**Validates: Requirements 10.5, 10.6**
"""
import json
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.gui.web.routes.kanban import create_routes


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

    return app, mock_load, mock_save, settings_store


# ── Strategies ──

# Lists containing at least one out-of-range hour value
out_of_range_hours_st = st.lists(
    st.integers(min_value=-1000, max_value=1000),
    min_size=1,
    max_size=20,
).filter(lambda hrs: any(h < 0 or h > 23 for h in hrs))

# Lists where all values are valid hours [0, 23]
valid_hours_st = st.lists(
    st.integers(min_value=0, max_value=23),
    min_size=0,
    max_size=24,
)


class TestHourlyValidationProperty:
    """Property 10: Hourly frequency validation rejects out-of-range hours."""

    @given(hours=out_of_range_hours_st)
    @settings(max_examples=100, deadline=None)
    def test_out_of_range_hours_return_422(self, hours):
        """
        **Validates: Requirements 10.5, 10.6**

        For any list of integers where at least one value is outside [0, 23],
        PUT /api/tickets/settings should return a 422 status code.
        """
        app, mock_load, mock_save, store = _make_test_client()

        with patch("distr.gui.web.routes.kanban.load_settings_from_db", side_effect=mock_load), \
             patch("distr.gui.web.routes.kanban.save_settings_to_db", side_effect=mock_save):
            client = TestClient(app)
            resp = client.put("/api/tickets/settings", json={
                "kanban_agent_hours": hours,
            })

        assert resp.status_code == 422, (
            f"Expected 422 for out-of-range hours {hours}, got {resp.status_code}"
        )

    @given(hours=valid_hours_st)
    @settings(max_examples=100, deadline=None)
    def test_valid_hours_return_success(self, hours):
        """
        **Validates: Requirements 10.5, 10.6**

        For any list of integers where all values are within [0, 23],
        PUT /api/tickets/settings should return a success response (200).
        """
        app, mock_load, mock_save, store = _make_test_client()

        with patch("distr.gui.web.routes.kanban.load_settings_from_db", side_effect=mock_load), \
             patch("distr.gui.web.routes.kanban.save_settings_to_db", side_effect=mock_save):
            client = TestClient(app)
            resp = client.put("/api/tickets/settings", json={
                "kanban_agent_hours": hours,
            })

        assert resp.status_code == 200, (
            f"Expected 200 for valid hours {hours}, got {resp.status_code}"
        )
        assert resp.json()["success"] is True
