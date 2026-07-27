from distr.core.workflow.handoff_packet import (
    StepHandoffPacket,
    extract_required_handoff_fields,
    extract_source_urls,
    extract_ticket_contract,
    handoff_budget_for_role,
    select_relevant_memory,
)


def test_required_named_context_is_extracted_without_adjacent_report_fields():
    report = """## Context Packet
**context_packet:** Existing brief at `docs/design.md`; only two screenshots remain.
Reuse the existing source inventory.

**unknowns:** whether the current draft is approved
Status: completed
Summary: context assembled
"""

    extracted = extract_required_handoff_fields(report, ["context_packet"])

    assert "Existing brief" in extracted
    assert "Reuse the existing source inventory" in extracted
    assert "unknowns" not in extracted
    assert "Status" not in extracted


def test_reporting_context_preserves_concrete_test_evidence_via_semantic_aliases():
    report = """Status: completed
Summary: Ran the exact named suite.
Tests run:
`/Users/paul/.virtualenvs/decisions/bin/python -m pytest tests/core/test_example.py`
7 passed in 0.55s
Exit code: `0`
Drift check: None.
Files changed: none.
Blockers: none.
Ship verdict: pass.
"""

    extracted = extract_required_handoff_fields(
        report,
        ["final_changed_files", "command_log", "evidence", "memory_delta"],
    )

    assert "7 passed in 0.55s" in extracted
    assert "Exit code: 0" in extracted
    assert "Files changed: none" in extracted
    assert "Blockers: none" in extracted


def test_source_urls_are_extracted_exactly_for_critical_handoff_references():
    urls = extract_source_urls(
        "Spotify https://open.spotify.com/artist/abc?si=123 and "
        "YouTube https://www.youtube.com/channel/UC123."
    )

    assert urls == [
        "https://open.spotify.com/artist/abc?si=123",
        "https://www.youtube.com/channel/UC123",
    ]


def test_ticket_contract_extractor_preserves_late_acceptance_and_browser_sections():
    ticket = """Objective:
Research the artist.

Non-goals:
- No code changes.

Acceptance criteria:
- Produce the source-backed brief.

Browser evidence required:
- Screenshots of Spotify and YouTube pages.

Expected artifacts:
- docs/evidence/spotify.png

Unrelated narrative:
This should not be promoted.
"""

    contract = extract_ticket_contract(ticket)

    assert "No code changes" in contract
    assert "Produce the source-backed brief" in contract
    assert "Screenshots of Spotify and YouTube" in contract
    assert "docs/evidence/spotify.png" in contract
    assert "Unrelated narrative" not in contract


def test_step_handoff_packet_deduplicates_references_and_reports_section_costs():
    packet = StepHandoffPacket(
        identity={"workflow_id": 369, "run_id": 104, "ticket_id": 177},
        objective="Rebuild the artist homepage and preserve checkout.",
        current_step={"title": "Implement", "instruction": "Copy source files first, then edit them."},
        workflow_map="· 1. Plan [planning; codex / auto]\n→ 2. Implement [implementation; pi / ornith:35b]",
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
    assert "Whole-run coordination map" in prompt
    assert "ornith:35b" in prompt
    assert telemetry["total_chars"] == len(prompt)
    assert telemetry["section_chars"]["current_step"] > 0
    assert telemetry["reference_count"] == 2


def test_current_step_guardrails_precede_project_and_evidence_references():
    packet = StepHandoffPacket(
        identity={"run_id": 142, "step": "Confirm contract"},
        objective="Follow the repository instructions in AGENTS.md.",
        required_context="context_packet: AGENTS.md was already inspected by planning.",
        current_step={
            "title": "Confirm contract",
            "instruction": (
                "[TOOL-FREE EXECUTION — HIGHEST PRIORITY]\n"
                "Do not read files, invoke tools, or emit tool-call markup."
            ),
        },
        memory_refs=["/tmp/project/AGENTS.md"],
        return_contract="Status: completed",
    )

    prompt, _telemetry = packet.render(max_chars=8_000)

    guardrail_position = prompt.index("TOOL-FREE EXECUTION")
    assert guardrail_position < prompt.index("AGENTS.md")
    assert guardrail_position < prompt.index("## Evidence and memory references")
    assert prompt.index("## Current step") < prompt.index("## Objective and ticket context")


def test_current_step_stays_before_references_when_packet_is_compacted():
    packet = StepHandoffPacket(
        identity={"run_id": 142},
        objective="Large supporting narrative mentioning AGENTS.md. " * 800,
        current_step={"title": "Synthesize", "instruction": "DO NOT USE TOOLS"},
        memory_refs=["/tmp/project/AGENTS.md"],
        return_contract="Status: completed",
    )

    prompt, telemetry = packet.render(max_chars=2_400)

    assert telemetry["compacted"] is True
    assert prompt.index("DO NOT USE TOOLS") < prompt.index("AGENTS.md")


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
    assert telemetry["raw_total_chars"] > telemetry["total_chars"]
    assert telemetry["estimated_tokens_saved"] > 0
    assert telemetry["compacted"] is True


def test_compacted_handoff_preserves_non_negotiable_ticket_contract():
    packet = StepHandoffPacket(
        identity={"run_id": 1},
        objective="long narrative " * 1000,
        ticket_contract=(
            "Acceptance criteria:\n- Brief exists.\n\n"
            "Browser evidence required:\n- Spotify and YouTube screenshots."
        ),
        current_step={"title": "Review", "instruction": "Validate the artifacts."},
        return_contract="Status: completed",
    )

    prompt, _telemetry = packet.render(max_chars=3000)

    assert "## Non-negotiable ticket contract" in prompt
    assert "Spotify and YouTube screenshots" in prompt
    assert "Validate the artifacts" in prompt


def test_compacted_handoff_preserves_required_upstream_context():
    packet = StepHandoffPacket(
        identity={"workflow": "Development", "step": "Confirm contract"},
        objective="Long objective " * 900,
        ticket_contract="Acceptance criteria:\n- Preserve the player",
        required_context=(
            "context_packet: Existing brief docs/design.md already covers content and tone; "
            "only browser evidence remains."
        ),
        current_step={"title": "Confirm contract", "instruction": "Reuse upstream evidence."},
        memory_refs=["/tmp/project/context.md"],
        return_contract="Status: completed",
    )

    prompt, telemetry = packet.render(max_chars=2_000)

    assert telemetry["compacted"] is True
    assert "## Required upstream context" in prompt
    assert "only browser evidence remains" in prompt


def test_compacted_handoff_preserves_latest_human_steering():
    steer = (
        "Do not recapture evidence. Reuse docs/evidence/source-a.png and "
        "docs/evidence/source-b.png; make only the missing documentation change."
    )
    packet = StepHandoffPacket(
        identity={"run_id": 1},
        objective="Long objective " * 900,
        ticket_contract="Acceptance criteria:\n- Preserve existing evidence.",
        required_context="execution_contract: Existing screenshots are complete.",
        current_step={"title": "Implement", "instruction": "Update the brief only."},
        continuation=steer,
        return_contract="Status: completed",
    )

    prompt, telemetry = packet.render(max_chars=3_000)

    assert telemetry["compacted"] is True
    assert "## Latest human steering" in prompt
    assert "docs/evidence/source-a.png" in prompt
    assert "make only the missing documentation change" in prompt


def test_compacted_handoff_preserves_reference_paths_and_complete_statements():
    packet = StepHandoffPacket(
        identity={"run_id": 1},
        objective=("First complete sentence. " * 300) + "Final complete sentence.",
        current_step={"title": "Inspect", "instruction": "Read only."},
        memory_refs=["/tmp/project/memory/active.md", "/tmp/project/memory/handoff.md"],
        return_contract="Status: completed",
    )

    prompt, telemetry = packet.render(max_chars=1800)

    assert "/tmp/project/memory/active.md" in prompt
    assert "/tmp/project/memory/handoff.md" in prompt
    assert "[section compacted]" in prompt
    assert telemetry["compacted"] is True


def test_step_handoff_budgets_are_role_aware_and_bounded():
    assert handoff_budget_for_role("reporting") < handoff_budget_for_role("planning")
    assert handoff_budget_for_role("planning") < handoff_budget_for_role("implementation")
    assert handoff_budget_for_role("implementation") <= 8_000
