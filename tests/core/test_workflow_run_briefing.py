from distr.core.workflow.run_briefing import (
    RunBriefingContext,
    build_run_briefing_message,
    build_step_review_message,
    classify_human_workflow_response,
    human_checkpoint_enabled,
)


def test_build_run_briefing_message_is_plain_english():
    message = build_run_briefing_message(
        RunBriefingContext(
            run_id=1,
            workflow_id=2,
            workflow_name="Senior Software Engineer: Ticket to Green",
            ticket_id=124,
            ticket_title="Session Report feedback",
            ticket_summary="Update the session report UI to match the May spec.",
            project_name="Player1Sport",
            loop_goal="linked ticket implemented, validated, and evidence-backed",
            loop_exit="plan.md is attached and checks are green",
            first_step_name="Ingest ticket and project context",
            first_step_instruction="Restate the problem and project route.",
            step_count=12,
            step_outline="1. Ingest; 2. Plan",
        )
    )
    assert "Session Report feedback" in message
    assert "Player1Sport" in message
    assert "Ingest ticket and project context" in message
    assert "**About to do:**" in message
    assert "**Recommendation:**" in message
    assert "reply continue" not in message.lower()


def test_classify_human_workflow_response():
    assert classify_human_workflow_response("yes go ahead", waiting_kind="run_briefing") == "confirm"
    assert classify_human_workflow_response("yes, go ahead", waiting_kind="run_briefing") == "confirm"
    assert classify_human_workflow_response("do the safe option", waiting_kind="run_briefing") == "confirm"
    assert classify_human_workflow_response("stop", waiting_kind="run_briefing") == "stop"
    assert classify_human_workflow_response(
        "Focus on the PDF spec first, not the Google doc yet.",
        waiting_kind="run_briefing",
    ) == "steer"
    assert classify_human_workflow_response("looks good", waiting_kind="step_review") == "continue"
    assert classify_human_workflow_response("approve", waiting_kind="step_review") == "continue"
    assert classify_human_workflow_response("looks good, continue", waiting_kind="step_review") == "continue"


def test_build_step_review_message():
    message = build_step_review_message(
        ticket_title="Session Report feedback",
        step_name="Ingest ticket and project context",
        step_index=1,
        passed=True,
        result_summary="Restated scope and risks.",
        next_step_name="Write plan.md and attach to ticket",
    )
    assert "Continue after" in message
    assert "Write plan.md" in message
    assert "**Recommendation:**" in message


def test_human_checkpoint_enabled_for_ticket_runs():
    assert human_checkpoint_enabled({"ticket_id": 124, "loop_contract": {"goal": "ship"}})
    assert not human_checkpoint_enabled({"skip_human_checkpoints": True, "ticket_id": 124})
