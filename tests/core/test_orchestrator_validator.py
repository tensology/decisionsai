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
