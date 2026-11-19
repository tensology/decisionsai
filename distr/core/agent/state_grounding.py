"""Ground "what's going on" answers in live Development state.

Before the chat model answers a state question ("what am I working on",
"what's going on", "status", "what's running"), Decisions itself calls the
read-only Development overview, ticket and terminal tools, logs each call to
the chat event log, and hands the model the results with a capture time. The
model is told to answer from that data and say how fresh it is, instead of
guessing from a stale global "active project" flag.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

_STATE_PATTERNS = (
    r"\bwhat\s+(?:am\s+i|are\s+we|is\s+paul)\s+(?:currently\s+)?(?:working|busy)\s+on\b",
    r"\bwhat\b[^?.!]{0,40}\b(?:i'?m|i\s+am|we'?re|we\s+are|am\s+i)\s+(?:currently\s+|now\s+)?(?:working|busy)\s+on\b",
    r"\bwhat(?:'s|\s+is)\s+(?:going\s+on|happening|running|in\s+flight|active|outstanding|pending)\b",
    r"\bwhat\s+(?:project|thread|ticket|workflow|run)s?\s+(?:am\s+i|are\s+we|is|are)\b",
    r"\bwhich\s+(?:project|thread|ticket|workflow|run)s?\s+(?:am\s+i|are\s+we|is|are)\b",
    r"\b(?:give\s+me\s+(?:a|the)\s+)?(?:status|progress)\s+(?:update|report|check)\b",
    r"^\s*(?:status|progress)\s*\??\s*$",
    r"\bwhat(?:'s|\s+is)\s+the\s+(?:status|progress|state)\b",
    r"\bwhere\s+(?:are\s+we|am\s+i|is\s+it)\s+(?:at|up\s+to)\b",
    r"\b(?:any|which)\s+(?:runs?|workflows?|terminals?|threads?)\s+(?:running|active|stuck|blocked|failing)\b",
    r"\bhow\s+(?:is|are)\s+(?:the\s+)?(?:build|runs?|workflows?|threads?|tickets?)\s+(?:going|doing)\b",
)
_STATE_RE = re.compile("|".join(_STATE_PATTERNS), re.IGNORECASE)

# Each probe is read-only by construction (list/overview actions only).
STATE_PROBES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("development_control", {"action": "list_threads", "limit": 8}),
    ("development_control", {"action": "list_workflows", "limit": 8}),
    ("kanban_ticket", {"action": "workflow_overview"}),
    ("terminal_overview", {"action": "list"}),
)
PROBE_CHAR_BUDGET = 1400


def is_state_question(text: str | None) -> bool:
    return bool(_STATE_RE.search(str(text or "")))


@dataclass
class GroundingResult:
    captured_at: datetime
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        return [call["tool"] for call in self.calls]

    def freshness_label(self) -> str:
        local = self.captured_at.astimezone()
        return local.strftime("%H:%M:%S %Z").strip()

    def to_prompt_block(self, user_text: str) -> str:
        lines = [
            f"[LIVE DEVELOPMENT STATE — fetched by Decisions at {self.freshness_label()}, just now]",
        ]
        for call in self.calls:
            args = ", ".join(f"{k}={v}" for k, v in call["args"].items())
            status = "ok" if call["ok"] else "failed"
            lines.append(f"## {call['tool']}({args}) [{status}]")
            lines.append(str(call["result"])[:PROBE_CHAR_BUDGET])
        lines.append(
            "[ANSWER RULES] Answer only from the live state above and the Developer workflow "
            "context. Name the project/thread/ticket the data shows; if the data does not show "
            "what the user is working on, say so instead of guessing. The 'active_project' in "
            "context is only a global default unless its source says thread or chat. End with "
            f"how fresh this is, e.g. 'as of {self.captured_at.astimezone().strftime('%H:%M')}'."
        )
        lines.append("")
        lines.append("User request:")
        lines.append(user_text)
        return "\n".join(lines)


def ground_state(
    tools_dict: dict[str, Any],
    *,
    chat_id: int | None = None,
    project_id: int | None = None,
    record: Callable[..., Any] | None = None,
    record_start: Callable[..., Any] | None = None,
    probes: Iterable[tuple[str, dict[str, Any]]] = STATE_PROBES,
    now: Callable[[], datetime] | None = None,
) -> GroundingResult:
    """Run the read-only state probes and log each one to the chat event log."""
    result = GroundingResult(captured_at=(now or (lambda: datetime.now(timezone.utc)))())
    if record is None or record_start is None:
        try:
            from distr.core.agent.tool_audit import record_tool_execution, record_tool_start

            record = record or record_tool_execution
            record_start = record_start or record_tool_start
        except Exception:
            record = record or (lambda *a, **k: None)
            record_start = record_start or (lambda *a, **k: None)
    for tool_name, base_args in probes:
        args = dict(base_args)
        if project_id and tool_name == "development_control":
            args["project_id"] = int(project_id)
        tool = tools_dict.get(tool_name)
        if tool is None:
            result.calls.append({"tool": tool_name, "args": args, "ok": False,
                                 "result": "Tool not loaded in this session."})
            continue
        metadata = {"arguments": args, "source": "state_grounding"}
        try:
            record_start(chat_id, tool_name, metadata=metadata)
        except Exception:
            logger.debug("state grounding start log failed", exc_info=True)
        try:
            output = tool._run(**args)
            ok = not str(output).lower().startswith("error")
        except Exception as exc:  # a failed probe is reported, never fatal
            output = f"Error: {exc}"
            ok = False
        try:
            record(chat_id, tool_name, str(output), "completed" if ok else "failed", metadata=metadata)
        except Exception:
            logger.debug("state grounding finish log failed", exc_info=True)
        result.calls.append({"tool": tool_name, "args": args, "ok": ok, "result": output})
    logger.info(
        "State grounding: chat=%s project=%s tools=%s at %s",
        chat_id, project_id, result.tool_names, result.captured_at.isoformat(),
    )
    return result
