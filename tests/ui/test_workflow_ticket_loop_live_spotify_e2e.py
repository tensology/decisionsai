"""Opt-in live backend-matrix E2E for the Spotify-remake workflow.

Run only when you intentionally want DecisionsAI to create/delete stamped
project folders under ~/development and dispatch work to configured CLIs:

  DECISIONS_RUN_LIVE_SPOTIFY_E2E=1 \
  DECISIONS_LIVE_SPOTIFY_BACKEND=all-ready \
  rtk python3 -m pytest -m "live_cli_matrix" \
    tests/ui/test_workflow_ticket_loop_live_spotify_e2e.py -q -s
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from scripts.workflow_ticket_loop_e2e import (
    DEFAULT_BASE_URL,
    WorkflowTicketLoopHarness,
    backend_status_map,
    select_backend_matrix,
)


pytestmark = [pytest.mark.e2e_playwright, pytest.mark.live_cli_matrix]


def test_live_spotify_backend_matrix_runs_selected_ready_backends() -> None:
    if os.environ.get("DECISIONS_RUN_LIVE_SPOTIFY_E2E") != "1":
        pytest.skip("Set DECISIONS_RUN_LIVE_SPOTIFY_E2E=1 to run destructive live CLI matrix.")

    harness = WorkflowTicketLoopHarness(DEFAULT_BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {DEFAULT_BASE_URL}")

    backend = os.environ.get("DECISIONS_LIVE_SPOTIFY_BACKEND", "all-ready")
    fail_on_unavailable = os.environ.get("DECISIONS_LIVE_SPOTIFY_FAIL_ON_UNAVAILABLE") == "1"
    matrix = select_backend_matrix(
        backend,
        statuses=backend_status_map(),
        fail_on_unavailable=fail_on_unavailable,
    )
    if not matrix.selected:
        pytest.skip(f"No ready backend selected; skipped={matrix.skipped}")

    stamp = os.environ.get("DECISIONS_LIVE_SPOTIFY_STAMP") or time.strftime("%Y%m%d-%H%M%S")
    development_root = Path(os.environ.get("DECISIONS_LIVE_SPOTIFY_DEVELOPMENT_ROOT", str(Path.home() / "development")))

    reports = []
    for backend_id in matrix.selected:
        reports.append(
            harness.run_live_spotify_backend(
                backend_id=backend_id,
                stamp=stamp,
                development_root=development_root,
                cleanup=True,
            )
        )

    assert [report["backend_id"] for report in reports] == matrix.selected
    assert all(len(report["tickets"]) == 4 for report in reports)
