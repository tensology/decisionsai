"""Tests for per-step harness suggestions."""

from __future__ import annotations

import sys
import types

import pytest

from distr.core.workflow.loop_catalog import ELORM_LOOP_KICKOFFS
from distr.core.workflow.step_harness import (
    _ui_tools_from_harness,
    derive_action_type_from_ui_tools,
    merge_step_harness_config,
    suggest_step_harness,
    suggest_step_harness_llm,
)


def test_suggest_step_harness_check_command_run_command():
    suggestion = suggest_step_harness(
        instruction="Between iterations run: npm test",
        loop_contract={"check_command": "npm test"},
        step_role="check",
    )
    assert suggestion["action_type"] == "run_command"
    assert suggestion["config"].get("command") == "npm test"
    assert suggestion["validation_type"] == "exit_code"


def test_suggest_step_harness_vague_instruction_waits_for_continue():
    suggestion = suggest_step_harness(instruction="todo something")
    assert suggestion["wait_for_continue"] is True
    assert len(suggestion["clarify_questions"]) >= 2


def test_suggest_step_harness_hermes_backend_when_named():
    suggestion = suggest_step_harness(
        instruction="Use Hermes Agent to triage the failing test output",
        action_type="send_to_project_cli",
    )
    assert suggestion["backend_id"] == "hermes_agent"


def test_suggest_step_harness_cline_backend_when_named():
    suggestion = suggest_step_harness(
        instruction="Use Cline to fix the auth middleware regression",
        action_type="send_to_project_cli",
    )
    assert suggestion["backend_id"] == "cline"


def test_merge_step_harness_config_preserves_user_values():
    existing = {"backend_id": "cursor_ide", "model": "gpt-5"}
    suggestion = suggest_step_harness(instruction="Fix lint errors in the auth module")
    merged = merge_step_harness_config(existing, suggestion)
    assert merged["backend_id"] == "cursor_ide"
    assert merged["model"] == "gpt-5"
    assert merged.get("skills")


def test_harness_tool_aliases_normalize_to_specific_capabilities():
    assert _ui_tools_from_harness("agent_instruction", ["other"]) == ["agent"]
    assert _ui_tools_from_harness("execute_code", ["script"]) == ["python"]
    assert _ui_tools_from_harness("run_command", ["terminal"]) == ["shell"]
    assert derive_action_type_from_ui_tools(["python"]) == "execute_code"
    assert derive_action_type_from_ui_tools(["shell"]) == "run_command"
    assert derive_action_type_from_ui_tools(["agent"]) == "agent_instruction"


def test_suggest_step_harness_llm_returns_step_improvement_packet(monkeypatch):
    class FakeMessage:
        content = (
            '{"refined_instruction":"Run the checkout flow in a browser and verify Add all is blocked until a workflow is selected.",'
            '"guardrail":"- Stay inside the Player1Sport workflow UI\\\\n- Do not add tickets to a stale workflow",'
            '"validation_prompt":"Pass when Add all is disabled without an active workflow and enabled after selecting one.",'
            '"skills":["webapp-testing"],'
            '"tools":["playwright","browser_use"],'
            '"wait_for_continue":false,'
            '"clarify_questions":[],'
            '"rationale":"Browser automation matches the requested UI behavior."}'
        )

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    def fake_completion(**kwargs):
        return FakeResponse()

    monkeypatch.setattr(
        "distr.core.orchestrator.get_orchestrator_role_model",
        lambda role: ("openai", "gpt-test"),
    )
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})
    monkeypatch.setattr(
        "distr.core.skills.catalog.orchestrator_skill_catalog",
        lambda limit=120: [{"id": "webapp-testing", "name": "Webapp testing"}],
    )
    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(completion=fake_completion))

    suggestion = suggest_step_harness_llm(
        instruction="test add all workflow selection",
        guardrail="Existing guardrail",
        validation_prompt="Existing validation",
    )

    assert suggestion["source"] == "orchestrator_llm"
    assert suggestion["refined_instruction"].startswith("Run the checkout flow")
    assert "stale workflow" in suggestion["guardrail"]
    assert "Add all is disabled" in suggestion["validation_prompt"]
    assert suggestion["skills"] == ["webapp-testing"]
    assert suggestion["tools"] == ["playwright", "browser_use"]
    assert suggestion["action_type"] == "playwright"


@pytest.mark.parametrize("entry", ELORM_LOOP_KICKOFFS[:3], ids=lambda e: e["name"])
def test_suggest_step_harness_for_elorm_archetypes(entry):
    suggestion = suggest_step_harness(
        instruction=entry["kickoff"],
        archetype=entry["archetype"],
        loop_contract={"check_command": entry.get("expected_check_command") or "npm test"},
    )
    assert suggestion["action_type"] in {
        "send_to_project_cli",
        "run_command",
        "agent_instruction",
        "playwright",
        "computer_use",
    }
    assert suggestion["archetype"] == entry["archetype"]
