"""Shared mutation safety, replay protection, and audit metadata for tools."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_NO_ACTION_PATTERNS = (
    r"\b(?:do not|don't|dont)\s+(?:try\s+to\s+)?(?:do|change|create|make|send|write|delete|move|touch|run|execute|modify|update)\b",
    r"\b(?:do not|don't|dont)\b[^.?!]{0,80}\banything\b",
    r"\b(?:don't|do not|dont)\s+touch\b",
    r"\bno\s+more\s+(?:attempts?|changes?|actions?)\b",
    r"^\s*(?:okay[,\s]*)?stop[.!]?\s*$",
    r"\bcancel\s+that\b[^.?!]{0,80}\b(?:don't|do not|dont)\b",
)

_READ_ACTION_PREFIXES = (
    "get", "list", "read", "search", "find", "query", "check", "inspect",
    "analyze", "analyse", "show", "status", "preview", "describe", "fetch",
    "download_status", "connection_health",
)

_READ_TOOL_PREFIXES = (
    "get_", "list_", "read_", "find_", "search_", "query_", "check_",
    "inspect_", "analyze_", "analyse_", "show_",
)

_READ_ONLY_TOOLS = {
    "accessibility_tree",
    "benchmark_models",
    "codex_thread_context",
    "computer_use_context",
    "developer_context",
    "document_extractor",
    "ecosystem_scan",
    "memory_read",
    "memory_search",
    "orchestrator_qualification",
    "screenshot_analyzer",
    "system_info",
    "terminal_overview",
    "web_fetch",
    "web_search",
}

_MUTATION_ACTION_PREFIXES = (
    "add", "append", "apply", "archive", "cancel", "clear", "click", "convert",
    "create", "delete", "disable", "draft", "edit", "enable", "execute", "install",
    "launch", "move", "open", "pause", "play", "post", "publish", "pull", "push",
    "record", "remove", "rename", "reply", "reset", "restart", "resume", "run", "save",
    "send", "set", "spawn", "start", "stop", "switch", "type", "unarchive", "update",
    "upload", "write",
)

_MULTIPLE_MUTATION_RE = re.compile(
    r"\b(?:multiple|several|both|all|each|every|batch|bulk|two|three|four|five|six|seven|eight|nine|ten|[2-9]|\d{2,})\b",
    re.IGNORECASE,
)

_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|passwd|secret|cookie|credential)",
    re.IGNORECASE,
)

_PRIVATE_TEXT_KEYS = {
    "body", "code", "content", "last_user_message", "prompt", "text", "transcription",
}


def no_action_requested(user_text: str) -> bool:
    """Return True only for explicit instructions not to perform side effects."""
    text = str(user_text or "").strip()
    return bool(text) and any(re.search(pattern, text, re.IGNORECASE) for pattern in _NO_ACTION_PATTERNS)


def _action_name(arguments: dict[str, Any]) -> str:
    for key in ("action", "operation", "command"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower().replace("-", "_").replace(" ", "_")
    params = arguments.get("params")
    if isinstance(params, dict):
        return _action_name(params)
    return ""


def _starts_with_any(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value == prefix or value.startswith(prefix + "_") for prefix in prefixes)


def is_mutating_tool_call(tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
    """Conservatively classify a tool call as read-only or side-effecting."""
    name = str(tool_name or "").strip().lower()
    args = arguments if isinstance(arguments, dict) else {}
    action = _action_name(args)
    if action:
        if _starts_with_any(action, _READ_ACTION_PREFIXES):
            return False
        if _starts_with_any(action, _MUTATION_ACTION_PREFIXES):
            return True
    if name in _READ_ONLY_TOOLS or name.startswith(_READ_TOOL_PREFIXES):
        return False
    if _starts_with_any(name, _MUTATION_ACTION_PREFIXES):
        return True
    # Unknown tools are treated as side-effecting at the explicit no-action boundary.
    return True


def _turn_key(owner: Any, user_text: str) -> tuple[Any, int, str]:
    chat_id = None
    manager = getattr(owner, "chat_manager", None)
    if manager:
        try:
            chat_id = manager.get_current_chat()
        except Exception:
            chat_id = None
    messages = getattr(owner, "_messages", None) or []
    user_count = sum(1 for message in messages if message.get("role") == "user")
    return chat_id, user_count, str(user_text or "")


def _state(owner: Any, user_text: str) -> dict[str, Any]:
    key = _turn_key(owner, user_text)
    state = getattr(owner, "_tool_execution_policy_state", None)
    if not isinstance(state, dict) or state.get("turn_key") != key:
        state = {"turn_key": key, "fingerprints": set(), "scopes": set()}
        setattr(owner, "_tool_execution_policy_state", state)
    return state


def _canonical_arguments(arguments: dict[str, Any]) -> str:
    clean = {
        key: value
        for key, value in (arguments or {}).items()
        if key not in {"is_telegram_request", "last_user_message"}
    }
    try:
        return json.dumps(clean, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return repr(clean)


def _mutation_keys(tool_name: str, arguments: dict[str, Any]) -> tuple[str, str]:
    name = str(tool_name or "").strip().lower()
    action = _action_name(arguments) or name
    scope = f"{name}:{action}"
    payload = f"{scope}:{_canonical_arguments(arguments)}".encode("utf-8", errors="replace")
    return scope, hashlib.sha256(payload).hexdigest()


def tool_execution_block_reason(
    owner: Any,
    tool_name: str,
    arguments: dict[str, Any] | None,
    user_text: str,
) -> str:
    """Block forbidden mutations and successful mutation replays within one user turn."""
    args = arguments if isinstance(arguments, dict) else {}
    if not is_mutating_tool_call(tool_name, args):
        return ""
    if no_action_requested(user_text):
        return (
            "Blocked mutation because the latest user instruction explicitly said not to act. "
            "Respond with diagnosis or read-only inspection only."
        )
    state = _state(owner, user_text)
    scope, fingerprint = _mutation_keys(tool_name, args)
    if fingerprint in state["fingerprints"]:
        return "Blocked duplicate mutation: this exact tool call already succeeded in the current user turn."
    if scope in state["scopes"] and not _MULTIPLE_MUTATION_RE.search(str(user_text or "")):
        return (
            "Blocked repeated mutation: this tool action already succeeded in the current user turn. "
            "Use its result instead of creating another side effect."
        )
    return ""


def remember_successful_tool_call(
    owner: Any,
    tool_name: str,
    arguments: dict[str, Any] | None,
    user_text: str,
    result: Any,
) -> None:
    """Remember successful mutations so later tool rounds cannot replay them."""
    args = arguments if isinstance(arguments, dict) else {}
    if not is_mutating_tool_call(tool_name, args):
        return
    text = str(result or "").strip().lower()
    if not text or text.startswith(("error", "failed", "blocked", "skipped", "stopped")):
        return
    state = _state(owner, user_text)
    scope, fingerprint = _mutation_keys(tool_name, args)
    state["scopes"].add(scope)
    state["fingerprints"].add(fingerprint)


def sanitized_tool_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Return bounded, secret-safe arguments for the durable action log."""
    def clean(value: Any, key: str = "") -> Any:
        if _SECRET_KEY_RE.search(key):
            return "<redacted>"
        if key in _PRIVATE_TEXT_KEYS:
            return f"<redacted {len(str(value or ''))} chars>"
        if isinstance(value, dict):
            return {str(k): clean(v, str(k).lower()) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(item, key) for item in value[:100]]
        if isinstance(value, str):
            return value if len(value) <= 800 else value[:800] + "..."
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:800]

    return clean(arguments if isinstance(arguments, dict) else {})
