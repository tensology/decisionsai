from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_harness_doctor_reports_pack_cli_projection_and_repair_state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project_root = tmp_path / "repo"
    (project_root / "scripts").mkdir(parents=True)

    _write(home / ".decisions" / "harness-pack-state.json", json.dumps({"status": "current"}))
    _write(home / ".decisions" / "harness" / "ecc-skills-registry.json", "[]")
    _write(home / ".decisions" / "harness" / "ecc-surface-manifest.json", "{}")
    _write(home / ".decisions" / "capabilities-pack-state.json", json.dumps({"status": "configured"}))
    _write(home / ".decisions" / "harness" / "capabilities-skills-registry.json", "[]")
    _write(home / ".decisions" / "harness" / "mcp-recommendations.json", "{}")
    _write(home / "plugins" / "decisions-codex" / "skills" / "ecc-harness-pack" / "SKILL.md")
    _write(home / "plugins" / "decisions-codex" / "skills" / "decisions-competition-harness" / "SKILL.md")
    _write(home / "plugins" / "decisions-codex" / "skills" / "decisions-browser-content-harness" / "SKILL.md")
    for skill_id in (
        "decisions-frontier-prep",
        "decisions-harness-audit",
        "decisions-harness-optimize",
        "codebase-design",
        "domain-modeling",
        "architecture-deepening-review",
    ):
        _write(home / ".codex" / "commands" / f"{skill_id}.md")

    monkeypatch.setattr(
        "distr.core.harness_doctor.detected_harnesses",
        lambda: {"codex": True, "claude": True, "cursor": False, "pi": False, "cline": False},
    )
    monkeypatch.setattr(
        "distr.core.harness_doctor.get_backend_statuses",
        lambda: {
            "backends": [
                {"id": "codex", "name": "Codex", "installed": True, "ready": True, "state": "ready"},
                {
                    "id": "claude_code",
                    "name": "Claude Code",
                    "installed": False,
                    "ready": False,
                    "state": "missing",
                    "setup_instructions": "Install Claude Code",
                },
            ]
        },
    )

    from distr.core.harness_doctor import assess_harness_stack

    report = assess_harness_stack(home=home, project_root=project_root)

    assert report["ok"] is False
    assert report["summary"]["ready"] >= 3
    assert report["summary"]["missing"] >= 1
    assert report["harnesses"]["codex"]["ready"] is True
    assert report["harnesses"]["codex"]["projections"]["ecc"]["ready"] is True
    assert report["harnesses"]["codex"]["projections"]["reference_skills"]["ready"] is True
    assert report["harnesses"]["claude"]["ready"] is False
    assert any("setup_project_clis.sh claude" in action["command"] for action in report["repair_actions"])


def test_harness_doctor_route_returns_report(monkeypatch, tmp_path):
    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr(
        "distr.core.harness_doctor.assess_harness_stack",
        lambda: {
            "ok": True,
            "summary": {"ready": 1, "missing": 0, "stale": 0, "total": 1},
            "harnesses": {"codex": {"ready": True}},
            "packs": {},
            "clis": {},
            "repair_actions": [],
        },
    )

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    client = TestClient(app)

    response = client.get("/api/projects/harness-doctor")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["report"]["ok"] is True
