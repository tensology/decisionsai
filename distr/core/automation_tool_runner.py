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
}


def _normalize_tool_name(tool_name: str) -> str:
    return str(tool_name or "").strip().lower().replace("-", "_")


def run_automation_tool(tool_name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
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
        raw = tool._run(**payload)
    except Exception as exc:
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
