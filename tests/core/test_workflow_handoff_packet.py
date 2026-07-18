from distr.core.workflow.handoff_packet import StepHandoffPacket, select_relevant_memory


def test_step_handoff_packet_deduplicates_references_and_reports_section_costs():
    packet = StepHandoffPacket(
        identity={"workflow_id": 369, "run_id": 104, "ticket_id": 177},
        objective="Rebuild the artist homepage and preserve checkout.",
        current_step={"title": "Implement", "instruction": "Copy source files first, then edit them."},
        constraints=["Browser evidence is required.", "Browser evidence is required."],
        prior_outcomes=[{"title": "Plan", "status": "completed", "summary": "Plan saved."}],
        artifact_refs=["docs/plan.md", "docs/plan.md"],
        memory_refs=["Project handoff: /tmp/handoff.md"],
        memory_facts=["The user requires files to be copied before editing."],
        return_contract="Status: completed | failed | needs_input",
    )

    prompt, telemetry = packet.render(max_chars=8000)

    assert prompt.count("docs/plan.md") == 1
    assert prompt.count("Browser evidence is required.") == 1
    assert "Copy source files first" in prompt
    assert telemetry["total_chars"] == len(prompt)
    assert telemetry["section_chars"]["current_step"] > 0
    assert telemetry["reference_count"] == 2


def test_relevant_memory_prefers_constraints_that_match_current_step():
    selected = select_relevant_memory(
        [
            "The user likes conversational release notes.",
            "Copy checkout files from That Shirt Show before editing. Browser acceptance evidence is mandatory.",
            "Use Telegram for progress updates.",
        ],
        query="Implement checkout by copying source files and validate in browser",
        max_items=2,
    )

    assert selected
    assert "Copy checkout files" in selected[0]


def test_step_handoff_packet_honours_total_budget_and_preserves_current_step():
    packet = StepHandoffPacket(
        identity={"run_id": 1},
        objective="objective " * 2000,
        current_step={"title": "Audit", "instruction": "RUN THE REAL PLAYWRIGHT CHECK"},
        constraints=["constraint " * 1000],
        return_contract="Status: completed",
    )

    prompt, telemetry = packet.render(max_chars=3000)

    assert len(prompt) <= 3000
    assert "RUN THE REAL PLAYWRIGHT CHECK" in prompt
    assert telemetry["max_chars"] == 3000
