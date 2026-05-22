"""Detection and guardrails for multi-action user requests."""

from __future__ import annotations

import re
from dataclasses import dataclass


_DAY_HEADING_RE = re.compile(r"^\s*day\s+\d+\b", re.IGNORECASE | re.MULTILINE)
_NUMBERED_RE = re.compile(r"^\s*(?:\d+[\.)]|[-*+])\s+\S", re.MULTILINE)
_TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:am|pm)?\b", re.IGNORECASE)
_EXECUTE_RE = re.compile(
    r"\b(?:execute|run|do|perform|set\s+up|create|schedule|process|carry\s+out|follow)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BulkInstructionProfile:
    """Summary of a user message that looks like a multi-action request."""

    is_bulk: bool
    char_count: int
    line_count: int
    day_heading_count: int
    list_item_count: int
    time_count: int
    asks_execution: bool


def profile_bulk_instruction(text: str) -> BulkInstructionProfile:
    """Return a lightweight profile for large pasted or batched instructions."""

    text = text or ""
    lines = [line for line in text.splitlines() if line.strip()]
    day_heading_count = len(_DAY_HEADING_RE.findall(text))
    list_item_count = len(_NUMBERED_RE.findall(text))
    time_count = len(_TIME_RE.findall(text))
    char_count = len(text)
    line_count = len(lines)
    structured_density = day_heading_count >= 3 or list_item_count >= 8 or time_count >= 12
    is_bulk = (
        char_count >= 3500
        or line_count >= 45
        or (char_count >= 1800 and structured_density)
    )
    return BulkInstructionProfile(
        is_bulk=is_bulk,
        char_count=char_count,
        line_count=line_count,
        day_heading_count=day_heading_count,
        list_item_count=list_item_count,
        time_count=time_count,
        asks_execution=bool(_EXECUTE_RE.search(text)),
    )


def augment_bulk_instruction(text: str, *, source: str = "chat") -> str:
    """Wrap large multi-action instructions with orchestration guardrails.

    The raw user text is preserved exactly after the guardrail block so tools and
    downstream agents still receive the original content.
    """

    profile = profile_bulk_instruction(text)
    if not profile.is_bulk:
        return text

    mode = (
        "The user appears to be asking for execution."
        if profile.asks_execution
        else "The user may only be asking for assessment or planning."
    )
    return (
        "[Multi-Action Intake]\n"
        f"Source: {source}\n"
        f"Size: {profile.char_count} characters, {profile.line_count} non-empty lines.\n"
        f"Structure signals: {profile.day_heading_count} day headings, "
        f"{profile.list_item_count} list items, {profile.time_count} time-like entries.\n"
        f"{mode}\n\n"
        "Before executing tool actions from this request:\n"
        "1. Split the request into an ordered action queue with dependencies and blockers.\n"
        "2. Execute ready actions through the correct tools instead of treating the request as a single vague instruction.\n"
        "3. Verify each material tool result before moving to dependent actions.\n"
        "4. Separate reversible local/read-only work from external, destructive, sending, deleting, purchasing, deployment, or mass-creation actions.\n"
        "5. Ask one compact clarification or approval question before risky or ambiguous external actions. Do not silently create many external items when key details are missing.\n"
        "6. Prefer batch-capable tools for repeated actions, and report progress as grouped phases rather than narrating every individual item.\n"
        "7. Preserve the user's original intent and do not drop later sections of the request.\n\n"
        "[Original User Packet]\n"
        f"{text}"
    )
