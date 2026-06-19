from distr.core.human_engagement import (
    build_remote_reply_context_preamble,
    record_remote_reply_context,
    reset_remote_reply_context,
)


def test_remote_reply_context_prepends_ticket_workflow_context():
    reset_remote_reply_context()
    record_remote_reply_context(
        platform="telegram",
        channel="telegram",
        workflow_id=372,
        run_id=74,
        step_id=12,
        ticket_id=161,
        ticket_title="Build React and Django smoke app",
        workflow_title="Development: Ticket to Implementation",
        step_title="Implement and validate",
        outbound_text="Build React and Django smoke app: Step 3 passed.",
    )

    routed = build_remote_reply_context_preamble(
        "That TTS message did not make sense.",
        platform="telegram",
    )

    assert "Build React and Django smoke app" in routed
    assert "Development: Ticket to Implementation" in routed
    assert "Run: #74" in routed
    assert "User reply:" in routed
    assert routed.endswith("That TTS message did not make sense.")
