"""Regression checks for human-readable workflow control feedback."""

from pathlib import Path

from distr.gui.web.routes.settings.workflows import _workflow_error_payload, _workflow_feedback_message


ROOT = Path(__file__).resolve().parents[2]


def test_workflow_error_payload_humanizes_common_active_run_failure():
    payload = _workflow_error_payload("A run is already in progress for this board/ticket", "run")

    assert payload["detail"] == "A run is already active for this workflow scope."
    assert payload["raw_detail"] == "A run is already in progress for this board/ticket"
    assert payload["action"] == "run"
    assert "Active Runs" in payload["next_action"]


def test_workflow_error_payload_humanizes_continue_wrong_state():
    payload = _workflow_error_payload("Run is not waiting (status: running)", "continue")

    assert payload["detail"] == "This workflow is not currently waiting for input."
    assert "continue only applies to waiting runs" in payload["next_action"]


def test_workflow_feedback_message_describes_continue_decision():
    payload = _workflow_feedback_message("continued", {"action": "next_step", "step_id": 42})

    assert payload["message"] == "Workflow continued to step #42."
    assert "next step outcome" in payload["next_action"]


def test_workflow_js_preserves_structured_error_context():
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert "function workflowFeedbackText" in js
    assert "function workflowErrorText" in js
    assert "err.workflowDetail = d" in js
    assert 'workflowErrorText(e, "Run failed")' in js
    assert 'workflowFeedbackText(resp, "Run continued")' in js


def test_workflow_run_all_dispatches_an_explicit_ticket_group():
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert "function startWorkflowTicketGroup" in js
    assert 'openWorkflowRunPreview(first.id, queue.map(function (ticket)' in js
    assert '"/workflows/" + encodeURIComponent(currentWorkflowId) + "/run-ticket-group"' in js
    assert "Group ' + esc(groupPosition) + '/' + esc(groupSize)" in js
    assert "workflowRunPreviewCountSuffix(additionalTickets)" in js
    assert "startedTicketIds.indexOf(String(ticketId)) === -1" in js
    assert "function activeWorkflowDetailTab" in js
    assert 'activeWorkflowDetailTab() === "tickets"' in js


def test_workflow_state_reads_bypass_browser_cache():
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert 'cache: method === "GET" ? "no-store" : "default"' in js


def test_workflow_js_exposes_ui_taste_feedback_controls():
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert "function renderUiTasteControls" in js
    assert "function submitUiTasteFeedback" in js
    assert 'data-ui-feedback-label="approved"' in js
    assert 'data-ui-save-baseline="true"' in js
    assert "save_as_visual_baseline" in js
    assert "visual_baseline_name" in js
    assert "baseline_screen_name" in js
    assert 'data-ui-feedback-label="spacing_off"' in js
    assert 'data-ui-feedback-label="flow_bad"' in js
    assert 'data-ui-feedback-label="hierarchy_unclear"' in js
    assert 'data-ui-feedback-label="inconsistent_styling"' in js
    assert 'data-ui-feedback-label="too_many_clicks"' in js
    assert 'data-testid="wf-ui-taste-controls"' in js
    assert '"/workflows/" + pending.workflowId + "/runs/" + pending.runId + "/ui-feedback"' in js


def test_workflow_step_editor_exposes_visual_baseline_config():
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert "sf-ui-capture" in js
    assert "sf-visual-baseline-name" in js
    assert "sf-baseline-screen-name" in js
    assert "sf-visual-diff-threshold" in js
    assert "visual_baseline_name" in js
    assert "baseline_screen_name" in js
    assert "visual_diff_threshold" in js


def test_workflow_config_modal_exposes_run_policy_and_context_rules():
    html = (ROOT / "distr/gui/web/templates/workflows/workflows.html").read_text(encoding="utf-8")
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert "Global workflow configuration" in html
    assert 'data-wf-config-tab="run-policy"' in html
    assert 'data-wf-config-tab="context-rules"' in html
    assert 'data-wf-config-tab="execution"' in html
    assert 'id="wf-config-run-execution-mode"' in html
    assert 'id="wf-config-run-concurrency-scope"' in html
    assert 'id="wf-config-run-max-parallel"' in html
    assert 'id="wf-config-run-branch-per-ticket"' in html
    assert 'id="wf-config-context-items-list"' in html
    assert 'id="wf-config-add-context-item-btn"' in html
    assert 'id="wf-global-exec-routes"' in html
    assert 'id="wf-global-exec-backend-pills"' in html
    assert "function refreshWorkflowConfigPanel" in js
    assert "function saveWorkflowRunSettings" in js
    assert "function renderContextRules" in js
    assert "function refreshWorkflowGlobalExecutionPanel" in js
    assert "function saveWorkflowGlobalExecutionRouting" in js
    assert "hermes-readiness-strip" not in html
    assert "Ticket complexity routing" in html
    assert "function workflowExecRouteHtml" in js
    assert "function saveWorkflowExecRouting" in js
    assert "wf-board-edit-tab" not in js
    assert 'data-wf-config-tab="execution"' in html
    assert "wf-board-hermes-routing-mode" not in js
    assert "max_correction_attempts" not in js
    assert "auto_dispatch_corrections" not in js
    assert "function renderCorrectionStatus" not in js
    assert 'data-runs-tab="corrections"' not in html
    assert 'id="wf-tab-context"' not in html
    assert 'data-tab="context"' not in html
    assert "Bundled skills" not in js
    assert "function renderSkillChains" not in js
    assert "function loadLearnedRules" not in js
    assert "function loadScheduledActions" not in js
    assert "function loadVisualBaselines" not in js
    assert 'id="wf-skills-catalog"' not in html
    assert 'id="wf-learned-rules-list"' not in html
    assert 'id="wf-scheduled-actions-panel"' not in html
    assert 'id="wf-visual-baselines-panel"' not in html
    assert 'data-runs-tab="memory"' in html
    assert 'id="wf-steering-memory-body"' in html
    assert "function loadWorkflowSteeringMemory" in js
    assert "function renderSteeringMemory" in js
    assert '"/runs/" + workflowMemoryRunId + "/steering-memory"' in js
