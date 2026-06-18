"""Live Spotify program E2E: one project, one board, ideation → dev → polish workflows."""

from __future__ import annotations

import pytest

from scripts.workflow_ticket_loop_e2e import (
    SPOTIFY_DEVELOPMENT_PRESET,
    SPOTIFY_IDEATION_PRESET,
    SPOTIFY_POLISH_PRESET,
    WorkflowTicketLoopHarness,
    build_spotify_ticket_specs,
)

BASE_URL = "http://127.0.0.1:8765"


@pytest.mark.e2e
def test_spotify_program_seed_has_one_ecosystem(tmp_path) -> None:
    harness = WorkflowTicketLoopHarness(BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {BASE_URL}")

    fixture = harness.seed_spotify_program(tmp_path)
    harness.assert_spotify_ideation_artifacts(fixture)

    ideation = harness.api_request(f"/workflows/{int(fixture['ideation_workflow_id'])}")
    development = harness.api_request(f"/workflows/{int(fixture['development_workflow_id'])}")
    polish = harness.api_request(f"/workflows/{int(fixture['polish_workflow_id'])}")
    assert len(ideation.get("steps") or []) >= 3
    assert len(development.get("steps") or []) >= 3
    assert len(polish.get("steps") or []) >= 3

    workflows = harness.api_request("/workflows?limit=200&search=%5Be2e%5D%20spotify-remake")
    spotify_workflows = [row for row in (workflows or []) if "spotify-remake" in str(row.get("name") or "")]
    assert len(spotify_workflows) == 3


@pytest.mark.e2e
def test_spotify_program_chain_development_and_polish(tmp_path) -> None:
    harness = WorkflowTicketLoopHarness(BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {BASE_URL}")

    result = harness.run_spotify_program_chain(tmp_path)
    fixture = result["fixture"]
    dev = result["development"]
    polish = result["polish"]

    assert dev["summary"]["run_status"] == "completed"
    assert polish is not None
    assert polish["summary"]["run_status"] == "completed"

    events = harness.api_request(
        f"/workflows/{int(fixture['development_workflow_id'])}/orchestrator-events?limit=80"
    )
    event_types = {str(event.get("event_type") or "") for event in events}
    assert "workflow_step_completed" in event_types
    assert dev["summary"].get("green_seen") or dev["summary"].get("completed_seen")


@pytest.mark.e2e
def test_spotify_program_presets_match_expected_slugs(tmp_path) -> None:
    harness = WorkflowTicketLoopHarness(BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {BASE_URL}")

    presets = harness.api_request("/workflows/loop-presets")
    slugs = {str(row.get("slug") or "") for row in (presets.get("presets") or [])}
    assert SPOTIFY_IDEATION_PRESET in slugs
    assert SPOTIFY_DEVELOPMENT_PRESET in slugs
    assert SPOTIFY_POLISH_PRESET in slugs

    fixture = harness.seed_spotify_program(tmp_path)
    expected_titles = [spec.title for spec in build_spotify_ticket_specs()[:3]]
    assert [t["title"] for t in fixture["tickets"]] == expected_titles
