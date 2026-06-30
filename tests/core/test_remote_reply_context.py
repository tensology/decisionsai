from distr.core.human_engagement import (
    build_remote_reply_context_preamble,
    record_remote_reply_context,
    reset_remote_reply_context,
)


def test_remote_reply_context_prepends_ticket_workflow_context():
    reset_remote_reply_context()
    thread_id = "telegram-thread-1"
    record_remote_reply_context(
        platform="telegram",
        channel="telegram",
        thread_id=thread_id,
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
        thread_id=thread_id,
    )

    assert "Build React and Django smoke app" in routed
    assert "Development: Ticket to Implementation" in routed
    assert "Run: #74" in routed
    assert "User reply:" in routed
    assert routed.endswith("That TTS message did not make sense.")


def test_remote_reply_context_preamble_includes_goal_hint():
    reset_remote_reply_context()
    thread_id = "telegram-thread-2"
    record_remote_reply_context(
        platform="telegram",
        channel="telegram",
        thread_id=thread_id,
        outbound_text="Plan your board move now.",
        metadata={
            "engagement_goal_hint": "Move the highest priority backlog tickets to Current before standup.",
            "engagement_source": "initiative",
        },
    )

    routed = build_remote_reply_context_preamble(
        "Can we postpone that?",
        platform="telegram",
        thread_id=thread_id,
    )

    assert "Goal: Move the highest priority backlog tickets to Current before standup." in routed
    assert "User reply:" in routed


def test_remote_reply_context_prefers_response_required_over_latest():
    reset_remote_reply_context()
    thread_id = "telegram-thread-3"
    record_remote_reply_context(
        platform="telegram",
        channel="telegram",
        thread_id=thread_id,
        outbound_text="Status ping: the board refresh finished.",
        metadata={
            "requires_response": False,
            "engagement_source": "initiative",
            "engagement_goal_hint": "Daily board refresh",
        },
    )
    record_remote_reply_context(
        platform="telegram",
        channel="telegram",
        thread_id=thread_id,
        outbound_text="Approve this workflow change now.",
        metadata={
            "requires_response": True,
            "engagement_source": "initiative",
            "engagement_goal_hint": "Approve moving backlog tickets into Current before standup.",
        },
    )
    record_remote_reply_context(
        platform="telegram",
        channel="telegram",
        thread_id=thread_id,
        outbound_text="I just synced the inbox feed.",
        metadata={
            "requires_response": False,
            "engagement_source": "initiative",
            "engagement_goal_hint": "Inbox sync complete",
        },
    )

    routed = build_remote_reply_context_preamble(
        "Proceed with the change",
        platform="telegram",
        thread_id=thread_id,
    )

    assert "Approve this workflow change now." in routed
    assert "Approve moving backlog tickets into Current before standup." in routed
    assert "The user is replying from telegram to the most recent DecisionsAI update." in routed
    assert "Inbox sync complete" not in routed


def test_remote_reply_context_matches_exact_thread_only():
    reset_remote_reply_context()
    thread_id_a = "thread-a"
    thread_id_b = "thread-b"

    record_remote_reply_context(
        platform="telegram",
        channel="telegram",
        thread_id=thread_id_a,
        outbound_text="Context from thread A",
        metadata={"engagement_goal_hint": "Review thread A plan", "engagement_source": "initiative"},
    )
    record_remote_reply_context(
        platform="telegram",
        channel="telegram",
        thread_id=thread_id_b,
        outbound_text="Context from thread B",
        metadata={"engagement_goal_hint": "Resolve thread B plan", "engagement_source": "initiative"},
    )

    routed = build_remote_reply_context_preamble(
        "I’m replying from B",
        platform="telegram",
        thread_id=thread_id_b,
    )

    assert "Context from thread B" in routed
    assert "Context from thread A" not in routed


def test_remote_reply_context_without_thread_id_does_not_premap():
    reset_remote_reply_context()
    record_remote_reply_context(
        platform="telegram",
        channel="telegram",
        thread_id="thread-no-match",
        outbound_text="Should not be used without thread id",
    )

    routed = build_remote_reply_context_preamble("plain follow up")

    assert routed == "plain follow up"
