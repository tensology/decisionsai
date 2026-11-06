"""Prove budget/readiness gates and harness fallback chains are gone."""
from __future__ import annotations

from distr.core.workflow.blueprint_adherence import (
    consume_run_power_budget,
    power_budget_is_exhausted,
)
from distr.core.project_cli_backends.model_policy import build_auto_fallback_chain
from distr.core.workflow.step_executor import StepExecutorMixin
from distr.core.workflow.execution_mode import (
    choose_development_execution_mode,
    choose_proposal_execution_mode,
)


def test_power_budget_never_gates():
    exhausted = {
        "max_turns": 1,
        "turns_used": 99,
        "max_tokens": 1,
        "tokens_used": 99_999,
        "estimated_cost_usd": 50.0,
        "exhausted": True,
    }
    assert power_budget_is_exhausted(exhausted) is False
    run_data, interrupt = consume_run_power_budget(
        {"power_budget": dict(exhausted)}, turns=1, tokens=5000, model_provider="openai"
    )
    assert interrupt is None
    assert run_data["power_budget"]["exhausted"] is False


def test_no_runtime_fallback_chain_or_route_switch():
    assert build_auto_fallback_chain({"backend": "pi", "model": "auto"}) == []
    assert build_auto_fallback_chain({"backend": "codex", "model": "gpt-5"}) == []
    route = {"backend": "codex", "model": "auto", "fallback_backend": "claude_code"}
    kept = StepExecutorMixin._ready_route_or_fallback(route, project=None, board=None)
    assert kept["backend"] == "codex"
    assert "source" not in kept or kept.get("source") != "step_readiness_fallback"


def test_prompts_and_tickets_default_to_direct_not_workflow_shell():
    decision = choose_development_execution_mode(
        "Plan, implement, test, and independently review this end-to-end across the codebase"
    )
    assert decision["mode"] == "direct"
    proposal = choose_proposal_execution_mode(
        {
            "title": "Migration and architecture refactor",
            "description": "Use a workflow with independent review across the project",
            "tasks": ["a", "b", "c", "d", "e"],
        }
    )
    assert proposal["mode"] == "direct"
