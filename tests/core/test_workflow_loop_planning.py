"""Tests for loop-aware workflow planning (elorm.xyz loop patterns)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from distr.core.workflow.loop_catalog import ELORM_LOOP_KICKOFFS, infer_loop_archetype
from distr.core.workflow.loop_preset_loader import load_bundle_by_name
from distr.core.workflow.planning import (
    WORKFLOW_LOOP_MAX_STEPS,
    _normalize_plan_steps,
    build_loop_context_summary,
    is_loop_description,
    loop_contract_to_context_rules,
    parse_loop_contract,
    parse_loop_plan_response,
    plan_workflow,
)


@pytest.mark.parametrize("entry", ELORM_LOOP_KICKOFFS, ids=lambda e: e["name"])
def test_parse_loop_contract_elorm_catalog(entry):
    """Every bundled preset should have a parseable kickoff and loop contract."""
    kickoff = entry["kickoff"]
    assert is_loop_description(kickoff)
    parsed = parse_loop_contract(kickoff)
    bundle = load_bundle_by_name(entry["name"]) or {}
    loop_contract = bundle.get("loop_contract") or {}
    assert parsed["name"] == entry["name"]
    assert parsed.get("goal") or loop_contract.get("goal")
    if entry.get("expected_max_iterations") is not None:
        assert loop_contract.get("max_iterations") == entry["expected_max_iterations"]
    if entry.get("expected_check_command"):
        assert loop_contract.get("check_command") == entry["expected_check_command"]
    assert loop_contract.get("step_1") or parsed.get("step_1")
    assert loop_contract.get("archetype") == entry["archetype"]


def test_parse_loop_contract_senior_engineer_guardrails():
    bundle = load_bundle_by_name("Senior Software Engineer: Ticket to Green") or {}
    loop_contract = bundle.get("loop_contract") or {}
    assert len(loop_contract.get("guardrails") or []) >= 2
    rules = loop_contract_to_context_rules(loop_contract)
    assert "Guardrails" in rules
    assert "Do not modify the check command" in rules


def test_infer_loop_archetypes():
    senior = next(e for e in ELORM_LOOP_KICKOFFS if e["name"] == "Senior Software Engineer: Ticket to Green")
    assert infer_loop_archetype(senior["kickoff"]) == "incremental_ship"


def test_normalize_plan_steps_caps_at_fourteen():
    raw = [
        {"title": f"Step {i}", "instruction": f"Do task {i}", "action_type": "agent_instruction"}
        for i in range(20)
    ]
    normalized = _normalize_plan_steps(raw, "bulk task")
    assert len(normalized) == WORKFLOW_LOOP_MAX_STEPS


def test_parse_loop_plan_response_object_and_array():
    obj = parse_loop_plan_response(json.dumps({
        "name": "Build Until Green",
        "loop_contract": {"goal": "build succeeds", "max_iterations": 10},
        "steps": [{"title": "Fix", "instruction": "fix build", "action_type": "send_to_project_cli"}],
    }))
    assert obj and obj["steps"]
    arr = parse_loop_plan_response(json.dumps([{"title": "A", "instruction": "x", "action_type": "agent_instruction"}]))
    assert arr and arr["steps"]


def test_build_loop_context_summary_includes_iteration():
    summary = build_loop_context_summary(
        {"goal": "green build", "max_iterations": 4, "check_command": "npm run build"},
        iteration=1,
    )
    assert "[LOOP CONTRACT]" in summary
    assert "Iteration: 2 of 4" in summary


def test_build_loop_context_summary_includes_step1_and_ticket_title():
    summary = build_loop_context_summary(
        {
            "goal": "clean diff",
            "step_1": "Review diff for debug code and dead branches.",
            "max_iterations": 4,
        },
        iteration=0,
        ticket_title="Fix checkout flow",
    )
    assert "Ticket: Fix checkout flow" in summary
    assert "First step focus: Review diff for debug code" in summary
    assert "Iteration: 1 of 4" in summary


def test_call_planning_llm_fallback_chain_tries_next_tier():
    tiers = [("coding", "openai", "gpt-4o"), ("conversational", "ollama", "llama3.2")]
    calls = []

    def fake_call(prompt, provider, model, settings):
        calls.append((provider, model))
        if len(calls) == 1:
            raise RuntimeError("tier failed")
        return '[{"title":"S","instruction":"x","action_type":"agent_instruction"}]'

    with patch("distr.core.workflow.planning._planning_model_tiers", return_value=tiers):
        with patch("distr.core.workflow.planning.call_planning_llm", side_effect=fake_call):
            from distr.core.workflow.planning import _call_llm_for_plan

            result = _call_llm_for_plan("do something moderately complex with multiple steps and checks")
    assert result is not None
    assert len(calls) == 2


def test_plan_workflow_persists_loop_contract_and_goto(monkeypatch):
    senior = next(e for e in ELORM_LOOP_KICKOFFS if e["name"] == "Senior Software Engineer: Ticket to Green")
    mock_plan = {
        "name": "Senior Software Engineer: Ticket to Green",
        "planning_model_tier": "test",
        "loop_contract": {
            "goal": "ticket green",
            "max_iterations": 8,
            "check_command": "project safety net discovery command",
            "exit_when": "plan.md attached and all gates green",
            "guardrails": ["Do not bypass checks"],
        },
        "steps": [
            {
                "title": "Ingest ticket",
                "instruction": "Read ticket and project context",
                "action_type": "send_to_project_cli",
                "verification": "Ticket context is clear",
                "on_fail_goto_position": None,
            },
            {
                "title": "Run safety nets",
                "instruction": "Run project checks",
                "action_type": "run_command",
                "config": {"command": "project safety net discovery command"},
            },
            {
                "title": "Evaluate exit",
                "instruction": "Decide if exit met",
                "action_type": "agent_instruction",
                "on_fail_goto_position": 0,
            },
            {
                "title": "Report",
                "instruction": "Summarize outcome",
                "action_type": "agent_instruction",
            },
        ],
    }

    with patch("distr.core.workflow.planning._call_llm_for_loop_plan", return_value=mock_plan):
        wf_id = plan_workflow(senior["kickoff"], name="My Senior SWE Loop")

    assert wf_id is not None
    from distr.core.workflow.service import get_workflow

    wf = get_workflow(wf_id)
    assert wf["name"] == "My Senior SWE Loop"
    assert wf["context_rules"]
    assert "Do not bypass checks" in wf["context_rules"]
    assert len(wf["steps"]) == 4
    assert wf["steps"][2]["on_fail_goto"] == wf["steps"][0]["id"]

    workflow_input = json.loads(wf.get("workflow_input") or "{}")
    assert workflow_input.get("max_iterations") == 8
    assert "project safety net" in workflow_input.get("check_command", "")
