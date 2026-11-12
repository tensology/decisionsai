"""Percent-first conversational replies and mid-task updates (owner request, 12 Nov 2026)."""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from distr.core import conversational_progress as cp
from distr.core.human_engagement import voice_friendly_text
from distr.core.integrations.telegram.remote_tts_delivery import remote_spoken_text

PERCENT_THEN_PROSE = re.compile(r"^(\d{1,3})% done\. [A-Z][^\n]*[a-z][^\n]*[.!?]")
BANNER = (
    "Verification incomplete: Required verification has not passed. Inspect the recorded checks and rerun them."
    "\n\nAgent report (not independently verified):\n"
)


def test_completed_reply_starts_with_percent_then_conversational_prose():
    reply = cp.conversational_development_reply(
        "The Snuza dump loaded cleanly, and all the checks passed.", status="completed", success=True
    )
    assert reply == "100% done. The Snuza dump loaded cleanly, and all the checks passed."
    assert PERCENT_THEN_PROSE.match(reply)


def test_raw_harness_banner_is_rewritten_into_conversation():
    raw = BANNER + "Fresh 2026-11-11 dump set is on the server. Running now."
    reply = cp.conversational_development_reply(
        raw, status="blocked", success=False, change_expected=True, changed_files=0,
        verification={"status": "not_run", "checks": [], "required": [], "missing": []},
    )
    assert PERCENT_THEN_PROSE.match(reply)
    for harness_text in ("Verification incomplete", "not independently verified", "Inspect the recorded checks", "Agent report"):
        assert harness_text not in reply
    assert "I can't call it done yet" in reply
    assert "Fresh 2026-11-11 dump set is on the server." in reply


def test_blocked_turn_cannot_claim_100_even_if_the_agent_did():
    raw = BANNER + "100% done. Everything passed."
    reply = cp.conversational_development_reply(
        raw, status="blocked", success=False, change_expected=True, changed_files=1,
        verification={"status": "failed", "checks": [{"check": "npm run test", "status": "failed"}]},
    )
    assert cp.leading_percent(reply) == 75
    assert reply.count("% done.") == 1
    assert "`npm run test` failed" in reply


@pytest.mark.parametrize(
    "checks,expected",
    [
        ([], 75),
        ([{"check": "a", "status": "failed"}], 75),
        ([{"check": "a", "status": "passed"}, {"check": "b", "status": "failed"}], 87),
        ([{"check": "a", "status": "passed"}, {"check": "b", "status": "passed"}, {"check": "c", "status": "failed"}], 91),
    ],
)
def test_percent_is_derived_from_real_stages_and_checks(checks, expected):
    pct = cp.development_turn_percent(
        agent_finished=True, complete=False, verification_required=True,
        verification={"status": "failed", "checks": checks},
    )
    assert pct == expected


def test_noop_and_unrequired_verification_change_the_score():
    assert cp.development_turn_percent(agent_finished=True, complete=False, change_missing=True,
                                       verification_required=True, verification={"status": "passed"}) == 75
    assert cp.development_turn_percent(agent_finished=True, complete=False) == 99
    assert cp.development_turn_percent(agent_finished=False, complete=False) == 25


def test_step_percent_uses_done_over_planned_and_reserves_100():
    assert cp.percent_from_steps(1, 10) == 10
    assert cp.percent_from_steps(49, 50) == 98
    assert cp.percent_from_steps(50, 50) == 99  # all steps ran but completion not confirmed
    assert cp.percent_from_steps(50, 50, complete=True) == 100
    assert cp.percent_from_steps(3, 0) is None  # unknown plan: no invented number
    assert cp.percent_from_steps("x", 4) is None


def test_failed_agent_run_reports_only_the_dispatch_stage():
    reply = cp.conversational_development_reply("The model stopped early.", status="failed", success=False)
    assert reply.startswith("25% done. ")


def test_mid_task_updates_use_same_format_monotonic_and_never_100():
    first, pct = cp.conversational_mid_task("Running the next project tool.", floor_percent=25)
    assert first == "25% done. I'm still working on it and will tell you as soon as the next step lands."
    second, pct = cp.conversational_mid_task(
        "60% done. I've updated the settings view and I'm running the tests now", floor_percent=25, previous_percent=pct
    )
    assert second == "60% done. I've updated the settings view and I'm running the tests now."
    third, pct = cp.conversational_mid_task("40% done. Re-reading the config.", floor_percent=25, previous_percent=pct)
    assert pct == 60 and third.startswith("60% done. ")
    final, pct = cp.conversational_mid_task("100% done. Wrapping up.", floor_percent=25, previous_percent=pct)
    assert pct == 99 and final.startswith("99% done. ")
    for text in (first, second, third, final):
        assert PERCENT_THEN_PROSE.match(text)


def test_spoken_percent_reads_naturally_and_round_trips_for_display():
    spoken = cp.spoken_progress("98% done. The dump is loaded; I'm just confirming the last check passed.")
    assert spoken == "Ninety eight percent done. The dump is loaded; I'm just confirming the last check passed."
    assert cp.display_progress(spoken).startswith("98% done. The dump is loaded")
    assert cp.spoken_progress("No percent here.") == "No percent here."


def test_spoken_execution_notice_uses_reported_percent_and_matches_remote():
    text = (
        "the IDE session ran into an issue on Snuza. Result: 50% done. The agent finished the work, "
        "but I can't call it done yet because none of the checks I can track have run and passed yet."
    )
    spoken = voice_friendly_text(text)
    assert spoken.startswith("Fifty percent done. Snuza hit a problem. The agent finished the work")
    assert remote_spoken_text(text, proactive=True) == spoken
    assert voice_friendly_text(spoken) == spoken


def test_mid_task_initiative_notices_say_progress_is_unknown_instead_of_guessing():
    idle = voice_friendly_text(
        "Cursor has been quiet on Spotify for 12 minutes. It was working on: a local path run x. "
        "Ask me to check it when you want me to step in."
    )
    assert idle == (
        "Spotify is still running, but Cursor has been quiet for 12 minutes, so I can't tell how far along it is. "
        "Ask me to check on it whenever you want me to step in."
    )
    waiting = voice_friendly_text("the IDE session for RelightSA is waiting for input. Tell me what you want it to do next.")
    assert waiting.startswith("RelightSA is paused and waiting for you")
    assert "percent" not in idle + waiting


def test_development_guidance_asks_for_percent_first_conversational_prose():
    from distr.core.turn_runtime.context import DEVELOPMENT_RESPONSE_GUIDANCE as guidance

    assert "Start every progress update and your final reply with an honest progress percentage" in guidance
    assert "complete, natural sentences" in guidance
    assert "avoid guessing progress percentages" not in guidance
    assert "Use 100% only when the work is finished and verified" in guidance


def test_harness_turn_reply_and_progress_events_are_percent_first(tmp_path, monkeypatch):
    from tests.core.test_development_harness import _database
    from distr.core.workflow import development_harness as harness
    from distr.core.turn_runtime import TurnEvent, TurnEventKind, TurnResult, TurnStatus
    import distr.core.chat_turns as chat_turns

    get_session, chat_id, _ = _database(tmp_path, monkeypatch)
    root = tmp_path / "project"
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    (root / "app.txt").write_text("before\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=T", "-c", "user.email=t@example.com",
                    "commit", "-m", "base"], check=True, capture_output=True)
    events = []
    real_create = chat_turns.create_event

    def recording_create(*args, **kwargs):
        events.append((args, kwargs))
        return real_create(*args, **kwargs)

    monkeypatch.setattr(chat_turns, "create_event", recording_create)

    async def execute(request, *, on_event, **kwargs):
        on_event(TurnEvent(kind=TurnEventKind.PROGRESS, status=TurnStatus.UPDATING, summary="Running the next project tool.",
                           runtime_id="cli_harness"))
        (root / "app.txt").write_text("implemented\n")
        on_event(TurnEvent(kind=TurnEventKind.PROGRESS, status=TurnStatus.UPDATING,
                           summary="70% done. The change is in and I'm running the tests now.", runtime_id="cli_harness"))
        on_event(TurnEvent(kind=TurnEventKind.TOOL_COMPLETED, status=TurnStatus.UPDATING, summary="Check finished",
                           runtime_id="cli_harness", tool_name="bash",
                           details={"command": "npm run test", "exit_code": 1, "call_id": "1"}))
        return TurnResult(success=True, backend_id="codex", model="fixture", runtime_id="cli_harness",
                          output="100% done. Implemented and verified. Everything passed.")

    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", execute)
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
                        lambda *a, **k: {"status": {"ready": True}})
    result = harness.dispatch_development_prompt(chat_id, "Fix the implementation and verify it", dispatch_async=False)
    progress = [kw.get("summary") for args, kw in events if kw.get("title") == "Progress update"]
    assert progress == [
        "25% done. I'm still working on it and will tell you as soon as the next step lands.",
        "70% done. The change is in and I'm running the tests now.",
    ]
    assert result["status"] == "blocked"
    summary = result["summary"]
    assert PERCENT_THEN_PROSE.match(summary)
    assert cp.leading_percent(summary) == 75  # dispatched + finished + changed, 0/1 checks passed
    assert "not independently verified" not in summary and "Verification incomplete" not in summary
    failed = [kw.get("summary") for args, kw in events if args[1:2] == ("turn_failed",)]
    assert failed and all(PERCENT_THEN_PROSE.match(text) for text in failed)


def test_waiting_turn_keeps_agents_own_percent_or_stays_conservative():
    asked = cp.conversational_development_reply(
        "40% done. Which retention window do you want for the backups?", status="waiting", success=True)
    assert asked == "40% done. Which retention window do you want for the backups?"
    silent = cp.conversational_development_reply("Which retention window do you want?", status="waiting", success=True)
    assert silent.startswith("25% done. ")
