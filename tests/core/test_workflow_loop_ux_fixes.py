from __future__ import annotations

from unittest.mock import MagicMock, patch

from distr.core.kanban.ticket_workflow_engagement import (
    build_run_done_message,
    build_step_done_message,
    build_step_start_message,
)
from distr.core.workflow.verification import _run_verification


def test_run_verification_passes_ticket_context_to_llm_judgment():
    step = MagicMock()
    step.validation_type = "llm_judgment"
    step.validation_prompt = "Ticket brief is explicit."
    step.id = 1

    with patch(
        "distr.core.workflow.verification._verify_llm_judgment",
        return_value=True,
    ) as mock_judgment:
        passed = _run_verification(
            step,
            "ingested ticket context",
            True,
            ticket_context="Fix login bug",
            standards_context="Keep diffs small.",
        )

    assert passed is True
    mock_judgment.assert_called_once_with(
        "ingested ticket context",
        "Ticket brief is explicit.",
        standards_context="Keep diffs small.",
        ticket_context="Fix login bug",
        unavailable_fallback=True,
    )


def test_brief_step_start_message():
    assert (
        build_step_start_message(
            ticket_title="Login bug",
            step_name="Ingest ticket and project context",
            step_index=1,
        )
        == "Login bug: Step 1: Ingest ticket and project context has started."
    )


def test_brief_step_failure_message_for_missing_project():
    message = build_step_done_message(
        ticket_title="Login bug",
        step_name="Ingest ticket and project context",
        passed=False,
        result_text="No linked project for this ticket.",
        step_index=1,
    )
    assert message.startswith("Login bug: Step 1:")
    assert "failed" in message
    assert "Link the ticket to a project first." in message


def test_brief_step_failure_message_for_cursor_usage_limit():
    message = build_step_done_message(
        ticket_title="Login bug",
        step_name="Ingest ticket and project context",
        passed=False,
        result_text="You've hit your usage limit",
        step_index=1,
    )
    assert "usage limit" in message.lower()
    assert build_run_done_message(ticket_title="T", status="completed") == "T: Run finished."
    assert build_run_done_message(ticket_title="T", status="cancelled") == "T: Run stopped."
    assert build_run_done_message(ticket_title="T", status="failed") == "T: Run failed."
