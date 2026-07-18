"""Tests for Hermes validator LLM second pass."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from distr.core.orchestrator_validator import (
    _parse_pass_fail_response,
    apply_orchestrator_validator_overlay,
    run_orchestrator_validator_judgment,
)


def test_parse_pass_fail_response_edge_cases():
    assert _parse_pass_fail_response("PASS")[0] is True
    assert _parse_pass_fail_response("")[0] is False


@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_validator.run_orchestrator_validator_judgment")
@patch("distr.core.orchestrator_validator.is_orchestrator_validator_second_pass_enabled", return_value=True)
def test_apply_orchestrator_validator_overlay_fails_closed(mock_enabled, mock_judge, mock_emit):
    mock_judge.return_value = {"passed": False, "rationale": "FAIL: incomplete", "mode": "second_pass"}
    step = MagicMock()
    step.validation_type = "text_match"
    step.validation_prompt = "must include evidence"
    step.id = 7

    overlay = apply_orchestrator_validator_overlay(
        step=step,
        result="done",
        caller_passed=True,
        mechanical_passed=True,
        ticket_context="Fix login bug",
    )
    assert overlay is not None
    assert overlay["passed"] is False
    mock_emit.assert_called_once()


@patch("distr.core.orchestrator_validator.is_orchestrator_validator_second_pass_enabled", return_value=True)
def test_apply_orchestrator_validator_overlay_skips_when_mechanical_failed(mock_enabled):
    step = MagicMock()
    step.validation_type = "none"
    overlay = apply_orchestrator_validator_overlay(
        step=step,
        result="bad",
        caller_passed=True,
        mechanical_passed=False,
    )
    assert overlay is None


@patch("distr.core.orchestrator.emit_event")
@patch("distr.core.orchestrator_validator.run_orchestrator_validator_judgment")
@patch("distr.core.orchestrator_validator.is_orchestrator_validator_second_pass_enabled", return_value=True)
def test_planned_independent_validators_run_in_parallel_and_require_consensus(
    mock_enabled, mock_judge, mock_emit
):
    mock_judge.side_effect = [
        {"passed": True, "rationale": "PASS: complete", "mode": "independent_second_pass"},
        {"passed": False, "rationale": "FAIL: missing browser evidence", "mode": "independent_second_pass"},
    ]
    step = MagicMock(id=9, validation_type="rule_based", validation_prompt="Evidence is required")
    routes = [
        {"backend": "pi", "model_provider": "ollama", "model": "ornith:35b"},
        {"backend": "pi", "model_provider": "openrouter", "model": "tencent/hy3-preview"},
    ]

    overlay = apply_orchestrator_validator_overlay(
        step=step,
        result="Status: completed",
        caller_passed=True,
        mechanical_passed=True,
        validation_routes=routes,
    )

    assert overlay is not None
    assert overlay["passed"] is False
    assert overlay["mode"] == "dual_independent"
    assert len(overlay["reviews"]) == 2
    assert mock_judge.call_count == 2
    used_routes = [call.kwargs["route"] for call in mock_judge.call_args_list]
    assert used_routes == routes
    mock_emit.assert_called_once()
