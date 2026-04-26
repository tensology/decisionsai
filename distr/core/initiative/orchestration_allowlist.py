"""
Allowlisted tool names for Initiative JSON `suggested_tool` proposals.

Initiative does not execute arbitrary tools — it may only *suggest* that the user
ask the main voice agent to run one of these (same names as LangChain tool `.name`).
"""

from typing import Dict, Optional

# Keep in sync with distr.core.agent.tools loader registry `name` fields.
INITIATIVE_SUGGESTIBLE_TOOL_NAMES = frozenset(
    {
        "create_ticket",
        "pi_agent",
        "terminal_overview",
        "list_workflows",
        "get_workflow",
        "run_workflow",
        "continue_workflow",
        "cancel_workflow_run",
        "find_skill",
        "push_skill",
    }
)


def normalize_suggested_tool(raw) -> Optional[Dict[str, object]]:
    """Return ``{"name": str, "args": dict}`` if *raw* is valid, else ``None``."""
    if not isinstance(raw, dict):
        return None
    name = (raw.get("name") or "").strip()
    if name not in INITIATIVE_SUGGESTIBLE_TOOL_NAMES:
        return None
    args = raw.get("args")
    if args is not None and not isinstance(args, dict):
        args = {}
    return {"name": name, "args": args if isinstance(args, dict) else {}}
