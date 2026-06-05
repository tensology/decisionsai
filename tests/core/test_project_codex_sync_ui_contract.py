"""Static UI/API contract checks for project implementation routing controls."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_project_details_points_to_global_complexity_routing():
    html = (ROOT / "distr/gui/web/templates/projects/tabs/details.html").read_text(encoding="utf-8")

    assert 'id="detail-coding-backend"' not in html
    assert 'id="codex-sync-panel"' not in html
    assert "Implementation routing" in html
    assert "Settings → LLMs" in html
    assert 'href="/settings/#llms"' in html


def test_project_js_does_not_save_project_coding_backend_from_details():
    js = (ROOT / "distr/gui/web/static/projects/js/projects.js").read_text(encoding="utf-8")

    assert 'loadCodexSync(project.id)' not in js
    assert 'coding_backend: (document.getElementById("detail-coding-backend")' not in js
    assert 'terminal-backend-select' in js


def test_project_routes_expose_codex_sync_api():
    py = (ROOT / "distr/gui/web/routes/settings/projects.py").read_text(encoding="utf-8")

    assert '@router.get("/projects/{project_id}/codex-sync")' in py
    assert '@router.post("/projects/{project_id}/codex-sync")' in py
    assert 'project.coding_backend = "codex"' in py


def test_project_routes_expose_cursor_plugin_sync_api():
    py = (ROOT / "distr/gui/web/routes/settings/projects.py").read_text(encoding="utf-8")

    assert "def _cursor_plugin_state" in py
    assert "def _install_local_cursor_plugin" in py
    assert '@router.get("/projects/{project_id}/cursor-sync")' in py
    assert '@router.post("/projects/{project_id}/cursor-sync")' in py
    assert 'project.coding_backend = "cursor"' in py


def test_project_routes_install_codex_plugin_when_codex_backend_setup_runs():
    py = (ROOT / "distr/gui/web/routes/settings/projects.py").read_text(encoding="utf-8")

    assert "def _install_local_codex_plugin" in py
    assert 'Path.home() / "plugins" / "decisions-codex"' in py
    assert 'Path.home() / ".agents" / "plugins" / "marketplace.json"' in py
    assert 'if normalized_backend_id == "codex"' in py
    assert '"plugin_install": install' in py
    assert '"plugin_install": plugin_install' in py


def test_decisions_startup_checks_cursor_plugin_setup():
    script = (ROOT / "bin/decisions.sh").read_text(encoding="utf-8")

    assert "check_cursor_plugin_setup" in script
    assert "cursor_plugin/decisions-cursor/scripts/install_local.py" in script
    assert "DecisionsAI Cursor plugin" in script


def test_decisions_startup_checks_project_tools_without_installing_clis():
    script = (ROOT / "bin/decisions.sh").read_text(encoding="utf-8")

    assert "check_project_cli_presence" in script
    assert "check_codex_plugin_setup" in script
    assert "scripts/setup_project_clis.sh" in script
    assert "npm install -g @mariozechner/pi-coding-agent" not in script
    assert "brew install node" not in script
    assert "sudo apt-get install -y nodejs" not in script
    assert "check_pi_agent" not in script


def test_project_cli_setup_script_installs_tools_outside_startup():
    setup_script = ROOT / "scripts" / "setup_project_clis.sh"
    script = setup_script.read_text(encoding="utf-8")

    assert setup_script.exists()
    assert "cursor-agent" in script
    assert "@openai/codex" in script
    assert "@anthropic-ai/claude-code" in script
    assert "@earendil-works/pi-coding-agent" in script
    assert "decisions.sh" not in script
