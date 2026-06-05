"""Deterministic routing helpers for ticket-related user requests.

This module is intentionally small and boring: it protects the product
workflow before an LLM gets a chance to improvise.  Natural "create a ticket"
requests should go to the Kanban Ticket Board.  Legacy .tickets/Cursor files
should only be used when the user explicitly asks for Cursor/project-ticket
behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from typing import Iterable


@dataclass(frozen=True)
class TicketIntent:
    """Classified ticket intent used by tools and tests."""

    kind: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class SkillRecommendation:
    """A skill that may help execute a ticket."""

    name: str
    reason: str


@dataclass(frozen=True)
class TicketDraft:
    """Deterministic ticket draft extracted before/after LLM summarisation."""

    title: str
    description: str
    acceptance_criteria: list[str] = field(default_factory=list)
    board_hint: str = ""
    project_hint: str = ""
    remote_target: str = ""


_CREATE_TICKET_RE = re.compile(
    r"\b(create|make|add|new|write|draft|open)\s+(?:a\s+|an\s+)?(?:kanban\s+|board\s+)?ticket\b",
    re.IGNORECASE,
)
_CURSOR_TICKET_RE = re.compile(
    r"\b("
    r"tell\s+cursor|"
    r"cursor\s+ticket|"
    r"create\s+(?:a\s+)?(?:cursor|\.tickets?)\s+ticket|"
    r"\.tickets?\s+(?:file|ticket)|"
    r"send\s+(?:this|that|it)?\s*to\s+cursor"
    r")\b",
    re.IGNORECASE,
)
_TICKET_FILE_DESTINATION_RE = re.compile(
    r"\b(create|make|add|new|write|draft|open)\s+"
    r"(?:a\s+|an\s+|this\s+|that\s+)?"
    r"(?:ticket|task|work\s+item)(?:\s+file)?\b"
    r".{0,80}?\b(?:in|into|to|inside|under|within)\s+"
    r"(?:the\s+|my\s+)?(?:downloads?|desktop|documents?|folder|directory|/"
    r"|~\/|[A-Za-z]:\\)",
    re.IGNORECASE | re.DOTALL,
)
_TYPE_OUT_TICKET_RE = re.compile(
    r"\b(type|type\s+out|write\s+out|enter|dictate)\b"
    r".{0,80}?\b(?:ticket|task|work\s+item)\b",
    re.IGNORECASE | re.DOTALL,
)
_EXTERNAL_TICKET_RE = re.compile(r"\b(jira|trello)\s+(?:ticket|card|issue)\b", re.IGNORECASE)
_DECISIONS_PROJECT_TICKET_RE = re.compile(
    r"\b(create|make|add|new|write|draft|open)\s+"
    r"(?:a\s+|an\s+|this\s+|that\s+)?(?:ticket|task|work\s+item)\s+"
    r"(?:for|in|into|to)\s+(?:the\s+)?decisions(?:ai| ai)?\b",
    re.IGNORECASE,
)
_DISCUSS_TICKET_RE = re.compile(
    r"\b(talk|discuss|load|open|think\s+through|help\s+me\s+with)\s+(?:about\s+)?(?:this\s+|that\s+)?ticket\b",
    re.IGNORECASE,
)
_IDE_HANDOFF_CONVERSATION_RE = re.compile(
    r"\b(?:can|could|would|should)\s+(?:we|you)\b"
    r"(?=.{0,260}?\b(?:cursor|codex|codecs)\b)"
    r"(?=.{0,260}?\b(?:talk|discuss|plan|conversation|before\s+sending|before\s+dispatch|"
    r"what\s+(?:work|to\s+do)|work\s+should\s+be\s+done|whether|if|approach)\b)",
    re.IGNORECASE | re.DOTALL,
)


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))


def is_debug_enabled() -> bool:
    """Return whether DecisionsAI is running in developer/debug mode.

    An explicit DEBUG environment variable wins. When absent, fall back to the
    repo-local .env so the desktop app behaves the same way as the dev server.
    """
    env_value = os.environ.get("DEBUG")
    if env_value is not None:
        return env_value.strip().lower() in {"1", "true", "yes", "on"}

    env_path = os.path.join(_repo_root(), ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                cleaned = line.strip()
                if not cleaned or cleaned.startswith("#") or "=" not in cleaned:
                    continue
                key, value = cleaned.split("=", 1)
                if key.strip() == "DEBUG":
                    return value.strip().strip('"\'').lower() in {"1", "true", "yes", "on"}
    except OSError:
        pass
    return False


def classify_ticket_intent(text: str) -> TicketIntent:
    """Classify ticket-related text before tool routing.

    The ordering is deliberate: explicit Cursor requests win over generic
    ticket creation, while generic creation always maps to Kanban.
    """
    raw = (text or "").strip()
    if not raw:
        return TicketIntent("none", 0.0, "empty text")

    if _TYPE_OUT_TICKET_RE.search(raw):
        return TicketIntent("type_text", 0.94, "explicit request to type ticket text")
    if _TICKET_FILE_DESTINATION_RE.search(raw):
        return TicketIntent("ticket_file", 0.94, "ticket requested in a filesystem destination")
    if _IDE_HANDOFF_CONVERSATION_RE.search(raw):
        return TicketIntent("ide_conversation", 0.90, "conversation about IDE handoff, not a durable handoff request")
    if _CURSOR_TICKET_RE.search(raw):
        return TicketIntent("cursor_ticket", 0.97, "explicit Cursor plugin handoff request")
    if _DECISIONS_PROJECT_TICKET_RE.search(raw) and is_debug_enabled():
        return TicketIntent("debug_decisions_ticket", 0.96, "DEBUG=True DecisionsAI .tickets request")
    if _EXTERNAL_TICKET_RE.search(raw):
        return TicketIntent("external_ticket", 0.84, "external ticket provider mentioned")
    if _DISCUSS_TICKET_RE.search(raw):
        return TicketIntent("discuss_ticket", 0.90, "ticket discussion request")
    if _CREATE_TICKET_RE.search(raw):
        return TicketIntent("kanban_ticket", 0.95, "generic ticket creation maps to Kanban")

    return TicketIntent("none", 0.0, "no ticket intent detected")


_WEAK_TICKET_TITLES = {
    "instruction from user",
    "user instruction",
    "user request",
    "request from user",
    "task",
    "new ticket",
    "ticket",
    "work item",
    "todo",
}


def is_weak_ticket_title(title: str) -> bool:
    """Return True for meta/vague ticket titles that should be replaced."""
    cleaned = re.sub(r"\s+", " ", (title or "").strip().lower())
    if not cleaned:
        return True
    if cleaned in _WEAK_TICKET_TITLES:
        return True
    return bool(
        re.fullmatch(
            r"(create|make|add|write|draft)\s+(a\s+|an\s+)?(ticket|task|work item)",
            cleaned,
        )
    )


def draft_ticket_from_request(text: str) -> TicketDraft:
    """Create a stable ticket draft from natural language.

    This is intentionally deterministic and conservative. It protects ticket
    quality when a small/free model returns meta wording such as "Instruction
    from user" or when no LLM is available.
    """
    raw = (text or "").strip()
    if not raw:
        return TicketDraft(title="New Ticket", description="No request text was provided.")

    request = _extract_primary_ticket_request(raw)
    board_hint = _extract_target_hint(request, ("board",))
    project_hint = _extract_project_hint(request)
    remote_target = ""
    lowered = request.lower()
    if "jira" in lowered:
        remote_target = "jira"
    elif "trello" in lowered:
        remote_target = "trello"

    title_source = _strip_ticket_command(request)
    title = _title_from_task_text(title_source)
    description = _description_from_task_text(title_source, raw)
    acceptance = _extract_acceptance_criteria(raw)

    return TicketDraft(
        title=title,
        description=description,
        acceptance_criteria=acceptance,
        board_hint=board_hint,
        project_hint=project_hint,
        remote_target=remote_target,
    )


def _extract_primary_ticket_request(text: str) -> str:
    patterns = (
        r"(?im)^\s*User instruction:\s*(.+)$",
        r"(?im)^\s*Instruction:\s*(.+)$",
        r"(?im)^\s*Request:\s*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    first_non_empty = next((line.strip() for line in text.splitlines() if line.strip()), text)
    return first_non_empty.strip()


def _strip_ticket_command(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(
        r"(?i)^(please\s+)?(can you\s+|could you\s+)?"
        r"(create|make|add|write|draft|open)\s+"
        r"(a\s+|an\s+|this\s+|that\s+)?"
        r"(jira\s+|trello\s+|kanban\s+|board\s+)?"
        r"(ticket|task|card|issue|work item)\s*",
        "",
        cleaned,
    ).strip()
    cleaned = re.sub(r"(?i)^(for|about|to|in|into)\s+", "", cleaned).strip()
    cleaned = re.sub(r"(?i)^[A-Za-z][A-Za-z0-9_.-]{2,40}\s+to\s+", "", cleaned).strip()
    cleaned = re.sub(
        r"(?i)\b(?:for|in|into|on)\s+(?:the\s+)?(?:decisions(?:ai| ai)?|jira|trello)\b",
        "",
        cleaned,
    ).strip(" -:,.")
    return cleaned or text.strip()


def _title_from_task_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip(" -:,."))
    cleaned = re.sub(r"(?i)^(that|this|it)\s+", "", cleaned).strip()
    if not cleaned:
        return "New Ticket"
    sentence = re.split(r"[.!?]\s+", cleaned, maxsplit=1)[0].strip()
    if len(sentence) > 90:
        sentence = sentence[:90].rsplit(" ", 1)[0].strip()
    if not sentence:
        return "New Ticket"
    first = sentence[0].upper() + sentence[1:]
    return first


def _description_from_task_text(task_text: str, raw_text: str) -> str:
    task = re.sub(r"\s+", " ", (task_text or "").strip(" -:,."))
    if not task:
        task = _extract_primary_ticket_request(raw_text)
    if not task.endswith((".", "!", "?")):
        task += "."
    return task[0].upper() + task[1:]


def _extract_acceptance_criteria(text: str) -> list[str]:
    criteria: list[str] = []
    for line in (text or "").splitlines():
        cleaned = line.strip()
        match = re.match(
            r"(?i)^(?:[-*]\s*)?(?:acceptance criteria|acceptance|done when|verify|validate|must):\s*(.+)$",
            cleaned,
        )
        if match:
            item = match.group(1).strip()
            if item:
                criteria.append(item)
    return criteria[:8]


def _extract_target_hint(text: str, labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?i)\b(?:{label_pattern})\s+['\"]?([A-Za-z0-9 _.-]+)['\"]?", text or "")
    return match.group(1).strip(" .,:;") if match else ""


def _extract_project_hint(text: str) -> str:
    match = re.search(
        r"(?i)\b(?:for|in|into|to)\s+(?:the\s+)?([A-Za-z][A-Za-z0-9_.-]{2,40})"
        r"(?=\s+(?:to|about|because|that|where|when|with)\b|[,.!?]|$)",
        text or "",
    )
    if not match:
        return ""
    hint = match.group(1).strip(" .,:;")
    if hint.lower() in {"a ticket", "ticket", "jira", "trello", "kanban"}:
        return ""
    return hint


_SKILL_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "systematic-debugging",
        ("bug", "error", "fail", "failing", "broken", "logs", "traceback", "regression", "stuck"),
        "The ticket appears to involve investigation or failure analysis.",
    ),
    (
        "webapp-testing",
        ("ui", "frontend", "browser", "playwright", "screen", "click", "form", "responsive", "web"),
        "The work likely needs browser/UI validation.",
    ),
    (
        "test-driven-development",
        ("test", "tests", "coverage", "pytest", "unit", "integration", "e2e", "regression"),
        "The request mentions tests or needs a test-first implementation path.",
    ),
    (
        "skill-creator",
        ("skill", "skills", "agent skill", "create a skill", "skill creation"),
        "The work is about creating or improving reusable agent skills.",
    ),
    (
        "ln-627-observability-auditor",
        ("log", "logs", "observability", "telemetry", "health", "status", "diagnostic"),
        "The ticket needs logging, health, or diagnostic evidence.",
    ),
    (
        "ln-643-api-contract-auditor",
        ("api", "endpoint", "schema", "contract", "request", "response", "payload"),
        "The ticket touches API contracts or structured payloads.",
    ),
    (
        "verification-before-completion",
        ("validate", "validation", "verify", "done", "acceptance", "criteria"),
        "The ticket needs explicit completion checks.",
    ),
)


def _available_skill_names(skills_root: str | None = None) -> set[str]:
    root = skills_root or os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..", "skills"))
    registry_path = os.path.join(root, "skills_registry.json")
    names: set[str] = set()
    try:
        with open(registry_path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            items: Iterable = data.get("skills") or data.values()
        else:
            items = data if isinstance(data, list) else []
        for item in items:
            if isinstance(item, dict):
                name = item.get("name") or item.get("id") or item.get("slug")
                if name:
                    names.add(str(name))
    except Exception:
        pass

    if not names and os.path.isdir(root):
        try:
            names.update(
                name
                for name in os.listdir(root)
                if os.path.isdir(os.path.join(root, name)) and not name.startswith(".")
            )
        except OSError:
            pass
    return names


def recommend_skills_for_ticket(text: str, *, max_recommendations: int = 3) -> list[SkillRecommendation]:
    """Recommend available skills from deterministic keyword rules."""
    haystack = (text or "").lower()
    if not haystack.strip():
        return []

    available = _available_skill_names()
    recommendations: list[SkillRecommendation] = []
    seen: set[str] = set()
    for skill_name, keywords, reason in _SKILL_RULES:
        if skill_name in seen:
            continue
        if available and skill_name not in available:
            continue
        if any(keyword in haystack for keyword in keywords):
            recommendations.append(SkillRecommendation(skill_name, reason))
            seen.add(skill_name)
        if len(recommendations) >= max_recommendations:
            break
    return recommendations


def format_skill_recommendations_markdown(recommendations: list[SkillRecommendation]) -> str:
    """Render recommendations as a compact ticket description section."""
    if not recommendations:
        return ""
    lines = ["", "## Recommended Skills"]
    for rec in recommendations:
        lines.append(f"- `{rec.name}` — {rec.reason}")
    return "\n".join(lines).strip()
