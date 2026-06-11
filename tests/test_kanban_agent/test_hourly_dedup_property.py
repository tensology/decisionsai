# Feature: kanban-cli-settings-restructure, Property 4: Hourly settings deduplication on save
"""
Property 4: Hourly settings deduplication on save

For any list of hour integers (possibly containing duplicates), after saving via the
PUT /api/tickets/settings endpoint and loading back via GET, the returned
kanban_agent_hours list should contain no duplicate values and should be a subset of
the original values (preserving only unique entries in [0, 23]).

**Validates: Requirements 1.5**
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

# Lists of valid hours [0, 23] that may contain duplicates
hours_with_possible_duplicates_st = st.lists(
    st.integers(min_value=0, max_value=23),
    min_size=0,
    max_size=48,
)


class TestHourlyDeduplicationProperty:
    """Property 4: Hourly settings deduplication on save."""

    @given(hours=hours_with_possible_duplicates_st)
    @settings(max_examples=100, deadline=None)
    def test_saved_hours_contain_no_duplicates_and_are_subset(self, hours):
        """
        **Validates: Requirements 1.5**

        For any list of valid hour integers (possibly containing duplicates),
        after saving via PUT /api/tickets/settings and loading back via GET,
        the returned kanban_agent_hours list should contain no duplicate values
        and should be a subset of the original values.
        """
        app, mock_load, mock_save, store = _make_test_client()

        with patch("distr.gui.web.routes.kanban.load_settings_from_db", side_effect=mock_load), \
             patch("distr.gui.web.routes.kanban.save_settings_to_db", side_effect=mock_save):
            client = TestClient(app)

            # Save hours via PUT
            put_resp = client.put("/api/tickets/settings", json={
                "kanban_agent_hours": hours,
            })
            assert put_resp.status_code == 200, (
                f"Expected 200 for valid hours {hours}, got {put_resp.status_code}"
            )

            # Load back via GET
            get_resp = client.get("/api/tickets/settings")
            assert get_resp.status_code == 200

        returned_hours = get_resp.json()["kanban_agent_hours"]

        # No duplicates
        assert len(returned_hours) == len(set(returned_hours)), (
            f"Returned hours contain duplicates: {returned_hours}"
        )

        # Subset of original values
        assert set(returned_hours) <= set(hours), (
            f"Returned hours {returned_hours} are not a subset of original {hours}"
        )

        # All returned values are valid hours [0, 23]
        assert all(0 <= h <= 23 for h in returned_hours), (
            f"Returned hours contain out-of-range values: {returned_hours}"
        )
