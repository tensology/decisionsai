from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_harness_event_route_accepts_ambient_events_without_internal_token(monkeypatch):
    from distr.gui.web.server import create_app

    recorded = []

    def fake_record(payload):
        recorded.append(payload)
        return {"success": True, "attachment": "ambient", "project_id": None}

    monkeypatch.setattr("distr.core.harness_events.record_harness_event_silently", fake_record)

    client = TestClient(create_app())
    response = client.post(
        "/api/harness/events",
        json={
            "harness": "cursor",
            "event_type": "cursor_progress",
            "message": "Started a fresh chat against the project.",
            "project_folder": "/repo/DecisionsAI",
            "source": "ambient",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert recorded[0].harness == "cursor"
    assert recorded[0].project_folder == "/repo/DecisionsAI"


def test_reporter_fails_silently_when_decisions_is_off():
    script = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "codex-ide"
        / "scripts"
        / "report_decisions_event.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--api-base",
            "http://127.0.0.1:9",
            "--event-type",
            "codex_completed",
            "--message",
            "No DecisionsAI process is listening.",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
