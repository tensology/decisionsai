"""Direct platform-tool execution for tool-bound automations."""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_TOOL_BINDINGS: dict[str, tuple[str, str]] = {
    "proactive_orchestrator": (
        "distr.core.agent.tools.system.proactive_orchestrator",
        "ProactiveOrchestratorTool",
    ),
    # gap2/4 2026-10-29: read-only Support incoming monitor (MONITOR.md).
    "support_monitor": (
        "distr.core.automation.support_monitor",
        "SupportMonitorTool",
    ),
    # Automation-only: post one media item to the owner's WhatsApp Status.
    "whatsapp_status": (
        "distr.core.agent.tools.integrations.whatsapp_status_tool",
        "WhatsAppStatusTool",
    ),
}


def _normalize_tool_name(tool_name: str) -> str:
    return str(tool_name or "").strip().lower().replace("-", "_")


# Tools that declare they can run under enforced read-only mode.
READ_ONLY_CAPABLE_TOOLS = {"support_monitor"}


def run_automation_tool_for(automation: dict[str, Any], tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """gap2: run an automation's tool, enforcing ``action_config.read_only`` when set."""
    from distr.core.automation.read_only import ReadOnlyViolation, policy_from_action_config

    action_config = automation.get("action_config") if isinstance(automation.get("action_config"), dict) else {}
    policy = policy_from_action_config(action_config, label=str(automation.get("name") or "automation"))
    if policy is None:
        return run_automation_tool(tool_name, args)
    key = _normalize_tool_name(tool_name)
    if key not in READ_ONLY_CAPABLE_TOOLS:
        return {
            "success": False,
            "output": f"Read-only mode: tool {tool_name!r} is not read-only capable; refused.",
            "spoken_summary": "",
            "raw": {"read_only_refused": True},
        }
    try:
        return run_automation_tool(tool_name, args, read_only_policy=policy)
    except ReadOnlyViolation as exc:
        return {"success": False, "output": str(exc), "spoken_summary": "", "raw": {"read_only_blocked": policy.blocked}}


def run_automation_tool(tool_name: str, args: dict[str, Any] | None = None, *, read_only_policy: Any = None) -> dict[str, Any]:
    """Execute a registered platform tool and return a normalized result envelope."""
    key = _normalize_tool_name(tool_name)
    binding = _TOOL_BINDINGS.get(key)
    if not binding:
        return {
            "success": False,
            "output": f"Unknown automation tool: {tool_name}",
            "spoken_summary": "",
            "raw": {},
        }

    module_name, class_name = binding
    try:
        module = importlib.import_module(module_name)
        tool_cls = getattr(module, class_name)
        tool = tool_cls()
        payload = dict(args or {})
        if read_only_policy is not None:
            from distr.core.automation.read_only import read_only_scope

            prepare = getattr(tool, "prepare", None)
            if callable(prepare):
                prepare(read_only_policy=read_only_policy, **payload)  # credential warm-up, outside the scope
            with read_only_scope(read_only_policy):
                raw = tool._run(read_only_policy=read_only_policy, **payload)
        else:
            raw = tool._run(**payload)
    except Exception as exc:
        from distr.core.automation.read_only import ReadOnlyViolation

        if isinstance(exc, ReadOnlyViolation):
            raise
        logger.warning("Automation tool %s failed", key, exc_info=True)
        return {
            "success": False,
            "output": f"Tool execution failed: {exc}",
            "spoken_summary": "",
            "raw": {},
        }

    if isinstance(raw, dict):
        spoken = str(raw.get("spoken_summary") or "").strip()
        markdown = str(raw.get("markdown") or "").strip()
        output = spoken or markdown or json.dumps(raw, ensure_ascii=False, default=str)
        return {
            "success": bool(raw.get("success", True)),
            "output": output,
            "spoken_summary": spoken,
            "raw": raw,
        }

    text = str(raw or "").strip()
    return {
        "success": bool(text),
        "output": text,
        "spoken_summary": text[:650],
        "raw": {"text": text},
    }
