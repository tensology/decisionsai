"""Runtime guardrails for computer-use tool execution."""

from __future__ import annotations

import json
from typing import Any


_ACTIONING_COMPUTER_USE_TOOLS = {
    "mouse_movement",
    "mouse_actions",
    "move_to_element",
    "click_element_by_id",
    "drag_to",
    "scroll",
}


def _parse_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _is_actioning_computer_use_call(tool_name: str, args: dict[str, Any]) -> bool:
    if tool_name in _ACTIONING_COMPUTER_USE_TOOLS:
        return True
    if tool_name == "screenshot_analyzer":
        return bool(args.get("execute_action", False))
    return False


def build_computer_use_execution_decisions(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Allow at most one actioning computer-use call per batch.

    Returns one decision per tool call:
    - ``allow``: bool
    - ``reason``: short machine-readable reason
    """
    decisions: list[dict[str, Any]] = []
    action_call_seen = False

    for tool_call in tool_calls:
        function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
        name = function.get("name", "")
        args = _parse_args(function.get("arguments", {}))
        is_action_call = _is_actioning_computer_use_call(name, args)

        if is_action_call and action_call_seen:
            decisions.append(
                {
                    "allow": False,
                    "reason": "blocked_by_computer_use_single_action_rule",
                }
            )
            continue

        if is_action_call:
            action_call_seen = True
        decisions.append({"allow": True, "reason": "ok"})

    return decisions
