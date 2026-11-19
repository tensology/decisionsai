"""Guards that stop an agent loop from spinning without telling anyone.

* ``ReadLoopGuard`` counts consecutive read-only tool calls (read, grep, ls,
  find, list, get…) since the last write/command/answer. On 18 Nov a
  Development turn made 80+ Read/Grep/Ls calls with no edit until the owner stopped
  it by hand. The guard first nudges the model to act or answer, then stops the
  loop with a plain explanation.
* ``round_cap_notice`` is the message the user gets when the chat tool loop hits
  its round cap, instead of a silent log warning.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

_READ_NAMES = {
    "read", "read_file", "grep", "ls", "find", "glob", "search", "cat", "view", "list_dir",
    "list_files", "file_search", "codebase_search", "search_files", "rg", "open_file",
    "memory_read", "memory_search", "web_fetch", "web_search", "accessibility_tree",
    "screenshot_analyzer", "terminal_overview", "developer_context", "system_info",
}
_READ_PREFIXES = ("read", "get", "list", "find", "search", "grep", "show", "inspect", "check", "fetch", "view", "ls")
_READ_ACTIONS = ("get", "list", "read", "search", "find", "show", "status", "overview", "check", "inspect", "preview")


def is_read_call(tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
    name = str(tool_name or "").strip().lower()
    args = arguments if isinstance(arguments, dict) else {}
    action = str(args.get("action") or "").strip().lower()
    if action:
        return action.startswith(_READ_ACTIONS) or action.endswith("overview")
    if name in _READ_NAMES:
        return True
    return name.startswith(_READ_PREFIXES)


def _fingerprint(tool_name: str, arguments: dict[str, Any] | None) -> str:
    args = {k: v for k, v in (arguments or {}).items() if k not in {"last_user_message", "is_telegram_request"}}
    raw = json.dumps([str(tool_name).lower(), args], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


def _env_int(name: str, default: int) -> int:
    try:
        return max(2, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


@dataclass
class ReadLoopGuard:
    nudge_at: int
    stop_at: int
    repeat_stop_at: int = 4
    consecutive_reads: int = 0
    total_reads: int = 0
    nudged: bool = False
    tripped: bool = False
    repeats: dict[str, int] = field(default_factory=dict)
    reason: str = ""

    @classmethod
    def for_chat(cls) -> "ReadLoopGuard":
        return cls(nudge_at=_env_int("DECISIONS_CHAT_READ_NUDGE", 8), stop_at=_env_int("DECISIONS_CHAT_READ_STOP", 12), repeat_stop_at=3)

    @classmethod
    def for_development(cls) -> "ReadLoopGuard":
        return cls(nudge_at=_env_int("DECISIONS_DEV_READ_NUDGE", 30), stop_at=_env_int("DECISIONS_DEV_READ_STOP", 60), repeat_stop_at=4)

    def observe(self, tool_name: str, arguments: dict[str, Any] | None = None, *, read: bool | None = None) -> str:
        """Record one call. Returns "", "nudge" or "stop"."""
        if read is None:
            read = is_read_call(tool_name, arguments)
        if not read:
            self.consecutive_reads = 0
            self.repeats.clear()
            self.nudged = False
            return ""
        self.consecutive_reads += 1
        self.total_reads += 1
        key = _fingerprint(tool_name, arguments)
        self.repeats[key] = self.repeats.get(key, 0) + 1
        if self.repeats[key] >= self.repeat_stop_at:
            self.tripped = True
            self.reason = f"the same {tool_name} call was repeated {self.repeats[key]} times"
            return "stop"
        if self.consecutive_reads >= self.stop_at:
            self.tripped = True
            self.reason = f"{self.consecutive_reads} read-only calls in a row without changing anything or answering"
            return "stop"
        if self.consecutive_reads >= self.nudge_at and not self.nudged:
            self.nudged = True
            return "nudge"
        return ""

    def nudge_message(self) -> str:
        return (
            f"[LOOP GUARD] You have made {self.consecutive_reads} read-only calls in a row. Stop exploring: "
            "either make the change, or answer the user with what you found and what is still unknown. "
            "Do not repeat reads you already made."
        )

    def stop_message(self, surface: str = "chat") -> str:
        where = "this Development turn" if surface == "development" else "this request"
        return (
            f"I stopped {where} because it was going in circles: {self.reason}. "
            "Nothing was changed after that point. Tell me which file or step to focus on, "
            "or say \"continue\" to let it keep looking."
        )


def round_cap_notice(rounds: int, pending_tools: list[str] | None = None) -> str:
    pending = ", ".join(sorted({t for t in (pending_tools or []) if t}))[:200]
    tail = f" It still wanted to run: {pending}." if pending else ""
    return (
        f"I hit my limit of {rounds} tool rounds for one message before finishing.{tail} "
        "What I did so far is above. Say \"continue\" and I'll pick up from there."
    )


def observe_tool_calls(guard: ReadLoopGuard, tool_calls: list[Any] | None) -> str:
    """Feed one round of OpenAI/Ollama-shaped tool calls; return the strongest verdict."""
    verdict = ""
    for call in tool_calls or []:
        function = (call.get("function") if isinstance(call, dict) else None) or {}
        raw = function.get("arguments")
        if isinstance(raw, dict):
            args = raw
        else:
            try:
                args = json.loads(raw or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
        outcome = guard.observe(str(function.get("name") or ""), args if isinstance(args, dict) else {})
        if outcome == "stop" or (outcome == "nudge" and verdict != "stop"):
            verdict = outcome
    return verdict


def stop_instruction(guard: ReadLoopGuard) -> str:
    return (
        "[LOOP GUARD] Tools are now off for this message because " + guard.reason + ". "
        "Answer the user now with what you found, say plainly that you stopped looking "
        "because you were going in circles, and ask what to focus on."
    )


def log_skipped_tool(chat_id: Any, tool_name: str, arguments: Any, reason: str, guard: str, event_queue: Any = None) -> None:
    """Record a tool call that a guard skipped, so the event log shows every call."""
    try:
        from distr.core.agent.services.llm.tool_execution_policy import sanitized_tool_arguments
        from distr.core.agent.tool_audit import record_tool_execution

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
        record_tool_execution(
            chat_id,
            str(tool_name or "unknown_tool"),
            str(reason),
            "failed",
            event_queue=event_queue,
            metadata={
                "arguments": sanitized_tool_arguments(arguments if isinstance(arguments, dict) else {}),
                "skipped_by": guard,
            },
        )
    except Exception:
        import logging

        logging.getLogger(__name__).debug("Could not log skipped tool %s", tool_name, exc_info=True)


_READ_COMMAND_RE = __import__("re").compile(
    r"^\s*(?:cd\s+\S+\s*&&\s*)?(?:cat|rg|grep|egrep|ls|find|fd|head|tail|wc|tree|stat|file|less|"
    r"sed\s+-n|awk|git\s+(?:status|log|diff|show|grep|ls-files|blame)|nl|du)\b"
)
_HARNESS_WRITE_TOOLS = {"apply_patch", "edit", "write", "write_file", "replace_text", "multiedit", "str_replace", "create_file"}


def is_harness_read(tool_name: str, details: dict[str, Any] | None = None) -> bool | None:
    """Classify a Development harness tool event. None = not a tool we count."""
    name = str(tool_name or "").strip().lower()
    details = details or {}
    if not name or name in {"development_agent", "cursor_agent", "review_agent", "model_progress", "context_compaction"}:
        return None
    if name in _HARNESS_WRITE_TOOLS:
        return False
    if name in {"run_command", "bash", "shell", "command"}:
        command = str(details.get("command") or "")
        return bool(command) and bool(_READ_COMMAND_RE.match(command))
    return is_read_call(name, {})
