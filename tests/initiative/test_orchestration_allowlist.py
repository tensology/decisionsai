"""Tests for Initiative suggested_tool allowlist and parse_llm_response."""

import json

import pytest

from distr.core.initiative.orchestration_allowlist import (
    INITIATIVE_SUGGESTIBLE_TOOL_NAMES,
    normalize_suggested_tool,
)
from distr.core.initiative.proposed_action import parse_llm_response


def test_normalize_suggested_tool_accepts_allowlisted():
    out = normalize_suggested_tool({"name": "pi_agent", "args": {"instruction": "run tests"}})
    assert out == {"name": "pi_agent", "args": {"instruction": "run tests"}}


def test_normalize_suggested_tool_rejects_unknown_name():
    assert normalize_suggested_tool({"name": "malicious_tool", "args": {}}) is None


def test_normalize_suggested_tool_coerces_non_dict_args():
    out = normalize_suggested_tool({"name": "find_skill", "args": "bad"})
    assert out == {"name": "find_skill", "args": {}}


def test_parse_llm_response_strips_invalid_suggested_tool():
    raw = json.dumps(
        {
            "action_type": "suggestion",
            "description": "Try syncing the board",
            "suggested_tool": {"name": "not_a_real_tool", "args": {}},
        }
    )
    action = parse_llm_response(raw)
    assert action.action_type == "suggestion"
    assert action.suggested_tool is None


def test_parse_llm_response_keeps_valid_suggested_tool():
    raw = json.dumps(
        {
            "action_type": "suggestion",
            "description": "Run the deploy workflow",
            "suggested_tool": {"name": "run_workflow", "args": {"workflow_id": 3}},
        }
    )
    action = parse_llm_response(raw)
    assert action.suggested_tool == {"name": "run_workflow", "args": {"workflow_id": 3}}


def test_allowlist_contains_core_orchestration_tools():
    assert "create_ticket" in INITIATIVE_SUGGESTIBLE_TOOL_NAMES
    assert "pi_agent" in INITIATIVE_SUGGESTIBLE_TOOL_NAMES
    assert "run_workflow" in INITIATIVE_SUGGESTIBLE_TOOL_NAMES
