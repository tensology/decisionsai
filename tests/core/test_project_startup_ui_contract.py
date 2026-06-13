from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECTS_JS = ROOT / "distr/gui/web/static/projects/js/projects.js"


def _projects_js() -> str:
    return PROJECTS_JS.read_text(encoding="utf-8")


def test_startup_terminals_show_ordered_panels_before_backend_materializes_sessions():
    src = _projects_js()
    start_block = src.split("function startStartupTerminals() {", 1)[1].split(
        "function createStartupTerminal(",
        1,
    )[0]

    assert 'switchTab("startup");' in start_block
    assert "renderStartupTerminalPlaceholders(commands);" in start_block
    assert "refreshStartupSessionsUntilVisible(currentProjectId, commands)" in start_block
    assert "if (!sessions.length) return;" not in start_block


def test_startup_terminal_placeholders_are_visible_and_explain_queue_state():
    src = _projects_js()

    assert "function renderStartupTerminalPlaceholders(commands) {" in src
    assert "startup-terminal-card startup-terminal-card--pending" in src
    assert "Queued for startup" in src
    assert "Waiting for terminal session" in src


def test_startup_session_refresh_materializes_queued_backend_sessions():
    src = _projects_js()
    refresh_block = src.split("function refreshStartupSessionsUntilVisible(projectId, commands) {", 1)[1].split(
        "function createStartupTerminal(",
        1,
    )[0]

    assert 'apiFetch("/api/projects/" + projectId + "/startup-sessions")' in refresh_block
    assert "reattachProjectTerminals(projectId);" in refresh_block
    assert "setTimeout(poll, delay);" in refresh_block


def test_startup_success_toast_is_short_not_raw_diagnostics_dump():
    src = _projects_js()
    start_block = src.split("function startStartupTerminals() {", 1)[1].split(
        "function createStartupTerminal(",
        1,
    )[0]

    assert "function formatStartupStartSnackbar(response, commands) {" in src
    assert 'showSnackbar(formatStartupStartSnackbar(response, commands), response.failed ? "error" : "success");' in start_block
    assert 'showSnackbar(response.message, response.failed ? "error" : "success");' not in start_block
