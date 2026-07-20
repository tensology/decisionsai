import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from distr.core.kanban.ticket_workflow_engagement import (
    notify_ticket_workflow_progress,
    prepare_workflow_voice_text,
    record_ticket_workflow_elapsed,
)
from distr.core.workflow.router import StepRouter
from distr.core.workflow.risk_and_audit import infer_risk_profile
from distr.core.workflow.ticket_contract import (
    classify_ticket_execution,
    existing_work_satisfies_contract,
    research_review_has_evidence,
    result_reports_completion,
    step_scope_overlay,
)
from distr.core.orchestration_events import build_orchestration_notification
from distr.core.workflow.dispatcher import _finalize_result_packet_for_terminal_run


RESEARCH_TICKET = """
Research and design direction for the artist homepage.
Non-goals:
- No code changes.
- Browser screenshots are not required; evidence notes are sufficient.
"""

COMPLETE_REPORT = """
Status: completed
Summary: All acceptance criteria met and all research deliverables verified.
Files changed: docs/artist-design-direction.md, docs/evidence/source-capture.md
Blockers: none
"""


def test_research_ticket_contract_disables_code_and_implicit_ui_gates():
    profile = classify_ticket_execution(RESEARCH_TICKET)

    assert profile["kind"] == "research_documentation"
    assert profile["implementation_required"] is False
    assert profile["repository_checks_required"] is False
    assert profile["ui_evidence_required"] is False
    risk = infer_risk_profile(RESEARCH_TICKET + "\nHomepage design and player research")
    assert risk["risk_type"] == "research_documentation"
    assert risk["level"] == "low"


def test_research_completion_requires_artifacts_and_no_blockers():
    assert result_reports_completion(COMPLETE_REPORT) is True
    assert research_review_has_evidence(COMPLETE_REPORT) is True
    assert result_reports_completion("Status: completed\nBlockers: none") is False
    assert result_reports_completion(COMPLETE_REPORT.replace("Blockers: none", "Blockers: missing credentials")) is False
    screenshot_contract = RESEARCH_TICKET + "\nBrowser evidence required: screenshot."
    assert existing_work_satisfies_contract(screenshot_contract, COMPLETE_REPORT) is False
    assert existing_work_satisfies_contract(
        screenshot_contract,
        COMPLETE_REPORT + "\nScreenshot: docs/evidence/home.png",
    ) is False


def test_research_overlay_reframes_generic_implementation_step():
    overlay = step_scope_overlay("Implement the planned change", RESEARCH_TICKET)

    assert "explicit no-code contract" in overlay
    assert "research, documentation, and evidence" in overlay


def test_research_overlay_makes_explicit_browser_evidence_non_optional():
    ticket = RESEARCH_TICKET + "\nBrowser evidence required: screenshots of Spotify and YouTube."

    review = step_scope_overlay("Independently review and validate", ticket)
    ingest = step_scope_overlay("Understand ticket and acceptance criteria", ticket)

    assert "screenshots/recordings are not N/A" in review
    assert "single explicitly identified preservation surface" in ingest
    assert "do not explore unrelated implementation components" in ingest


def test_ingestion_short_circuits_existing_research_artifacts_to_report():
    router = StepRouter()
    run = SimpleNamespace(
        id=8,
        workflow_id=3,
        run_data=json.dumps({"ticket_workflow_brief": RESEARCH_TICKET}),
    )
    ingest = SimpleNamespace(id=10, position=0, name="Understand ticket")
    report = SimpleNamespace(id=70, name="Report and compact memory")

    with patch.object(router, "_report_step", return_value=report):
        target = router._apply_ticket_contract_routing(
            MagicMock(),
            run,
            ingest,
            20,
            verified_passed=True,
            result=COMPLETE_REPORT,
        )

    assert target == 70
    state = json.loads(run.run_data)
    assert state["already_satisfied_short_circuit"] is True


def test_repeated_validator_disagreement_stops_after_second_identical_gate():
    router = StepRouter()
    run = SimpleNamespace(id=8, run_data="{}")
    review = SimpleNamespace(id=40)
    snapshot = {"expected": "Acceptance criteria are covered and evidence exists."}

    router._record_validation_progress(
        run,
        review,
        caller_passed=True,
        verified_passed=False,
        validation_snapshot=snapshot,
    )
    router._record_validation_progress(
        run,
        review,
        caller_passed=True,
        verified_passed=False,
        validation_snapshot=snapshot,
    )

    state = json.loads(run.run_data)
    assert state["validation_stalled_step_id"] == 40
    assert state["validation_stall_count"] == 2


def test_new_actionable_validation_finding_does_not_count_as_identical_stall():
    router = StepRouter()
    run = SimpleNamespace(id=8, run_data="{}")
    review = SimpleNamespace(id=40)

    router._record_validation_progress(
        run,
        review,
        caller_passed=True,
        verified_passed=False,
        validation_snapshot={"expected": "Acceptance criteria are covered."},
    )
    router._record_validation_progress(
        run,
        review,
        caller_passed=True,
        verified_passed=False,
        validation_snapshot={
            "expected": "Acceptance criteria are covered.",
            "correction_hint": "Capture the missing Spotify and YouTube screenshots.",
            "ticket_acceptance_findings": [{"code": "missing_browser_media"}],
        },
    )

    state = json.loads(run.run_data)
    assert "validation_stalled_step_id" not in state
    assert state["validation_progress"]["40"]["count"] == 1


def test_repeated_validator_disagreement_ends_without_spending_a_reporting_model_call():
    router = StepRouter()
    run = SimpleNamespace(
        id=8,
        workflow_id=3,
        run_data=json.dumps({
            "ticket_workflow_brief": RESEARCH_TICKET,
            "validation_stalled_step_id": 40,
        }),
    )
    step = SimpleNamespace(id=40, position=0, name="Understand ticket")

    target = router._apply_ticket_contract_routing(
        MagicMock(),
        run,
        step,
        41,
        verified_passed=False,
        result="Status: completed\nSummary: malformed handoff",
    )

    assert target == -1
    state = json.loads(run.run_data)
    assert state["forced_terminal_status"] == "failed"
    assert "stopped instead of looping" in state["terminal_warning"]


def test_routine_workflow_progress_is_feed_only():
    with (
        patch(
            "distr.core.kanban.ticket_workflow_engagement._run_context",
            return_value={"workflow_id": 3, "ticket_id": 4, "board_id": 5},
        ),
        patch("distr.core.workflow.chat_trace.record_workflow_chat_event") as chat_event,
        patch("distr.core.orchestration_events.emit_user_notification") as ledger_event,
        patch("distr.core.human_engagement.HumanEngagementService.decide") as decide,
    ):
        notify_ticket_workflow_progress(
            run_id=8,
            step_id=40,
            body="Step 4 of 7 started.",
            state_fingerprint="step:40",
            audible=False,
        )

    chat_event.assert_called_once()
    ledger_event.assert_called_once()
    decide.assert_not_called()


def test_workflow_voice_text_keeps_the_decision_and_drops_internal_telemetry():
    spoken = prepare_workflow_voice_text(
        "Workflow run #108 for ticket #178 reached step 3 of 7 at 2026-07-19 14:05. "
        "I need your approval before switching to the recommended free model. "
        "Elapsed 12m 4s. Details: /Users/paul/project/settings_local.py"
    )

    assert spoken == (
        "This workflow for the ticket reached this phase. "
        "I need your approval before switching to the recommended free model."
    )
    assert "108" not in spoken
    assert "178" not in spoken
    assert "2026" not in spoken
    assert "14:05" not in spoken
    assert "Elapsed" not in spoken
    assert "/Users/" not in spoken


def test_worker_session_lifecycle_is_not_an_audible_notification():
    dispatched = build_orchestration_notification({
        "event_type": "worker_dispatched",
        "source": "pi",
        "subtype": "execution_session_created",
        "summary": "Project execution session created.",
    })
    step_completed = build_orchestration_notification({
        "event_type": "worker_completed",
        "source": "pi",
        "step_id": 40,
        "subtype": "execution_session_completed",
        "summary": "Project execution session completed.",
    })
    run_completed = build_orchestration_notification({
        "event_type": "worker_completed",
        "source": "workflow",
        "step_id": None,
        "subtype": "workflow_run_completed",
        "summary": "Workflow completed.",
    })

    assert dispatched["should_notify"] is False
    assert step_completed["should_notify"] is False
    assert run_completed["should_notify"] is True


def test_terminal_packet_uses_the_same_status_as_the_run():
    packet = _finalize_result_packet_for_terminal_run(
        {"status": "partial_success", "summary": "Workflow finished with status: completed."},
        run_id=105,
        status="failed",
        risk_profile={"level": "low"},
    )

    assert packet["status"] == "failed"
    assert packet["summary"] == "Workflow run 105 finished with status: failed."


def test_run_completion_speaks_this_run_elapsed_not_cumulative_ticket_time():
    started = datetime(2026, 7, 18, 18, 44, 51)
    run = SimpleNamespace(
        started_at=started,
        completed_at=started + timedelta(hours=1, seconds=6),
        run_data="{}",
        status="completed",
    )
    ticket = SimpleNamespace(title="Research artist", time_spent="13m 7s")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [run, ticket]
    session = MagicMock()
    session.__enter__.return_value = db
    session.__exit__.return_value = False

    with (
        patch("distr.core.kanban.ticket_workflow_engagement.get_session", return_value=session),
        patch("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress") as notify,
    ):
        record_ticket_workflow_elapsed(ticket_id=177, run_id=105, status="completed")

    message = notify.call_args.kwargs["body"]
    assert "Elapsed 1h 6s" in message
    assert "1h 13m" not in message
    assert notify.call_args.kwargs["audible"] is True
