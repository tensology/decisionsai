"""Regression coverage for the first-class computer_use workflow step."""

import sys
import types
from unittest.mock import patch

from distr.core.workflow.step_validator import build_step_config
from distr.core.workflow_engine.validation import StepValidator


def test_build_step_config_uses_instruction_as_computer_use_goal():
    cfg = build_step_config({
        "action_type": "computer_use",
        "instruction": "Click the visible Submit button",
        "config": {},
    })

    assert cfg["goal"] == "Click the visible Submit button"
    assert cfg["instruction"] == "Click the visible Submit button"


def test_computer_use_validates_as_known_step_type():
    errors = StepValidator().validate("computer_use", {
        "goal": "Fill the local desktop form",
        "max_iterations": 12,
        "stuck_threshold": 3,
        "screenshot_resize_width": 1280,
    })

    assert errors == []


def test_planner_preserves_computer_use_action_type_from_llm():
    from distr.core.workflow import planning

    class _Message:
        content = '[{"title":"Use screen","instruction":"Click Submit","action_type":"computer_use"}]'

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    fake_litellm = types.SimpleNamespace(completion=lambda **kwargs: _Response())

    with patch.dict(sys.modules, {"litellm": fake_litellm}), \
         patch("distr.core.settings.load_settings_from_db", return_value={"workflow_llm_provider": "ollama", "workflow_llm_model": "qwen3:8b"}), \
         patch("distr.core.llm_override.get_llm_override", return_value=None):
        steps = planning._call_llm_for_plan("Use the screen to click Submit")

    assert steps[0]["title"] == "Use screen"
    assert steps[0]["instruction"] == "Click Submit"
    assert steps[0]["action_type"] == "computer_use"
    assert steps[0]["config"]["goal"] == "Click Submit"
    assert steps[0]["config"]["stuck_threshold"] == 3
    assert steps[0]["validation_type"] == "llm_judgment"
