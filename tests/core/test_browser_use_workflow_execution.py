"""Executable Browser Use workflow action contracts."""

from unittest.mock import patch

from distr.core.workflow.dispatcher import StepDispatcher
from distr.core.workflow.step_validator import build_step_config, validate_before_dispatch


def _browser_use_step() -> dict:
    return {
        "id": 41,
        "workflow_id": 7,
        "name": "Inspect with Browser Use",
        "action_type": "browser_use",
        "instruction": "Open the local fixture and verify the status.",
        "code": "print('browser ok')",
        "config": {"headless": True, "tools": ["browser_use"]},
    }


def test_browser_use_step_validates_like_browser_automation():
    step = _browser_use_step()

    assert build_step_config(step)["code"] == "print('browser ok')"
    assert validate_before_dispatch(step) is None


def test_browser_use_step_executes_through_deterministic_browser_adapter():
    dispatcher = StepDispatcher()
    step = _browser_use_step()

    with patch.object(
        dispatcher,
        "_run_code_type",
        return_value={"output": "BROWSER_USE_GREEN", "passed": True},
    ) as run_code:
        result = dispatcher._execute(step, run_id=None)

    run_code.assert_called_once()
    assert run_code.call_args.args[2] == "playwright"
    assert result["passed"] is True
    assert result["browser_surface"] == "browser_use"
    assert result["browser_adapter"] == "playwright"
    assert result["output"].startswith("Browser Use executed via the local Playwright adapter.")


def test_workflow_editor_exposes_browser_use_as_an_action_type():
    js = (
        __import__("pathlib").Path(__file__).parents[2]
        / "distr/gui/web/static/workflows/js/workflows.js"
    ).read_text(encoding="utf-8")

    assert '<option value="browser_use"' in js
    assert 'if (tools.indexOf("browser_use") >= 0) return "browser_use";' in js


def test_ui_feedback_uses_in_app_dialog_instead_of_native_prompt():
    js = (
        __import__("pathlib").Path(__file__).parents[2]
        / "distr/gui/web/static/workflows/js/workflows.js"
    ).read_text(encoding="utf-8")

    assert 'id = "wf-ui-feedback-modal"' in js
    assert 'function submitUiTasteFeedbackFromModal()' in js
    assert "window.prompt" not in js
