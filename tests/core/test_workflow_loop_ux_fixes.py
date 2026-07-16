from __future__ import annotations

from unittest.mock import MagicMock, patch

from distr.core.kanban.ticket_workflow_engagement import (
    _notification_cadence,
    build_provider_failover_message,
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
    message = build_step_start_message(
        ticket_title="Login bug",
        step_name="Ingest ticket and project context",
        step_index=1,
    )
    assert message == (
        "For Login bug, I'm moving on to step 1. I'm now reviewing the ticket "
        "requirements, project files, and existing constraints."
    )
    assert "has started" not in message


def test_successful_step_message_explains_the_outcome_instead_of_only_saying_passed():
    message = build_step_done_message(
        ticket_title="Login bug",
        step_name="Write implementation plan",
        passed=True,
        result_text="Created plan.md and attached it to the ticket.",
        step_index=2,
    )

    assert "implementation plan is ready" in message.lower()
    assert not message.endswith("passed.")


def test_validation_voice_summary_does_not_expose_raw_worker_output():
    message = build_step_done_message(
        ticket_title="Login bug",
        step_name="Validate the implementation",
        passed=True,
        result_text=(
            "OpenAI Codex v0.144.5 callback_url=http://127.0.0.1:8765 "
            "node --test tests/login.test.mjs tests 4 pass 4 fail 0"
        ),
        step_index=4,
    )

    assert "validation checks passed" in message.lower()
    assert "callback_url" not in message
    assert "OpenAI Codex" not in message


def test_provider_failover_message_explains_the_change_and_required_action():
    message = build_provider_failover_message(
        ticket_title="Checkout validation",
        step_name="Implement and test checkout fixes",
        failed_backend="pi",
        fallback_backend="codex",
    )

    assert "local model" in message.lower()
    assert "switched to Codex" in message
    assert "continued automatically" in message
    assert "don't need to do anything" in message


def test_notification_ledger_cadence_prevents_duplicate_next_step_voice_note():
    run_started, next_step_already_announced = _notification_cadence(
        ["step_done:41:pass:next:42", "step_start:41"],
        step_id=42,
    )

    assert run_started is True
    assert next_step_already_announced is True


def test_notification_ledger_only_suppresses_when_latest_note_announced_step():
    run_started, next_step_already_announced = _notification_cadence(
        ["provider_failover:42:pi:codex", "step_done:41:pass:next:42", "step_start:41"],
        step_id=42,
    )

    assert run_started is True
    assert next_step_already_announced is False


def test_brief_step_failure_message_for_missing_project():
    message = build_step_done_message(
        ticket_title="Login bug",
        step_name="Ingest ticket and project context",
        passed=False,
        result_text="No linked project for this ticket.",
        step_index=1,
    )
    assert message.startswith("I couldn't complete this part of Login bug while reviewing")
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
    assert "supporting evidence are recorded on the ticket" in build_run_done_message(
        ticket_title="T", status="completed"
    )
    assert build_run_done_message(ticket_title="T", status="cancelled") == "I've stopped the workflow for T."
    assert build_run_done_message(ticket_title="T", status="failed") == "I couldn't finish the workflow for T."


def test_timeout_failure_is_explained_without_project_cli_jargon():
    message = build_step_done_message(
        ticket_title="Login bug",
        step_name="Implement and test",
        passed=False,
        result_text="Failed sending to project CLI: Project CLI timed out after 300s.",
        step_index=3,
    )

    assert "worker reached its time limit" in message.lower()
    assert "project CLI" not in message
    assert "step 3" not in message.lower()
    assert "making the requested changes" in message.lower()
