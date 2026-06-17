"""Tests for per-step harness suggestions."""

from __future__ import annotations

import pytest

from distr.core.workflow.loop_catalog import ELORM_LOOP_KICKOFFS
from distr.core.workflow.step_harness import merge_step_harness_config, suggest_step_harness


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
