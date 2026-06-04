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


def test_workflow_js_exposes_ui_taste_feedback_controls():
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert "function renderUiTasteControls" in js
    assert "function submitUiTasteFeedback" in js
    assert 'data-ui-feedback-label="approved"' in js
    assert 'data-ui-save-baseline="true"' in js
    assert "save_as_visual_baseline" in js
    assert "visual_baseline_name" in js
    assert "baseline_screen_name" in js
    assert "visual_baseline_readiness" in js
    assert "if (saveAsBaseline && data.visual_baseline_readiness)" in js
    assert 'data-ui-feedback-label="spacing_off"' in js
    assert 'data-ui-feedback-label="flow_bad"' in js
    assert 'data-ui-feedback-label="hierarchy_unclear"' in js
    assert 'data-ui-feedback-label="inconsistent_styling"' in js
    assert 'data-ui-feedback-label="too_many_clicks"' in js
    assert 'data-testid="wf-ui-taste-controls"' in js
    assert '"/workflows/" + workflowId + "/runs/" + runId + "/ui-feedback"' in js


def test_workflow_ui_exposes_visual_baseline_management():
    html = (ROOT / "distr/gui/web/templates/workflows/workflows.html").read_text(encoding="utf-8")
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert 'id="wf-visual-baselines-panel"' in html
    assert 'id="wf-baseline-name"' in html
    assert 'id="wf-baseline-screen-name"' in html
    assert 'id="wf-baseline-screenshot-path"' in html
    assert 'id="wf-save-visual-baseline"' in html
    assert "function loadVisualBaselines" in js
    assert "function renderVisualBaselineReadiness" in js
    assert "function createVisualBaseline" in js
    assert '"/workflows/visual-baselines?board_id="' in js
    assert '"/workflows/visual-baselines/readiness?board_id="' in js
    assert "visual_baseline_readiness" in js
    assert "data-visual-baseline-status" in js
    assert "data-visual-baseline-screen-status" in js
    assert "Ready for comparison" in js
    assert "Missing reference file" in js
    assert "store_copy: true" in js
    assert '"/workflows/visual-baselines"' in js


def test_workflow_ui_exposes_scheduled_action_queue_management():
    html = (ROOT / "distr/gui/web/templates/workflows/workflows.html").read_text(encoding="utf-8")
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert 'id="wf-scheduled-actions-panel"' in html
    assert 'id="wf-scheduled-actions-list"' in html
    assert 'id="wf-scheduled-actions-empty"' in html
    assert 'id="wf-refresh-scheduled-actions"' in html
    assert "function loadScheduledActions" in js
    assert "function renderScheduledActions" in js
    assert "function disableScheduledActionByTitle" in js
    assert "function cancelScheduledActionByTitle" in js
    assert "function rescheduleScheduledActionByTitle" in js
    assert '"/workflows/scheduled-actions"' in js
    assert '"/workflows/scheduled-actions/by-title?title="' in js
    assert 'data-scheduled-action-title' in js
    assert 'wf-scheduled-action-kind' in js
    assert 'wf-scheduled-action-time' in js
    assert 'wf-scheduled-action-reschedule' in js
    assert "schedule: {" in js


def test_workflow_step_editor_exposes_visual_baseline_config():
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert "sf-ui-capture" in js
    assert "sf-visual-baseline-name" in js
    assert "sf-baseline-screen-name" in js
    assert "sf-visual-diff-threshold" in js
    assert "visual_baseline_name" in js
    assert "baseline_screen_name" in js
    assert "visual_diff_threshold" in js


def test_workflow_run_evidence_exposes_ui_correction_status():
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert "function renderCorrectionStatus" in js
    assert 'data-testid="wf-run-correction-status"' in js
    assert "Correction queued" in js
    assert "Correction auto-dispatched" in js
    assert "correction_attempt_id" in js
    assert "dispatch_result" in js
    assert "terminal_ui_quality_gate" in js


def test_workflow_runs_tab_exposes_correction_history_panel():
    html = (ROOT / "distr/gui/web/templates/workflows/workflows.html").read_text(encoding="utf-8")
    js = (ROOT / "distr/gui/web/static/workflows/js/workflows.js").read_text(encoding="utf-8")

    assert 'data-runs-tab="corrections"' in html
    assert 'id="wf-runs-pane-corrections"' in html
    assert 'id="wf-corrections-status-filter"' in html
    assert 'id="wf-corrections-list"' in html
    assert 'id="wf-corrections-empty"' in html
    assert "function renderCorrections" in js
    assert "function loadWorkflowCorrections" in js
    assert '"/workflows/" + currentWorkflowId + "/corrections"' in js
    assert "wf-corrections-status-filter" in js
    assert "workflowRunsSubtab === \"corrections\"" in js
