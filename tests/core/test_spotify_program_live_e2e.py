"""Live Spotify program E2E: one project, one board, ideation → dev → polish workflows."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.workflow_ticket_loop_e2e import (
    SPOTIFY_DEVELOPMENT_PRESET,
    SPOTIFY_IDEATION_PRESET,
    SPOTIFY_POLISH_PRESET,
    WorkflowTicketLoopHarness,
    build_spotify_ticket_specs,
    select_spotify_ticket_specs,
)

BASE_URL = "http://127.0.0.1:8765"


def test_live_ticket_limit_scopes_fixture_before_queue_creation() -> None:
    assert [row.sequence for row in select_spotify_ticket_specs(1)] == [1]
    assert [row.sequence for row in select_spotify_ticket_specs(99)] == [1, 2, 3, 4]
    assert [row.sequence for row in select_spotify_ticket_specs(0)] == [1]


def test_live_lane_mapping_uses_server_api(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = WorkflowTicketLoopHarness(BASE_URL)
    calls: list[str] = []

    def request(path: str, **_kwargs: object) -> dict[str, object]:
        calls.append(path)
        return {
            "id": 41,
            "lanes": [
                {"id": 101, "name": "Backlog"},
                {"id": 102, "name": "Current"},
                {"id": 103, "name": "QA / Assess"},
                {"id": 104, "name": "Done"},
            ],
        }

    monkeypatch.setattr(harness, "api_request", request)

    lanes = harness._set_spotify_lanes(41)

    assert calls == ["/tickets/boards/41"]
    assert lanes == {
        "Backlog": 101,
        "Ready": 102,
        "In Progress": 102,
        "Validation": 103,
        "Improve": 103,
        "Complete": 104,
    }


def test_live_fixture_cleanup_uses_server_api_and_removes_disposable_folder(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = WorkflowTicketLoopHarness(BASE_URL)
    project_dir = tmp_path / "spotify-remake-e2e-codex-cleanup"
    project_dir.mkdir()
    calls: list[str] = []
    monkeypatch.setattr(harness, "_best_effort_api_delete", calls.append)

    harness.cleanup_live_spotify_fixture(
        {
            "tickets": [{"id": 7}, {"id": 8}],
            "board_id": 9,
            "workflow_id": 10,
            "project_id": 11,
            "project_dir": str(project_dir),
        },
        development_root=tmp_path,
    )

    assert calls == [
        "/tickets/tickets/7",
        "/tickets/tickets/8",
        "/tickets/boards/9",
        "/workflows/10",
        "/projects/11",
    ]
    assert not project_dir.exists()


@pytest.mark.only_browser
def test_spotify_fixture_ui_proof_captures_real_browser_evidence(tmp_path) -> None:
    harness = WorkflowTicketLoopHarness(BASE_URL)
    harness.scaffold_spotify_project(tmp_path)
    (tmp_path / "index.html").write_text(
        """<!doctype html><html><body>
        <a href="#/search">Search</a>
        <button type="button" aria-label="Play track">Start</button>
        </body></html>""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts" / "spotify_ui_proof.py"),
            "--project-root",
            str(tmp_path),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "artifacts" / "ui-after.png").is_file()
    assert "After screenshot:" in completed.stdout
    assert "1. [click] Open Search navigation" in completed.stdout
    assert "2. [click] Start playback" in completed.stdout


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
