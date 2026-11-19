"""Confirmation gate for high-impact side effects started from chat.

Workflows, recorded actions, delegated device work and outbound sends must be
asked for by the user. When the model reaches for one of these tools on a
request that never asked for it (e.g. "read from the clipboard"), the call is
blocked and the model must ask "Shall I run …?" first. A short affirmative
reply ("yes", "go ahead", "do it") then releases that exact tool once.
"""

from __future__ import annotations

import re
import time
from typing import Any

# Tool -> verbs that count as the user explicitly asking for it.
_WORKFLOW_VERBS = (
    r"\b(?:run|re-?run|start|launch|kick\s*off|execute|trigger|continue|resume|restart|retry|"
    r"try\s+again|redo|spawn|reset|clear|cancel|stop|abort|proceed|carry\s+on|finish|fire)\b"
)
HIGH_IMPACT_TOOLS: dict[str, str] = {
    "run_workflow": _WORKFLOW_VERBS,
    "continue_workflow": _WORKFLOW_VERBS,
    "spawn_ticket_workflow": _WORKFLOW_VERBS + r"|\b(?:work\s+on|pick\s+up|implement|build|fix)\b",
    "reset_workflow": r"\breset\b|\bstart\s+over\b|\bfrom\s+scratch\b",
    "clear_workflow_history": r"\bclear\b|\bwipe\b|\bdelete\b",
    "cancel_workflow_run": r"\b(?:cancel|stop|abort|kill|halt)\b",
    "play_action": r"\b(?:play|run|do|execute|perform|replay|trigger|start)\b",
    "delegated_workflow": (
        r"\b(?:delegate|hand\s*off|handoff|send\s+to|ask\s+(?:codex|cursor)|have\s+(?:codex|cursor)|"
        r"run|start|do|implement|prepare|prep|report\s+back)\b"
    ),
    "start_recording": r"\b(?:record|recording|start)\b",
}
# Actions on otherwise mixed tools that send something outside Decisions.
_SEND_ACTION_RE = re.compile(r"^(?:send|reply|post|publish|forward)", re.IGNORECASE)
_SEND_TOOLS = {"google_workspace", "tensology_workspace", "whatsapp", "telegram", "slack"}
_SEND_VERBS = r"\b(?:send|reply|respond|post|publish|forward|email|mail|message|text|tell|share)\b"

_AFFIRMATIVE_TOKEN = (
    r"(?:yes|yep|yeah|ya|sure|ok(?:ay)?|confirm(?:ed)?|go\s+ahead|go\s+for\s+it|go|do\s+it|please\s+do|"
    r"please|run\s+it|send\s+it|approved?|y|thanks?|thank\s+you|that'?s\s+fine|fine|absolutely)"
)
_AFFIRMATIVE_RE = re.compile(
    r"^\s*" + _AFFIRMATIVE_TOKEN + r"(?:[\s,.!]+" + _AFFIRMATIVE_TOKEN + r")*[\s.!]*$",
    re.IGNORECASE,
)
PENDING_TTL_SEC = 15 * 60
_PENDING_ATTR = "_pending_side_effect_confirmation"


def _action(arguments: dict[str, Any]) -> str:
    for key in ("action", "operation", "op", "method"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def gated_verbs(tool_name: str, arguments: dict[str, Any] | None) -> str:
    """Regex of verbs that authorise this call, or "" when the call is not gated."""
    name = str(tool_name or "").strip().lower()
    if name in HIGH_IMPACT_TOOLS:
        return HIGH_IMPACT_TOOLS[name]
    args = arguments if isinstance(arguments, dict) else {}
    if name in _SEND_TOOLS and _SEND_ACTION_RE.match(_action(args)):
        return _SEND_VERBS
    return ""


def is_affirmative(text: str) -> bool:
    return bool(_AFFIRMATIVE_RE.match(str(text or "")))


def confirmation_block_reason(owner: Any, tool_name: str, arguments: dict[str, Any] | None, user_text: str) -> str:
    """Return a block message when a high-impact call was not asked for, else ""."""
    verbs = gated_verbs(tool_name, arguments)
    if not verbs:
        return ""
    text = str(user_text or "")
    if getattr(owner, "_is_automation_turn", False):
        return ""
    if re.search(verbs, text, re.IGNORECASE):
        return ""
    pending = getattr(owner, _PENDING_ATTR, None) if owner is not None else None
    now = time.time()
    if (
        isinstance(pending, dict)
        and pending.get("tool") == str(tool_name)
        and now - float(pending.get("at") or 0) <= PENDING_TTL_SEC
        and is_affirmative(text)
    ):
        try:
            setattr(owner, _PENDING_ATTR, None)
        except Exception:
            pass
        return ""
    if owner is not None:
        try:
            setattr(owner, _PENDING_ATTR, {"tool": str(tool_name), "at": now})
        except Exception:
            pass
    label = str(tool_name).replace("_", " ")
    return (
        f"Needs confirmation: the user did not ask for {label} in this message, so it was NOT run. "
        f"If it is genuinely needed, ask the user one short question such as \"Shall I {label} now?\" "
        "and wait for a yes. Otherwise just answer what they asked."
    )
