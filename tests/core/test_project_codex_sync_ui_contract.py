"""Static UI/API contract checks for project Codex sync controls."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_project_details_exposes_codex_sync_controls():
    html = (ROOT / "distr/gui/web/templates/projects/tabs/details.html").read_text(encoding="utf-8")

    assert 'id="detail-coding-backend"' in html
    assert 'id="codex-sync-panel"' in html
    assert 'id="codex-sync-status"' in html
    assert 'id="codex-sync-btn"' in html
    assert "Codex integration" in html


def test_project_js_wires_codex_sync_endpoint_and_hides_active_button():
    js = (ROOT / "distr/gui/web/static/projects/js/projects.js").read_text(encoding="utf-8")

    assert '"/api/projects/" + projectId + "/codex-sync"' in js
    assert '"/api/projects/" + currentProjectId + "/codex-sync"' in js
    assert 'btn.className = active' in js
    assert '? "hidden"' in js
    assert 'loadCodexSync(project.id)' in js


def test_project_routes_expose_codex_sync_api():
    py = (ROOT / "distr/gui/web/routes/settings/projects.py").read_text(encoding="utf-8")

    assert '@router.get("/projects/{project_id}/codex-sync")' in py
    assert '@router.post("/projects/{project_id}/codex-sync")' in py
    assert 'project.coding_backend = "codex"' in py


def test_project_routes_install_codex_plugin_when_codex_backend_setup_runs():
    py = (ROOT / "distr/gui/web/routes/settings/projects.py").read_text(encoding="utf-8")

    assert "def _install_local_codex_plugin" in py
    assert 'Path.home() / "plugins" / "decisions-codex"' in py
    assert 'Path.home() / ".agents" / "plugins" / "marketplace.json"' in py
    assert 'if normalized_backend_id == "codex"' in py
    assert '"plugin_install": install' in py
    assert '"plugin_install": plugin_install' in py
