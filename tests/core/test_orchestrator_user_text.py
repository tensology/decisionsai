"""Before/after cases taken from live orchestrator notices and TTS on 20 Nov 2026."""

from __future__ import annotations

from distr.core.orchestrator_user_text import (
    humanize_orchestrator_update,
    prepare_spoken_user_text,
    reset_orchestrator_update_dedupe,
    should_emit_orchestrator_update,
    should_speak_sentence,
)


# Real chat/voice lines from 20 Nov 2026, 12:07–16:26 SAST (thread 1381 and
# the Development / E2E runs). Customer emails and ID numbers never appeared
# in these samples.
REAL_BEFORE_AFTER = [
    (
        "Waiting on your approval (APPROVE): My Board has 7 backlog items that should move into Current.",
        "I need your go-ahead to move 7 backlog items on My Board into Current.",
    ),
    (
        "Waiting on your approval (APPROVE): DecisionsAI has 1 backlog item that should move into Current.",
        "I need your go-ahead to move a backlog item on DecisionsAI into Current.",
    ),
    (
        "Waiting on your approval (APPROVE): Spoortrack has 3 backlog items that should move into Current.",
        "I need your go-ahead to move 3 backlog items on Spoortrack into Current.",
    ),
    (
        "Handled directly because this is a short, one-step change.",
        "I'll handle this directly. It's a short, one-step change.",
    ),
    (
        "Started workflow Development because implementation work: Development implements and verifies, and never deploys.",
        "I started the Development workflow because this is a larger piece of work that should be planned, built, reviewed and checked.",
    ),
    (
        "The workflow started and is preparing the first phase.",
        "I've started the workflow and I'm getting the first step ready.",
    ),
    (
        "The workflow completed. The ticket now contains the recorded result and audit evidence.",
        "The workflow finished, and I saved the result on the ticket.",
    ),
    (
        "Ticket created in Backlog.",
        "I've added a ticket in Backlog.",
    ),
    (
        "Ticket moved: Backlog to In Progress.",
        "I moved the ticket from Backlog to In Progress.",
    ),
    (
        "I couldn't finish this one.\nNo steps were recorded.",
        "I couldn't finish this one, and no steps were logged.",
    ),
    (
        "I stopped that run.\nNo steps were recorded.",
        "I stopped that run before any steps were logged.",
    ),
    (
        "Started workflow #12: Investigate the recurring alert.",
        "I started a workflow: Investigate the recurring alert.",
    ),
]


def test_real_log_lines_are_humanized():
    for before, after in REAL_BEFORE_AFTER:
        assert humanize_orchestrator_update(before) == after, before


def test_humanize_is_idempotent():
    for before, after in REAL_BEFORE_AFTER:
        assert humanize_orchestrator_update(after) == after, after
        assert humanize_orchestrator_update(humanize_orchestrator_update(before)) == after


def test_throwaway_test_project_notices_are_dropped_from_speech_and_main_chat():
    noisy = (
        "Waiting on your approval (APPROVE): E2E UX test 2 (safe to delete) "
        "has 4 backlog items that should move into Current."
    )
    other = (
        "Waiting on your approval (APPROVE): E2E board-tool chat (safe to delete) "
        "has 3 backlog items that should move into Current."
    )
    assert humanize_orchestrator_update(noisy, drop_throwaway=True) == ""
    assert humanize_orchestrator_update(noisy, spoken=True) == ""
    assert humanize_orchestrator_update(other, spoken=True) == ""
    # Stored on the test thread itself, the wording is still cleaned.
    stored = humanize_orchestrator_update(noisy)
    assert "APPROVE" not in stored
    assert "safe to delete" not in stored.lower()
    assert "go-ahead" in stored


def test_spoken_plan_outline_from_live_tts_is_rewritten():
    # TTS SENTENCE EMIT at 16:25–16:26 SAST on 20 Nov 2026, after the model
    # followed the [PLAN FIRST] instruction and the splitter read each line.
    spoken = "\n".join([
        "Plan.",
        "List.",
        "Move ticket 343 from Backlog to QA.",
        "3..",
        "Merge ticket 345 into 344, keeping 344.",
        "Report ticket ids and what changed.",
        "Move #343 to QA, blocked.",
        "Merge #345 into #344, blocked.",
        "The board tools refused both changes, saying this request was read-only, so nothing on the board has changed yet.",
        "Tell me to go ahead and I'll run the move and the merge straight away.",
    ])
    out = prepare_spoken_user_text(spoken)
    assert "Plan." not in out
    assert not out.startswith("List")
    assert "3.." not in out
    assert "Report ticket ids" not in out
    assert "blocked" not in out.lower()
    assert "couldn't move" in out.lower() or "could not move" in out.lower()
    assert "couldn't merge" in out.lower() or "could not merge" in out.lower()
    assert "board tools refused" in out.lower()
    assert "go ahead" in out.lower()


def test_heading_only_sentences_are_not_spoken():
    assert should_speak_sentence("Plan.") == ""
    assert should_speak_sentence("List.") == ""
    assert should_speak_sentence("3..") == ""
    assert should_speak_sentence("Plan and outcome.") == ""
    assert should_speak_sentence("Report ticket ids and what changed.") == ""
    assert should_speak_sentence("Move #343 to QA, blocked.") == "I couldn't move that ticket to QA."
    # Ordinary speech is left alone.
    assert should_speak_sentence("Hey Paul, good to have you back.") == "Hey Paul, good to have you back."


def test_enums_ids_and_tool_names_are_stripped():
    out = humanize_orchestrator_update(
        "Started workflow #44 on board id 40 (project id 28) with merge_ticket."
    )
    assert "#" not in out
    assert "board id" not in out.lower()
    assert "project id" not in out.lower()
    assert "merge_ticket" not in out
    assert "APPROVE" not in humanize_orchestrator_update(
        "Waiting on your approval (APPROVE): My Board has 2 backlog items that should move into Current."
    )


def test_duplicate_notices_are_rate_limited():
    reset_orchestrator_update_dedupe()
    line = "I need your go-ahead to move 3 backlog items on Spoortrack into Current."
    assert should_emit_orchestrator_update(line, now=1_000.0) is True
    assert should_emit_orchestrator_update(line, now=1_030.0) is False
    assert should_emit_orchestrator_update(line, now=1_000.0 + 91) is True
    other = "I need your go-ahead to move 5 backlog items on Merrypak into Current."
    assert should_emit_orchestrator_update(other, now=1_000.0 + 92) is True


def test_visible_reason_helpers_stay_conversational():
    from distr.core.workflow.execution_mode import (
        _visible_direct_reason,
        _visible_workflow_reason,
    )

    assert _visible_direct_reason("", lightweight=True, conversational=False) == (
        "I'll handle this directly. It's a short, one-step change."
    )
    reason = _visible_workflow_reason(
        "Development",
        "Implementation work: Development implements and verifies, and never deploys.",
        "",
    )
    assert reason.startswith("I started the Development workflow because")
    assert "implements and verifies, and never deploys" not in reason
