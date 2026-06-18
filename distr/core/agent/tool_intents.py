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
        "codex_thread_context",
        (
            r"\b(codex|codecs)\b.*\b(conversations?|threads?|chats?|sessions?|transcripts?|history)\b",
            r"\b(conversations?|threads?|chats?|sessions?|transcripts?|history)\b.*\b(codex|codecs)\b",
            r"\b(codex|codecs)\b.*\b(work\s+with|bring\s+in|pull\s+in|load|summari[sz]e|turn\s+.*\b(ticket|plan|skill)|what\s+happened)\b",
            r"\b(what\s+am\s+i\s+doing|where\s+am\s+i|workload|working\s+on)\b.*\b(codex|codecs)\b",
        ),
    ),
    (
        "ide_thread",
        (
            r"\b(cursor|codex|codecs)\b.*\b(thread|chat|session|response|reply|doing|status|latest)\b",
            r"\b(read|check|what\s+did|latest)\b.*\b(cursor|codex|codecs)\b",
            r"\b(send|prompt|continue|amend|resume)\b.*\b(cursor|codex|codecs)\b.*\b(thread|chat|session)\b",
            r"\bwhat\s+is\s+(cursor|codex|codecs)\b.*\b(doing|working)\b",
        ),
    ),
    (
        "proactive_orchestrator",
        (
            r"\b(proactive|morning|lunch|evening|check\s+work|work\s+coming\s+in|prioriti[sz]e|what\s+is\s+important)\b.*\b(gmail|slack|whats\s*app|telegram|trello|jira|boards?|codex|codecs|cursor|project)\b",
            r"\b(gmail|slack|whats\s*app|telegram|trello|jira|boards?)\b.*\b(prioriti[sz]e|important|check|scan|work\s+coming\s+in|what\s+matters)\b",
            r"\b(daily\s+plan|day\s+plan|today'?s\s+plan|plan\s+my\s+day|morning\s+brief|what\s+should\s+i\s+do\s+today|what'?s\s+my\s+plan)\b",
            r"\b(plan|prioriti[sz]e)\b.*\b(today|my\s+day|daily|emails?|gmail|whats\s*app|tickets?|boards?|projects?|workflows?)\b",
            r"\b(where\s+am\s+i|what\s+am\s+i\s+doing|workload|working\s+on)\b.*\b(cursor|codex|codecs)\b",
            r"\b(cursor|codex|codecs)\b.*\b(workload|working\s+on|what\s+am\s+i\s+doing|where\s+am\s+i)\b",
        ),
    ),
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
            r"\b(move|transfer|relocate)\b.+\b(ticket|card|issue)\b.+\bboard\b",
            r"\b(ticket|card|issue)\b.+\b(move|transfer|relocate)\b.+\bboard\b",
            r"\bwhats\s*app\b.+\b(sync|latest|activity|overview|contacts?|chats?|messages?|thread|context|snapshot|ticket|reply|send)\b",
            r"\b(sync|latest|activity|overview|list|show|read|open|snapshot|create|make|draft|reply|send)\b.+\bwhats\s*app\b",
            r"\b(groups?|chats?|threads?)\b.+\b(messages?|photos?|screenshots?|voice\s+notes?)\b",
            r"\b(messages?|photos?|screenshots?|voice\s+notes?)\b.+\b(groups?|chats?|threads?)\b",
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
        "scheduled_action",
        (
            r"\b(schedule|scheduled|recurring|every\s+(?:day|weekday|week|morning|evening))\b.*\b(action|desktop|keypress|key\s*press|type|open|recording|chrome|app|automation)\b",
            r"\b(list|show|what|cancel|delete|disable|enable|reschedule|move)\b.*\bscheduled\s+(?:desktop\s+)?actions?\b",
            r"\b(cancel|delete|disable|enable|reschedule|move)\b.*\baction\s+\d+\b",
            r"\bopen\s+\w+\b.*\b(every\s+(?:day|weekday|week)|daily|weekly|weekdays?)\b",
        ),
    ),
    (
        "google_workspace",
        (
            r"\b(gmail|email)\b.*\battachment",
            r"\battachment\b.*\b(gmail|email|inbox)\b",
            r"\bdownload\b.*\b(gmail|email)\b.*\battachment",
            r"\b(get|grab|pull|save)\b.*\b(gmail|email)\b.*\battachment",
        ),
    ),
    (
        "screenshot_analyzer",
        (
            r"\b(take|capture|grab|get)\s+(?:a\s+)?(?:screenshot|screen\s*shot|picture)\b",
            r"\b(give|send|show)\s+(?:me\s+)?(?:a\s+)?screenshots?\b",
            r"\bscreenshot\s+(?:of\s+)?screen\s+\d+\b",
            r"\b(what\s+do\s+you\s+see|what'?s?\s+on\s+(?:the\s+)?screen|describe\s+(?:the\s+)?screen|what'?s?\s+on\s+my\s+screen)\b",
            r"\b(see|look\s+at|analyze|check|examine)\b.*?\b(?:my\s+)?(?:screen|display|monitor)\b",
            r"\bsee\s+what\s+i(?:'m|\s+am)\s+looking\s+at\b",
            r"\bscreen\s+(?:capture|interaction|shot)\b",
        ),
    ),
    (
        "visual_baseline",
        (
            r"\b(visual\s+baselines?|baseline\s+sets?|reference\s+screens?|gold(?:en)?\s+standard)\b",
            r"\b(save|capture|create|add)\b.*\b(screenshot|screen)\b.*\b(baseline|reference|gold(?:en)?\s+standard)\b",
            r"\b(list|show|get|inspect|check|audit|ready|readiness)\b.*\b(visual\s+baselines?|baseline\s+sets?|reference\s+screens?)\b",
            r"\b(visual\s+baselines?|baseline\s+sets?|reference\s+screens?)\b.*\b(ready|readiness|missing|exist|usable)\b",
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
    elif ticket_intent_kind == "ticket_file":
        forced.append("file_operations")
    elif ticket_intent_kind == "type_text":
        forced.append("type_text")

    for tool_name, patterns in _RULES:
        if ticket_intent_kind in {"debug_decisions_ticket", "ticket_file", "type_text"} and tool_name == "create_ticket":
            continue
        if any(re.search(pattern, raw, re.IGNORECASE) for pattern in patterns):
            forced.append(tool_name)
    return forced
