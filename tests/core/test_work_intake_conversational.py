"""Conversational remarks must not be routed as work (the "lost thread" bug).

Paul, 2026-11-23: mid-conversation he said "…if you take a look at the changelog,
I mean, I'm not asking you to do that…" and Hayley answered "Plan My Charter has
no workflow linked, so I can't run this here. Which project or board should run
it?" — the 'take a look at' work idiom plus a missing disclaimer guard routed a
comment into the work-intake/dispatch flow.
"""

import pytest

from distr.core.work_intake.service import (
    WorkIntake,
    WorkIntakeAction,
    get_work_intake_service,
)


@pytest.mark.parametrize(
    "text",
    [
        # The exact remark from the transcript.
        (
            "No, no more like, more like three, almost two, three years, man. "
            "If you take a look at the changelog, I mean, I'm not asking you to do that, "
            "but if you do take a look at the changelog, you can check that it started in 2025, "
            "like literally as ChatGPT somewhat 3 came out"
        ),
        "Do you know what I mean?",
        "I'm just commenting, don't do anything with that.",
        "No need to action that, I was only thinking out loud.",
        "If you take a look at the changelog you'll see what I mean.",
    ],
)
def test_conversational_remark_is_not_routed_as_work(text):
    decision = get_work_intake_service().classify(WorkIntake(source="web", user_text=text))
    assert decision.action == WorkIntakeAction.ANSWER_DIRECTLY


@pytest.mark.parametrize(
    "text",
    [
        "Fix the login bug on the charter site",
        "create a ticket to reorder the calendar options",
        "run the workflow for ticket 330",
        # A request wrapped in disclaimer words must still route.
        "Don't forget to fix the login bug.",
        "can you update the footer copy",
        "This is not a request, actually it is — create a ticket for the header.",
    ],
)
def test_real_work_requests_still_route(text):
    decision = get_work_intake_service().classify(WorkIntake(source="web", user_text=text))
    assert decision.action != WorkIntakeAction.ANSWER_DIRECTLY


@pytest.mark.parametrize(
    "text",
    [
        # Commentary that merely mentions work-ish words stays conversational.
        "The fix you did yesterday was great.",
        "I think the header looks off.",
        "I'm just saying the header looks off.",
    ],
)
def test_commentary_about_work_stays_conversational(text):
    decision = get_work_intake_service().classify(WorkIntake(source="web", user_text=text))
    assert decision.action == WorkIntakeAction.ANSWER_DIRECTLY
