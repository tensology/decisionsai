"""Light plan step for multi-step chat requests.

When a request clearly asks for several things in sequence, the model is asked
to write a short numbered plan before its first tool call and to report each
step's outcome at the end. Single-step requests are left alone so simple turns
stay fast.
"""

from __future__ import annotations

import re

_SEQUENCE_RE = re.compile(
    r"\b(?:and\s+then|then|after\s+that|afterwards|once\s+(?:that'?s|that\s+is|it'?s|you'?ve)\s+done|"
    r"followed\s+by|next,?|finally|first\b[^.?!]{3,120}\b(?:then|and|after))\b",
    re.IGNORECASE,
)
_NUMBERED_RE = re.compile(r"(?:^|\n)\s*(?:\d+[.)]|[-*•])\s+\S", re.MULTILINE)
_ACTION_VERBS = (
    "check", "read", "open", "create", "make", "send", "write", "update", "move", "fix", "find",
    "list", "summarise", "summarize", "draft", "reply", "add", "remove", "delete", "run", "start",
    "schedule", "book", "compare", "review", "deploy", "build", "test", "email", "message",
    "post", "save", "download", "upload", "rename", "close", "plan", "look", "search",
)
_VERB_RE = re.compile(r"\b(?:" + "|".join(_ACTION_VERBS) + r")\b", re.IGNORECASE)
_CLAUSE_SPLIT_RE = re.compile(r"(?:,|;|\band\b|\bthen\b|\balso\b)", re.IGNORECASE)

PLAN_HEADER = "[PLAN FIRST]"


def is_multi_step_request(text: str) -> bool:
    clean = str(text or "").strip()
    if len(clean) < 20 or clean.startswith(PLAN_HEADER):
        return False
    if len(_NUMBERED_RE.findall(clean)) >= 2:
        return True
    clauses_with_verbs = sum(1 for clause in _CLAUSE_SPLIT_RE.split(clean) if _VERB_RE.search(clause or ""))
    if _SEQUENCE_RE.search(clean) and clauses_with_verbs >= 2:
        return True
    return clauses_with_verbs >= 3


def plan_instruction() -> str:
    return (
        f"{PLAN_HEADER} This request has several steps. Before your first tool call, write a short "
        "numbered plan (at most 5 lines, one line per step). Then do the steps in order. Do not start a "
        "workflow, send anything, or change anything the user did not ask for. In your final answer, "
        "list each step as done, skipped or blocked, with the reason for anything not done."
    )


def with_plan_step(user_text: str, content: str) -> tuple[str, bool]:
    """Return (content, planned). Appends the plan instruction for multi-step requests."""
    if not isinstance(content, str) or not is_multi_step_request(user_text):
        return content, False
    return content.rstrip() + "\n\n" + plan_instruction(), True
