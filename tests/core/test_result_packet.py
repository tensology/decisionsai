"""Tests for canonical multi-lane Result Packet formatting."""

from distr.core.kanban.result_packet import (
    append_workflow_step_to_packet,
    build_result_packet,
    create_initial_result_packet_for_run,
    extract_action_trace_from_step_result,
    extract_artifacts_from_step_result,
    format_result_packet_note,
    summarize_packet_for_step_context,
)


def test_result_packet_has_required_top_level_sections():
    packet = build_result_packet(
        ticket_id="123",
        execution_lane="cli",
        status="success",
        summary="Implemented requested fix.",
    )
    assert packet["ticket_id"] == "123"
    assert packet["execution_lane"] == "cli"
    assert "changes" in packet
    assert "commands" in packet
    assert "tests_and_checks" in packet
    assert "risks_and_notes" in packet
    assert "next_actions" in packet
    assert "artifacts" in packet
    assert "execution" in packet
    assert "audit" in packet


def test_result_packet_note_includes_status_lane_and_change_summary():
    packet = build_result_packet(
        ticket_id="5",
        execution_lane="cursor",
        status="partial_success",
        summary="Applied patch and left follow-up checks.",
        change_summary=["Analyze: passed", "Patch: passed"],
    )
    note = format_result_packet_note(packet, title="Workflow Run #42")
    assert "[Workflow Run #42] Status: partial_success" in note
    assert "Execution lane: cursor" in note
    assert "Analyze: passed" in note


def test_append_workflow_step_to_packet_updates_summary_and_status():
    packet = create_initial_result_packet_for_run(
        ticket_id="9",
        board_id="2",
        board_name="Main",
        project_id="4",
        project_name="DecisionsAI",
        execution_lane="cursor",
    )
    packet = append_workflow_step_to_packet(
        packet,
        step_name="Analyze Ticket",
        step_status="passed",
        step_result="Analysis complete and constraints extracted.",
        run_status="running",
    )
    packet = append_workflow_step_to_packet(
        packet,
        step_name="Apply Patch",
        step_status="failed",
        step_result="Patch failed due to context mismatch.",
        run_status="failed",
    )
    assert packet["status"] == "failed"
    assert len(packet["changes"]["change_summary"]) == 2
    assert packet["audit"]["final_verdict"] == "needs_changes"


def test_extract_artifacts_from_step_result_classifies_evidence():
    artifacts = extract_artifacts_from_step_result(
        """
        Captured screenshot at /tmp/decisions/workflow_screenshots/step_42_current.png
        Wrote run log /tmp/decisions/logs/workflow_42.log
        Patch artifact: /tmp/decisions/patches/fix-checkout.diff
        Browser trace: https://example.test/traces/42
        """
    )
    assert artifacts["screenshots"] == ["/tmp/decisions/workflow_screenshots/step_42_current.png"]
    assert artifacts["logs"] == ["/tmp/decisions/logs/workflow_42.log"]
    assert artifacts["diffs_or_patches"] == ["/tmp/decisions/patches/fix-checkout.diff"]
    assert artifacts["links"] == ["https://example.test/traces/42"]


def test_append_workflow_step_to_packet_merges_artifacts_without_duplicates():
    packet = create_initial_result_packet_for_run(
        ticket_id="9",
        board_id="2",
        board_name="Main",
        project_id="4",
        project_name="DecisionsAI",
        execution_lane="cursor",
    )
    step_result = (
        "Screenshot: /tmp/decisions/workflow_screenshots/step_1_current.png\n"
        "Screenshot: /tmp/decisions/workflow_screenshots/step_1_current.png\n"
        "Log: /tmp/decisions/logs/workflow_9.log"
    )
    packet = append_workflow_step_to_packet(
        packet,
        step_name="Validate UI",
        step_status="passed",
        step_result=step_result,
        run_status="running",
    )
    packet = append_workflow_step_to_packet(
        packet,
        step_name="Validate UI Again",
        step_status="passed",
        step_result=step_result,
        run_status="running",
    )
    assert packet["artifacts"]["screenshots"] == [
        "/tmp/decisions/workflow_screenshots/step_1_current.png"
    ]
    assert packet["artifacts"]["logs"] == ["/tmp/decisions/logs/workflow_9.log"]


def test_extract_action_trace_from_computer_use_summary():
    trace = extract_action_trace_from_step_result(
        """
        Goal: Open Downloads
        Status: Goal achieved.

        Steps taken:
          1. [click] clicked Finder in dock -> Clicked at (0.10, 0.95): True
          2. [keys] opened downloads shortcut → Pressed cmd,option,l: True
          ESC. [escalation] asked orchestrator for guidance
        """
    )
    assert trace == [
        {
            "step": "1",
            "action_type": "click",
            "description": "clicked Finder in dock",
            "result": "Clicked at (0.10, 0.95): True",
        },
        {
            "step": "2",
            "action_type": "keys",
            "description": "opened downloads shortcut",
            "result": "Pressed cmd,option,l: True",
        },
        {
            "step": "ESC",
            "action_type": "escalation",
            "description": "asked orchestrator for guidance",
            "result": "",
        },
    ]


def test_append_workflow_step_to_packet_merges_action_trace_without_duplicates():
    packet = create_initial_result_packet_for_run(
        ticket_id="9",
        board_id="2",
        board_name="Main",
        project_id="4",
        project_name="DecisionsAI",
        execution_lane="cursor",
    )
    step_result = (
        "Steps taken:\n"
        "  1. [click] open menu -> Clicked at (0.70, 0.10): True\n"
        "  2. [type] enter query -> Typed 12 chars: True"
    )
    packet = append_workflow_step_to_packet(
        packet,
        step_name="Use UI",
        step_status="passed",
        step_result=step_result,
        run_status="running",
    )
    packet = append_workflow_step_to_packet(
        packet,
        step_name="Use UI Again",
        step_status="passed",
        step_result=step_result,
        run_status="running",
    )
    assert packet["execution"]["action_trace"] == [
        {
            "step": "1",
            "action_type": "click",
            "description": "open menu",
            "result": "Clicked at (0.70, 0.10): True",
        },
        {
            "step": "2",
            "action_type": "type",
            "description": "enter query",
            "result": "Typed 12 chars: True",
        },
    ]


def test_summarize_packet_for_step_context_contains_recent_changes():
    packet = build_result_packet(
        ticket_id="88",
        execution_lane="cursor",
        status="running",
        summary="Live run summary.",
        change_summary=["Step A: passed", "Step B: passed", "Step C: failed"],
        screenshots=["/tmp/decisions/workflow_screenshots/step_c_current.png"],
        logs=["/tmp/decisions/logs/workflow_88.log"],
        action_trace=[
            {
                "step": "3",
                "action_type": "click",
                "description": "pressed submit",
                "result": "Clicked at (0.80, 0.20): True",
            }
        ],
        final_verdict="cannot_determine",
    )
    text = summarize_packet_for_step_context(packet, max_lines=2)
    assert "[RESULT PACKET CONTEXT]" in text
    assert "status: running" in text
    assert "Step B: passed" in text
    assert "Step C: failed" in text
    assert "screenshot: /tmp/decisions/workflow_screenshots/step_c_current.png" in text
    assert "log: /tmp/decisions/logs/workflow_88.log" in text
    assert "click: pressed submit -> Clicked at (0.80, 0.20): True" in text
