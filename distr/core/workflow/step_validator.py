"""Step config validation helpers — extracted from StepDispatcher for testability.

These functions are pure (no side effects, no DB access) and can be imported
and tested independently of the full dispatcher.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WorkflowStartError(ValueError):
    """The saved workflow selection cannot be launched."""


def workflow_start_error(workflow, start_step_id=None) -> str | None:
    if str(workflow.status or '').strip().lower() == 'archived':
        return 'This workflow is archived. Restore it before starting work.'
    if start_step_id is None:
        try:
            position = int(getattr(workflow, 'start_step_position', None) or 0)
        except (TypeError, ValueError):
            position = -1
        if position < 0 or (position > 0 and position >= len(workflow.steps)):
            return 'The configured start step is no longer available. Refresh the workflow and select a current step.'
        return None
    try:
        step_id = int(start_step_id) if type(start_step_id) in (int, str) else None
    except ValueError:
        step_id = None
    if step_id is None or not any(step.id == step_id for step in workflow.steps):
        return 'The selected start step is no longer part of this workflow. Refresh the workflow and select a current step.'
    return None


def build_step_config(step_data: dict) -> dict:
    """Build a config dict suitable for StepValidator from raw step data.

    Normalises action-type-specific fields so the generic StepValidator
    receives a uniform shape regardless of which fields the step uses.
    """
    action_type = step_data["action_type"]
    config = dict(step_data.get("config") or {})
    # ``timeout_seconds`` is a first-class workflow-step field. Older steps
    # may also carry an action-specific override inside ``config``; preserve
    # that override while ensuring executors always receive the canonical
    # value when only the database column is populated.
    if step_data.get("timeout_seconds") is not None:
        config.setdefault("timeout_seconds", step_data["timeout_seconds"])
    if action_type in ("execute_code", "playwright", "browser_use"):
        config.setdefault("code", step_data.get("code", ""))
        config.setdefault("instruction", step_data.get("instruction", ""))
    elif action_type == "computer_use":
        config.setdefault("goal", step_data.get("instruction", ""))
        config.setdefault("instruction", step_data.get("instruction", ""))
    elif action_type == "send_to_project_cli":
        config.setdefault("instruction", step_data.get("instruction", ""))
    elif action_type == "play_recording":
        if not config.get("recording_name") and step_data.get("recording_filename"):
            config["recording_name"] = step_data["recording_filename"]
        if not config.get("recording_id") and step_data.get("action_id"):
            config["recording_id"] = step_data["action_id"]
    elif action_type == "decision_action":
        if not config.get("action_id") and step_data.get("action_id"):
            config["action_id"] = step_data["action_id"]
    return config


def validate_before_dispatch(step_data: dict) -> Optional[str]:
    """Validate a step's config before execution.

    Returns an error string describing the problem, or ``None`` when the step
    is ready to run.
    """
    action_type = step_data["action_type"]
    if action_type == "agent_instruction":
        return "No instruction provided" if not step_data["instruction"].strip() else None
    try:
        from distr.core.workflow_engine.validation import StepValidator
        config = build_step_config(step_data)
        errors = StepValidator().validate(action_type, config)
        if errors:
            return "; ".join(f"{e.field}: {e.message}" for e in errors)
    except Exception as e:
        logger.warning("Validation import failed: %s", e)
    return None
