"""Tests for orchestrator LLM step harness suggestions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from distr.core.workflow.step_harness import (
    derive_action_type_from_ui_tools,
    suggest_step_harness_llm,
    _ui_tools_from_harness,
)


def test_derive_action_type_from_ui_tools():
    assert derive_action_type_from_ui_tools(["computer_use"]) == "computer_use"
    assert derive_action_type_from_ui_tools(["playwright"]) == "playwright"
    assert derive_action_type_from_ui_tools(["cli"]) == "send_to_project_cli"
    assert derive_action_type_from_ui_tools(["other"]) == "agent_instruction"


def test_ui_tools_from_harness_playwright():
    assert "playwright" in _ui_tools_from_harness("playwright", ["browser", "playwright"])


def test_suggest_step_harness_llm_falls_back_without_model():
    with patch("distr.core.orchestrator.get_orchestrator_role_model", return_value=("", "")):
        result = suggest_step_harness_llm(
            instruction="Fix checkout flow and validate in the browser",
            guardrail="Stay on ticket scope",
            validation_prompt="Checkout completes without console errors",
        )
    assert result["source"] == "heuristic"
    assert isinstance(result.get("skills"), list)
    assert isinstance(result.get("ui_tools"), list)


def test_suggest_step_harness_llm_parses_response():
    import sys

    mock_litellm = MagicMock()
    mock_litellm.completion.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"skills":["tdd-workflow"],"tools":["cli"],"wait_for_continue":false,"rationale":"Unit work"}'))]
    )
    with patch("distr.core.orchestrator.get_orchestrator_role_model", return_value=("openai", "gpt-test")), patch(
        "distr.core.skills.catalog.orchestrator_skill_catalog",
        return_value=[{"id": "tdd-workflow", "name": "tdd-workflow"}],
    ), patch("distr.core.workflow.planning._litellm_model", return_value="openai/gpt-test"), patch.dict(
        sys.modules, {"litellm": mock_litellm}
    ):
        result = suggest_step_harness_llm(
            instruction="Fix failing auth test",
            guardrail="Do not change unrelated modules",
            validation_prompt="Tests pass",
        )
    assert result.get("source") == "orchestrator_llm"
    assert result.get("skills") == ["tdd-workflow"]
    assert result.get("ui_tools") == ["cli"]
