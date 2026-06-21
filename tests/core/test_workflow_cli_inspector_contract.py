from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_HTML = ROOT / "distr/gui/web/templates/workflows/workflows.html"
WORKFLOWS_JS = ROOT / "distr/gui/web/static/workflows/js/workflows.js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_cli_tab_uses_left_inspector_with_step_models_and_session_thread():
    html = read(WORKFLOWS_HTML)

    assert 'id="wf-cli-inspector"' in html
    assert 'id="wf-cli-inspector-toggle"' in html
    assert 'data-cli-inspector-tab="step-models"' in html
    assert 'data-cli-inspector-tab="session-thread"' in html
    assert ">Step Models<" in html
    assert ">Session Thread<" in html
    assert 'id="wf-cli-step-models-callout"' in html
    assert 'id="wf-cli-step-models-callout-close"' in html
    assert 'id="wf-cli-session-thread-list"' in html


def test_cli_tab_no_longer_uses_right_sidebar_inventory_shell():
    html = read(WORKFLOWS_HTML)

    assert 'class="wf-cli-sidebar"' not in html
    assert 'aria-label="CLI model inventory"' not in html


def test_workflows_js_tracks_cli_inspector_and_board_pane_collapse_state():
    js = read(WORKFLOWS_JS)

    assert 'wf_cli_inspector_open' in js
    assert 'wf_cli_inspector_tab' in js
    assert 'wf_step_models_callout_visible' in js
    assert 'wf_board_panel_collapsed' in js
    assert "function renderWorkflowCliSessionThread" in js
    assert "function toggleWorkflowCliInspector" in js
    assert "function setWorkflowStepModelsCalloutVisible" in js
    assert "function setWorkflowBoardPaneCollapsed" in js


def test_board_ticket_rows_render_board_pane_toggle_affordance():
    js = read(WORKFLOWS_JS)

    assert "wf-board-pane-toggle" in js
    assert "Collapse board tickets" in js
    assert "Expand board tickets" in js
