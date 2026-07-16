"""Workflow action timeouts reach the subprocess that actually does the work."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from distr.core.workflow.dispatcher import StepDispatcher
from distr.core.workflow.step_validator import build_step_config


def _step(action_type: str, *, timeout_seconds: int, config: dict | None = None) -> dict:
    return {
        "id": 91,
        "workflow_id": None,
        "name": f"Timed {action_type}",
        "action_type": action_type,
        "instruction": "Run the deterministic check.",
        "code": "print('ok')",
        "timeout_seconds": timeout_seconds,
        "config": dict(config or {}),
    }


def test_build_step_config_inherits_canonical_timeout_without_overriding_config():
    assert build_step_config(_step("playwright", timeout_seconds=17))["timeout_seconds"] == 17
    configured = _step("playwright", timeout_seconds=17, config={"timeout_seconds": 9})
    assert build_step_config(configured)["timeout_seconds"] == 9


def test_playwright_step_passes_configured_timeout_to_subprocess():
    service = MagicMock()
    service._execute_playwright.return_value = SimpleNamespace(exit_code=0, stdout="ok", stderr="")

    with patch("distr.core.workflow_engine.test_loop.TestLoopService", return_value=service):
        result = StepDispatcher()._execute(
            _step("playwright", timeout_seconds=20, config={"headless": True}),
            run_id=None,
        )

    service._execute_playwright.assert_called_once_with(
        "print('ok')",
        headless=True,
        timeout=20,
    )
    assert result["passed"] is True


def test_execute_code_step_passes_configured_timeout_to_subprocess():
    service = MagicMock()
    service._execute_python.return_value = SimpleNamespace(exit_code=0, stdout="ok", stderr="")

    with patch("distr.core.workflow_engine.test_loop.TestLoopService", return_value=service):
        result = StepDispatcher()._execute(
            _step("execute_code", timeout_seconds=13),
            run_id=None,
        )

    service._execute_python.assert_called_once_with(
        "print('ok')",
        timeout=13,
        cwd=None,
    )
    assert result["passed"] is True
