"""Workflow completion belongs in the durable run receipt, not ticket prose."""

from distr.core.workflow.dispatcher import build_workflow_run_receipt


def test_workflow_receipt_keeps_step_evidence_without_ticket_description_writeback():
    receipt = build_workflow_run_receipt(
        run_id=42,
        workflow_id=7,
        status="completed",
        ticket_id=12,
        steps_summary=[
            {"title": "Analyze", "status": "passed", "result": "Found root cause"},
            {"title": "Patch", "status": "passed", "result": "Updated handler"},
        ],
    )

    assert receipt["run_id"] == 42
    assert receipt["ticket_id"] == 12
    assert receipt["completed_step_count"] == 2
    assert receipt["steps_summary"][1]["result"] == "Updated handler"
