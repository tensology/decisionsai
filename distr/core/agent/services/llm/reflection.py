"""
Self-Reflection Mixin — detects tool-call failure loops and injects corrective
context into the LLM's system prompt so the agent can adjust its strategy.

Operates as a lightweight, in-process ring buffer that tracks the last N tool
calls per session.  Before the LLM re-issues a tool call that matches a recent
failed attempt, the mixin injects a reflection prompt suggesting a different
approach.  After too many identical consecutive calls, it raises a soft-fail.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ToolAttempt:
    tool_name: str
    args_hash: str       # sha256 prefix of sorted JSON args
    outcome: str          # "success" | "failure" | "partial" | "unknown"
    result_preview: str   # first 200 chars of the result
    timestamp: float


class SelfReflectionMixin:
    """Track tool execution outcomes and provide loop-break guards.

    Intended to be mixed into LLM service classes.  The mixin expects the host
    to call ``record_tool_attempt`` after each tool execution and
    ``get_reflection_context`` before issuing a new tool call.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._reflection_attempts: deque[_ToolAttempt] = deque()
        self._reflection_max_attempts: int = 5
        self._reflection_max_identical: int = 3
        self._reflection_lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def record_tool_attempt(
        self,
        tool_name: str,
        args: dict,
        outcome: str,            # "success" | "failure" | "partial" | "unknown"
        result_text: str = "",
    ) -> None:
        """Record a tool execution attempt in the ring buffer."""
        import time
        args_hash = hashlib.sha256(
            json.dumps(args, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        attempt = _ToolAttempt(
            tool_name=tool_name,
            args_hash=args_hash,
            outcome=outcome,
            result_preview=(result_text or "")[:200],
            timestamp=time.time(),
        )
        with self._reflection_lock:
            self._reflection_attempts.append(attempt)
            if len(self._reflection_attempts) > self._reflection_max_attempts:
                self._reflection_attempts.popleft()

    def check_before_tool_call(self, tool_name: str, args: dict) -> Optional[str]:
        """Return a reflection prompt if this call looks like a repeated failure.

        Returns None if the call should proceed normally, or a string to inject
        into the LLM context suggesting a different approach.

        Raises RuntimeError if the same tool+args has been called
        ``_reflection_max_identical`` times consecutively with failure outcomes.
        """
        import time
        args_hash = hashlib.sha256(
            json.dumps(args, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

        with self._reflection_lock:
            attempts = list(self._reflection_attempts)

        # Find recent (last 60s) attempts with same tool_name
        now = time.time()
        recent_same = [
            a for a in attempts
            if a.tool_name == tool_name and (now - a.timestamp) < 60.0
        ]

        # Count consecutive identical failures
        identical_failures = 0
        for a in reversed(recent_same):
            if a.args_hash == args_hash and a.outcome == "failure":
                identical_failures += 1
            else:
                break

        if identical_failures >= self._reflection_max_identical:
            raise RuntimeError(
                f"Loop-break: {tool_name} called with identical args "
                f"{identical_failures} times consecutively with failure outcomes. "
                f"Last result: {recent_same[-1].result_preview}"
            )

        if identical_failures >= 2:
            last_attempt = recent_same[-1]
            return (
                f"[SELF-REFLECTION] Your previous {identical_failures} attempt(s) "
                f"with {tool_name} returned: {last_attempt.result_preview}. "
                f"Try a different approach or different parameters."
            )

        # Also check if we've failed with ANY args recently
        failures = [a for a in recent_same if a.outcome == "failure"]
        if len(failures) >= 3:
            return (
                f"[SELF-REFLECTION] {tool_name} has failed {len(failures)} times "
                f"recently. Consider a different tool or strategy."
            )

        return None

    def get_session_reflection(self) -> str:
        """Build a summary of recent tool outcomes for the system prompt.

        Called at session start or reload to give the LLM awareness of recent
        tool execution patterns.
        """
        with self._reflection_lock:
            attempts = list(self._reflection_attempts)

        if not attempts:
            return ""

        success_count = sum(1 for a in attempts if a.outcome == "success")
        failure_count = sum(1 for a in attempts if a.outcome == "failure")
        partial_count = sum(1 for a in attempts if a.outcome == "partial")

        failed_tools: dict[str, int] = {}
        for a in attempts:
            if a.outcome == "failure":
                failed_tools[a.tool_name] = failed_tools.get(a.tool_name, 0) + 1

        lines = [
            f"Recent tool activity: {len(attempts)} calls "
            f"({success_count} succeeded, {failure_count} failed, {partial_count} partial)",
        ]
        if failed_tools:
            tools_list = ", ".join(
                f"{t} ({n} failures)" for t, n in failed_tools.items()
            )
            lines.append(f"Tools with failures: {tools_list}")

        return "\n".join(lines)

    def clear_reflection_history(self) -> None:
        """Clear the reflection ring buffer (e.g., on chat change)."""
        with self._reflection_lock:
            self._reflection_attempts.clear()
