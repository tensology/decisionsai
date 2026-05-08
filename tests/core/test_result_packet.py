"""Tests for canonical multi-lane Result Packet formatting."""

from distr.core.kanban.result_packet import (
    append_workflow_step_to_packet,
    build_result_packet,
    create_initial_result_packet_for_run,
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


def test_summarize_packet_for_step_context_contains_recent_changes():
    packet = build_result_packet(
        ticket_id="88",
        execution_lane="cursor",
        status="running",
        summary="Live run summary.",
        change_summary=["Step A: passed", "Step B: passed", "Step C: failed"],
        final_verdict="cannot_determine",
    )
    text = summarize_packet_for_step_context(packet, max_lines=2)
    assert "[RESULT PACKET CONTEXT]" in text
    assert "status: running" in text
    assert "Step B: passed" in text
    assert "Step C: failed" in text
