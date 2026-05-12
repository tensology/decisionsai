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
