"""Relevance rules for which tools a chat turn gets to see.

Replaces the ad-hoc keyword blocks that used to live in
``core_mixin._get_filtered_tools`` with named intent groups, and gives the
embedding retriever a relevance threshold instead of a blind top-K:

* ``select_by_relevance`` keeps the top-K tools that clear a floor score and
  adds any extra tool that scores strongly, so a clearly relevant tool is never
  cut just because it ranked 16th, and near-zero matches stop padding the list.
* ``intent_tool_names`` maps a request to intent groups (Development state,
  planning, mail, calendar, delegated device work). Each group pins the tools
  that answer it.
* ``describe_tool`` gives MCP tools (Laya, Headroom, Composio, …) and built
  artifact tools a real description for retrieval instead of their class name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

# --- relevance threshold ----------------------------------------------------

# Cosine floors below which a tool is noise for this request, and the score at
# which a tool is relevant enough to include even beyond K.
RELEVANCE_FLOOR = {"sbert": 0.12, "tfidf": 0.01}
STRONG_MATCH = {"sbert": 0.45, "tfidf": 0.30}
MAX_EXTRA_STRONG = 6
MIN_KEEP = 3


def select_by_relevance(names: Sequence[str], scores: Sequence[float], k: int, backend: str) -> list[str]:
    """Return tool names ranked by score, cut by threshold rather than count alone."""
    if not names:
        return []
    floor = RELEVANCE_FLOOR.get(backend, 0.0)
    strong = STRONG_MATCH.get(backend, 1.01)
    ranked = sorted(range(len(names)), key=lambda i: float(scores[i]), reverse=True)
    selected: list[str] = []
    for position, index in enumerate(ranked):
        score = float(scores[index])
        if position < k:
            if score >= floor or position < MIN_KEEP and score > 0:
                selected.append(names[index])
        elif score >= strong and len(selected) < k + MAX_EXTRA_STRONG:
            selected.append(names[index])
        else:
            break
    return selected


# --- intent groups ----------------------------------------------------------

@dataclass(frozen=True)
class IntentGroup:
    name: str
    tools: tuple[str, ...]
    patterns: tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in self.patterns)


DEVELOPMENT_TOOLS = ("development_control", "kanban_ticket", "terminal_overview", "planning")

DEVELOPMENT_GROUP = IntentGroup(
    name="development",
    tools=DEVELOPMENT_TOOLS,
    patterns=(
        r"\bdevelopment\b",
        r"\b(?:dev|development)\s+(?:area|tab|page|side|threads?|work)\b",
        r"\b(?:kanban|ticket|tickets|backlog|sprint|lane|lanes)\b",
        r"\b(?:workflow|automation|agent)\s+runs?\b",
        r"\bruns?\s+(?:status|failed|running|stuck)\b",
        r"\b(?:terminal|terminals)\b",
        r"\b(?:codex|cursor|claude\s+code)\s+(?:thread|threads|run|session|is\s+doing)\b",
        r"\bwhat(?:'s|\s+is)\s+(?:going\s+on|happening|running)\b",
        r"\bwhat\b[^?.!]{0,40}\b(?:working|busy)\s+on\b",
        r"\b(?:current|active|this)\s+(?:project|thread)\b",
        r"\bwhich\s+(?:project|thread)\b",
        r"\b(?:status|progress)\s+(?:of|on)\s+(?:the\s+)?(?:project|ticket|thread|build|plan)\b",
        r"\b(?:plan|planning|roadmap|milestones?)\b",
    ),
)

CONVERSATION_GROUP = IntentGroup(
    name="conversation",
    tools=("read_conversation", "open_thread"),
    patterns=(
        r"\bwhat\s+(?:did\s+)?(?:i|we|he|she|they)\s+(?:say|said|tell|told|write|wrote|ask|asked)\s+(?:to|with)\b",
        r"\bread\s+(?:the|that|this|my)\s+(?:conversation|thread|chat)\b",
        r"\b(?:conversation|chat)\s+with\s+[A-Z]",
        r"\bread_conversation\b",
    ),
)

TENSOLOGY_MAIL_KEYWORDS = ("tensology mail", "tensology inbox", "mailshot")
GMAIL_KEYWORDS = ("gmail", "email", "inbox", "google workspace")
CALENDAR_KEYWORDS = (
    "google calendar", "gcal", "calendar event", "calendar events", "event creation",
    "create calendar", "bulk event", "recurring event",
)
DELEGATED_SOURCE_KEYWORDS = ("telegram", "remote", "desktop", "browser", "phone")
DELEGATED_WORK_KEYWORDS = (
    "pdf", "document", "attachment", "file", "scope", "plan", "prep", "prepare", "http://",
    "https://", "website", "web page", "playwright", "browseruse", "browser use", "screenshot", "click",
)
DELEGATED_HANDOFF_KEYWORDS = ("codex", "cursor", "handoff", "implement", "report back")


def _has(text: str, words: Iterable[str]) -> bool:
    return any(word in text for word in words)


def intent_groups_for_text(text: str) -> list[str]:
    """Names of the intent groups a request belongs to (ordered, no duplicates)."""
    qlow = (text or "").lower()
    groups: list[str] = []
    if DEVELOPMENT_GROUP.matches(qlow):
        groups.append("development")
    if CONVERSATION_GROUP.matches(text or ""):
        groups.append("conversation")
    explicit_tensology_mail = _has(qlow, TENSOLOGY_MAIL_KEYWORDS)
    explicit_google_mail = _has(qlow, ("gmail", "google mail", "google workspace"))
    generic_mail_read = (
        _has(qlow, ("mail", "email", "inbox"))
        and _has(qlow, ("check", "read", "show", "list", "new", "unread", "inbox"))
        and not _has(qlow, ("send", "draft", "reply", "write"))
    )
    if explicit_tensology_mail:
        groups.append("tensology_mail")
    if generic_mail_read and not explicit_tensology_mail and not explicit_google_mail:
        groups.append("any_mail")
    workspace_keywords = GMAIL_KEYWORDS + CALENDAR_KEYWORDS
    if not explicit_tensology_mail and not generic_mail_read and _has(qlow, workspace_keywords):
        groups.append("google_workspace")
    if (
        _has(qlow, DELEGATED_SOURCE_KEYWORDS)
        and _has(qlow, workspace_keywords + DELEGATED_WORK_KEYWORDS)
        and _has(qlow, DELEGATED_HANDOFF_KEYWORDS + ("mouse", "keyboard", "copy", "paste"))
    ):
        groups.append("delegated")
    return groups


GROUP_TOOLS: dict[str, tuple[str, ...]] = {
    "development": DEVELOPMENT_TOOLS,
    "conversation": CONVERSATION_GROUP.tools,
    "tensology_mail": ("tensology_workspace",),
    "any_mail": ("tensology_workspace", "google_workspace"),
    "google_workspace": ("google_workspace",),
    "delegated": ("delegated_workflow",),
}


def intent_tool_names(text: str) -> list[str]:
    names: list[str] = []
    for group in intent_groups_for_text(text):
        for tool in GROUP_TOOLS.get(group, ()):
            if tool not in names:
                names.append(tool)
    return names


def is_development_query(text: str) -> bool:
    return "development" in intent_groups_for_text(text)


# --- tool descriptions ------------------------------------------------------

MCP_SERVER_DESCRIPTIONS: dict[str, str] = {
    "laya": "Laya MCP: submit, route and track Laya jobs and their results.",
    "headroom": (
        "Headroom MCP: compress very large tool outputs or documents into a short reference and "
        "retrieve the full original text later; use for huge logs, long files, context budget."
    ),
    "composio": (
        "Composio MCP: act in connected third-party apps (Gmail, Slack, GitHub, Notion, Google "
        "Sheets, Linear, Jira and more) through Composio connected accounts."
    ),
}


def _humanize(name: str) -> str:
    return re.sub(r"[_\-]+", " ", name or "").strip()


def mcp_server_for_tool(tool: Any) -> str:
    server = str(getattr(tool, "_server_name", "") or "")
    if server:
        return server
    name = str(getattr(tool, "name", "") or "")
    if name.startswith("mcp__"):
        return name.split("__")[1] if name.count("__") >= 2 else ""
    return ""


def describe_tool(tool: Any, static: dict[str, str]) -> str:
    """Retrieval text for a tool: static map, then MCP server + own description."""
    class_name = type(tool).__name__
    desc = static.get(class_name)
    if desc:
        return desc
    own = str(getattr(tool, "description", "") or "").strip()
    name = str(getattr(tool, "name", "") or class_name)
    server = mcp_server_for_tool(tool)
    if server:
        server_key = next((key for key in MCP_SERVER_DESCRIPTIONS if key in server.lower()), "")
        mcp_tool = str(getattr(tool, "_mcp_tool_name", "") or name.split("__")[-1])
        prefix = MCP_SERVER_DESCRIPTIONS.get(server_key, f"{_humanize(server)} MCP server tool.")
        return " ".join(part for part in (prefix, f"Tool: {_humanize(mcp_tool)}.", own[:400]) if part)
    if own:
        return f"{_humanize(name)}: {own[:400]}"
    return class_name
