"""Unit tests for workflow completion write-back onto Kanban tickets."""

from types import SimpleNamespace

from distr.core.workflow.dispatcher import _append_workflow_summary_to_ticket


def test_append_workflow_summary_to_empty_ticket_description():
    ticket = SimpleNamespace(description=None)
    steps = [
        {"title": "Analyze", "status": "passed", "result": "Found root cause"},
        {"title": "Patch", "status": "passed", "result": "Updated handler"},
    ]

    _append_workflow_summary_to_ticket(ticket, run_id=42, status="completed", steps_summary=steps)

    text = ticket.description or ""
    assert "[Workflow Run #42] Status: completed" in text
    assert "Analyze: passed" in text
    assert "Patch: passed" in text


def test_append_workflow_summary_preserves_existing_text_and_caps_size():
    ticket = SimpleNamespace(description="Existing ticket details")
    long_result = "x" * 20000
    steps = [{"title": "Step 1", "status": "failed", "result": long_result}]

    _append_workflow_summary_to_ticket(ticket, run_id=99, status="failed", steps_summary=steps)

    text = ticket.description or ""
    assert text.startswith("Existing ticket details")
    assert "[Workflow Run #99] Status: failed" in text
    assert len(text) <= 12000
