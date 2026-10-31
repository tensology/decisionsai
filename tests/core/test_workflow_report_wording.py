"""A step that did not clear must not be reported as completed."""

from __future__ import annotations

from distr.core.workflow_engine.agent_bridge import WorkflowAgentBridge

_BACKEND_RESULT = (
    "Project CLI backend: cursor\n"
    "Project: 2\n"
    "Status: completed\n\n"
    "I finished the turn."
)


def _report(status: str, *, success: bool) -> str:
    return WorkflowAgentBridge._generate_report(
        {
            "success": success,
            "cancelled": False,
            "steps_summary": [
                {
                    "title": "Implement the planned change",
                    "status": status,
                    "result": _BACKEND_RESULT,
                }
            ],
        }
    )


def test_a_step_that_did_not_clear_is_not_called_completed():
    report = _report("failed", success=False)

    assert "didn't clear" in report
    assert "stopped before this part was finished" in report
    # Must not claim the work was finished in any form.
    assert "finished this part of the work" not in report


def test_a_passing_step_reads_as_finished_in_plain_words():
    report = _report("passed", success=True)

    assert "I finished the turn." in report
    assert "Cursor:" in report
    assert "didn't clear" not in report


def test_a_cancelled_run_says_it_was_stopped():
    report = WorkflowAgentBridge._generate_report(
        {"success": False, "cancelled": True, "steps_summary": []}
    )

    assert report.startswith("I stopped that run.")


def test_public_worker_result_survives_packet_review_and_final_chat(monkeypatch):
    from unittest.mock import Mock
    from distr.core.kanban.result_packet import append_workflow_step_to_packet
    from distr.core.workflow.dispatcher import _finalize_development_chat_turn, _finalize_result_packet_for_terminal_run

    public_result = (
        "Project CLI backend: pi\nProject: 2\nStatus: completed\n\n"
        "<think>Private reasoning must not be shown.</think>\n"
        "<tool_call>Raw tool arguments must not be shown.</tool_call>\n"
        "Acceptance check passed. The actual command printed TELEGRAM_PROPOSAL_E2E_OK.\n"
        "API_KEY=sample-secret-value\nNo files were changed."
    )
    packet = append_workflow_step_to_packet({}, step_name="Critical session", step_status="passed", step_result=public_result, run_status="running")
    assert "TELEGRAM_PROPOSAL_E2E_OK" in packet["human_summary"]
    assert "Private reasoning" not in packet["human_summary"]
    assert "Raw tool arguments" not in packet["human_summary"]
    assert "sample-secret-value" not in packet["human_summary"]
    assert "Pi:" in packet["human_summary"]
    packet = append_workflow_step_to_packet(packet, step_name="Independent review", step_status="passed", step_result=_BACKEND_RESULT, run_status="completed")
    packet = _finalize_result_packet_for_terminal_run(packet, run_id=173, status="completed", risk_profile={})
    assert "TELEGRAM_PROPOSAL_E2E_OK" in packet["summary"]
    assert "Project CLI backend:" not in packet["summary"]
    run_result = {"success": True, "result_packet": packet, "steps_summary": []}
    complete, append = Mock(), Mock()
    monkeypatch.setattr("distr.core.chat_turns.latest_active_turn_id", lambda chat_id: 352)
    monkeypatch.setattr("distr.core.chat_turns.complete_turn", complete)
    monkeypatch.setattr("distr.core.chat.ChatService.append_assistant_notice", append)
    _finalize_development_chat_turn(349, "completed", run_result)
    saved_text = append.call_args.args[1]
    assert saved_text == packet["human_summary"]
    assert "TELEGRAM_PROPOSAL_E2E_OK" in saved_text
    complete.assert_called_once_with(349, turn_id=352, display_text=saved_text)


def test_final_worker_answer_keeps_its_conclusion_when_bounded():
    from distr.core.kanban.result_packet import append_workflow_step_to_packet

    output = "Project CLI backend: pi\nProject: 2\nStatus: completed\n\n" + "Public verification detail. " * 600 + "\nFinal result: TELEGRAM_PROPOSAL_E2E_OK"
    packet = append_workflow_step_to_packet({}, step_name="Implementation", step_status="passed", step_result=output, run_status="completed")
    assert len(packet["human_summary"]) <= 5600
    assert "TELEGRAM_PROPOSAL_E2E_OK" in packet["human_summary"]


def test_raw_cli_logs_do_not_become_the_public_worker_answer():
    output = "Project CLI backend: codex\nProject: 2\nStatus: completed\n\nReading additional input from stdin...\ncallback_url=http://localhost/private"
    assert WorkflowAgentBridge.public_project_cli_result(output) == ""
    assert "callback_url" not in WorkflowAgentBridge._human_step_result(output)


def test_public_answer_omits_duplicate_trailing_handoff_but_preserves_contract_only_result():
    header = "Project CLI backend: pi\nProject: 2\nStatus: completed\n\n"
    contract = "Status: completed\nSummary: Acceptance check passed.\nFiles changed: none\nCommands run: pwd; printf marker\nBlockers: none"
    report = WorkflowAgentBridge.public_project_cli_result(header + "Printed TELEGRAM_PROPOSAL_E2E_OK in the correct project.\n\n" + contract)
    assert report == "Pi: Printed TELEGRAM_PROPOSAL_E2E_OK in the correct project."
    assert "Acceptance check passed." in WorkflowAgentBridge.public_project_cli_result(header + contract)


def test_failed_and_cancelled_runs_do_not_promote_an_earlier_success():
    from distr.core.workflow.dispatcher import _finalize_result_packet_for_terminal_run

    for status in ("failed", "cancelled"):
        packet = _finalize_result_packet_for_terminal_run({"human_summary": "All done."}, run_id=173, status=status, risk_profile={})
        assert not packet.get("human_summary")
        assert status in packet["summary"]
        report = WorkflowAgentBridge._generate_report({"success": False, "cancelled": status == "cancelled", "result_packet": packet})
        assert not report.startswith("Done.")
