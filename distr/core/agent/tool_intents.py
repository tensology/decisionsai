"""Deterministic tool-intent hints for obvious user requests.

Semantic retrieval is useful for fuzzy requests, but common operational
commands should not depend on embedding luck.  These hints force key tools into
the candidate set while still leaving the LLM room to choose and fill args.
"""

from __future__ import annotations

import re

try:
    from distr.core.agent.ticket_intent import classify_ticket_intent
except Exception:  # pragma: no cover - routing should never fail on import noise
    classify_ticket_intent = None


_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "clipboard_action",
        (
            r"\b(what'?s?|what\s+is|show|get|read|see)\s+(?:in|on)?\s*(?:my\s+|the\s+)?clipboard\b",
            r"\b(read|inspect|check|look\s+at|review|load)\b.*\bclipboard\b.*\b(talk|discuss|go\s+through|about\s+it|with\s+me)\b",
            r"\b(explain|elaborate|summari[sz]e|rework|rewrite)\s+(?:on\s+)?this\b",
            r"\b(?:set|write|put|copy)\s+(?:the\s+)?clipboard\s+(?:to|as)\b",
            r"\b(?:set|write|put|copy)\s+.+\s+(?:to|into|onto)\s+(?:my\s+|the\s+)?clipboard\b",
        ),
    ),
    (
        "create_ticket",
        (
            r"\b(create|make|add|new|draft)\s+(?:a\s+|an\s+)?(?:ticket|card|issue)\b",
            r"\b(create|make|add|new|draft)\s+(?:a\s+|an\s+)?(?:jira|trello)\s+(?:ticket|card|issue)\b",
        ),
    ),
    (
        "file_operations",
        (
            r"\b(rename|move|delete|remove|list|show|create|copy)\s+.+\b(file|folder|directory|downloads|desktop|documents)\b",
            r"\bwhat\s+files\s+are\s+on\b",
        ),
    ),
    (
        "convert_document",
        (
            r"\b(convert|turn|export|make)\s+.+\b(pdf|word|docx|document)\b",
            r"\bexport\s+as\s+pdf\b",
        ),
    ),
    (
        "create_step_runner",
        (
            r"\b(create|build|make|generate)\s+(?:a\s+|an\s+)?(?:step\s+runner|automation|workflow)\b",
        ),
    ),
    (
        "exit_app",
        (
            r"\b(exit|quit|close)\s+(?:the\s+)?(?:app|application|decisionsai)\b",
            r"^quit$",
        ),
    ),
)


def forced_tool_names_for_text(text: str) -> list[str]:
    """Return tool names that should be force-included for this request."""
    raw = (text or "").strip()
    if not raw:
        return []

    forced: list[str] = []
    ticket_intent_kind = ""
    if classify_ticket_intent:
        try:
            ticket_intent_kind = classify_ticket_intent(raw).kind
        except Exception:
            ticket_intent_kind = ""

    if ticket_intent_kind == "debug_decisions_ticket":
        forced.append("create_cursor_ticket")

    for tool_name, patterns in _RULES:
        if ticket_intent_kind == "debug_decisions_ticket" and tool_name == "create_ticket":
            continue
        if any(re.search(pattern, raw, re.IGNORECASE) for pattern in patterns):
            forced.append(tool_name)
    return forced
