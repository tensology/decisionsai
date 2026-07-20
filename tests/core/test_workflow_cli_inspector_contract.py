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
    assert 'id="wf-cli-backend-trigger"' in html
    assert 'id="wf-cli-key-modal"' in html
    assert 'id="wf-cli-model-capability-row"' in html
    assert 'id="wf-cli-model-context-menu"' in html
    assert 'id="wf-cli-connect"' not in html


def test_cli_tab_no_longer_uses_right_sidebar_inventory_shell():
    html = read(WORKFLOWS_HTML)

    assert 'class="wf-cli-sidebar"' not in html
    assert 'aria-label="CLI model inventory"' not in html


def test_workflows_js_tracks_cli_inspector_and_board_pane_collapse_state():
    js = read(WORKFLOWS_JS)

    assert 'wf_cli_inspector_open' in js
    assert 'wf_cli_inspector_tab' in js
    assert 'wf_step_models_callout_visible' in js
    assert 'WF_CLI_BOARD_STATE_STORAGE_PREFIX' in js
    assert 'wf_board_panel_collapsed' in js
    assert "function renderWorkflowCliSessionThread" in js
    assert 'session && session.input_packet && typeof session.input_packet === "object"' in js
    assert "function persistWorkflowCliBoardState" in js
    assert "function restoreWorkflowCliBoardState" in js
    assert "function fetchWorkflowCliTerminalState" in js
    assert "terminalState.connected" in js
    assert "external_thread_id" in js
    assert "function toggleWorkflowCliInspector" in js
    assert "function setWorkflowStepModelsCalloutVisible" in js
    assert "function setWorkflowBoardPaneCollapsed" in js
    assert "if (!terminalState || !terminalState.alive) return;" not in js
    assert "CLI auth already available" in js
    assert "function renderWorkflowCliBackendMenu" in js
    assert "function openWorkflowCliKeyModal" in js
    assert "function syncWorkflowCliCapabilityControls" in js
    assert "function openWorkflowCliModelContextMenu" in js
    assert "function workflowCliAreaHeartbeatTick" in js
    assert "function setWorkflowCliAreaPresence" in js
    assert "function ensureWorkflowCliSessionForArea" in js
    assert "/terminal/keepalive" in js
    assert "dblclick" in js
    assert "contextmenu" in js
    assert "No new key is needed unless you want to replace or add an override." in js
    assert 'els.save.dataset.mode === "reveal"' in js
    assert "Replace saved key" in js


def test_board_ticket_rows_render_board_pane_toggle_affordance():
    js = read(WORKFLOWS_JS)

    assert "wf-board-pane-toggle" in js
    assert "Collapse board tickets" in js
    assert "Expand board tickets" in js


def test_active_run_polling_does_not_recursively_duplicate_network_refreshes():
    js = read(WORKFLOWS_JS)
    soft_refresh = js.split("function softRefresh()", 1)[1].split("function formatElapsed", 1)[0]
    active_runs = js.split("function loadActiveRuns()", 1)[1].split("function renderWorkflowExecutionSessions", 1)[0]
    start_polling = js.split("function startPolling()", 1)[1].split("function stopPolling", 1)[0]

    assert "checkActiveRun();" not in soft_refresh
    assert "loadWorkflowExecutionSessions();" not in soft_refresh
    assert "loadList();" not in active_runs
    assert "if (pollTimer) return;" in start_polling


def test_websocket_worker_event_bursts_are_coalesced_before_network_refreshes():
    js = read(WORKFLOWS_JS)
    ws_handler = js.split("ws.onmessage = function (evt)", 1)[1].split("ws.onclose", 1)[0]
    scheduler = js.split("function scheduleWorkflowLiveRefresh()", 1)[1].split("function formatElapsed", 1)[0]

    assert "scheduleWorkflowLiveRefresh();" in ws_handler
    assert "loadList();" not in ws_handler
    assert "softRefresh();" not in ws_handler
    assert "loadLoopActivityFeed" not in ws_handler
    assert "workflowWsRefreshInFlight" in scheduler
    assert "workflowWsRefreshQueued" in scheduler
    assert "Promise.allSettled(requests)" in scheduler
