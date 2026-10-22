"""Deterministic Oracle routing rule (routee2e 2026-10-22).

Rule:
1. ONE-SHOT actions (create/open a file, check email, transcribe audio, time, a single
   app action) complete in the current Oracle chat: no Development thread, no new chat.
2. PROJECT work (build/fix/edit/implement/add ... in a project) goes to a Development
   thread: the current thread when the user is inside one, else the named project's
   thread (send_instruction) or a new thread in that project (create_thread).
3. An instruction NEVER creates a new chat (hard guard in tool_execution_policy).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_ONE_SHOT_RE = re.compile(
    r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:"
    r"(?:create|make|write|save)\s+(?:a\s+)?(?:new\s+)?(?:file|note|text\s+file|md\s+file)\b[^?!]*\b(?:downloads?|desktop|documents)\b"
    r"|check\s+(?:my\s+)?(?:e-?mails?|inbox)"
    r"|transcribe\b"
    r"|open\s+(?:the\s+)?(?:file|folder|app)\b"
    r"|what(?:'s|\s+is)\s+the\s+time|what\s+time\s+is\s+it"
    r")",
    re.IGNORECASE,
)
_PROJECT_WORK_RE = re.compile(
    r"\b(?:add|build|fix|edit|implement|refactor|change|update|write|create|remove|debug|test)\b",
    re.IGNORECASE,
)
_THREAD_RE = re.compile(r"\b(?:development|dev)\s+thread\b|\bthread\b", re.IGNORECASE)


def _project_names() -> list[tuple[int, str]]:
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    with get_session() as db:
        return [(int(p.id), str(p.name or "")) for p in db.query(Project).all() if p.name]


def resolve_named_project(text: str, projects: Iterable[tuple[int, str]] | None = None) -> int | None:
    """Return the project id whose name appears in text (longest name wins), else None.

    Matching is on whole words, spacing-insensitive: "decisions ai" matches "DecisionsAI".
    """
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    grams = {"".join(words[k:k + n]) for n in range(1, 5) for k in range(len(words) - n + 1)}
    best: tuple[int, int] | None = None
    for pid, name in (projects if projects is not None else _project_names()):
        compact = "".join(re.findall(r"[a-z0-9]+", name.lower()))
        if compact and compact in grams and (best is None or len(compact) > best[1]):
            best = (pid, len(compact))
    return best[0] if best else None


def classify_instruction(
    text: str,
    *,
    in_dev_thread: bool = False,
    projects: Iterable[tuple[int, str]] | None = None,
) -> dict[str, Any]:
    """Classify one user instruction: {'route': 'one_shot'|'project'|'chat', 'project_id': ...}."""
    clean = str(text or "").strip()
    plist = list(projects) if projects is not None else None
    named = resolve_named_project(clean, plist) if clean else None
    from distr.core.workflow.development_threads import is_kanban_request

    if is_kanban_request(clean):
        # kanbanroute 2026-10-22: tickets/board -> Kanban tools; project scopes the board.
        return {"route": "kanban", "project_id": named}
    if in_dev_thread:
        return {"route": "project", "project_id": None, "continue_current_thread": True}
    if _ONE_SHOT_RE.search(clean) and named is None:
        return {"route": "one_shot", "project_id": None}
    if named is not None and (_PROJECT_WORK_RE.search(clean) or _THREAD_RE.search(clean)):
        return {"route": "project", "project_id": named}
    if _ONE_SHOT_RE.search(clean):
        return {"route": "one_shot", "project_id": None}
    return {"route": "chat", "project_id": None}


_EXPLICIT_NEW_THREAD_RE = re.compile(
    r"\b(?:create|start|open|make|spin\s+up|begin|add)\s+(?:a\s+|an\s+)?(?:new\s+|fresh\s+)?"
    r"(?:chat|thread|conversation|development\s+thread)\s+(?:to|in|for|under|on)\b",
    re.IGNORECASE,
)


def explicit_new_thread_request(text: str, projects: Iterable[tuple[int, str]] | None = None) -> bool:
    """True when the user's own words ask for a new chat/thread inside a named project.

    Such a request is carried out as a Development thread (project chats live there), even when
    the thing to ask is a plain question. Without a named project this stays False, so the
    no-new-chat guard is unaffected.
    """
    clean = str(text or "")
    if not _EXPLICIT_NEW_THREAD_RE.search(clean):
        return False
    try:
        return resolve_named_project(clean, projects) is not None
    except Exception:
        return False


def existing_project_thread_id(project_id: int | None) -> int | None:
    """Newest non-archived root Development thread of a project, else None."""
    if project_id is None:
        return None
    from distr.core.db import Chat, get_session
    from distr.core.db.workflow import DevelopmentWorkItem

    with get_session() as db:
        rows = (
            db.query(Chat)
            .join(DevelopmentWorkItem, DevelopmentWorkItem.chat_id == Chat.id)
            .filter(Chat.project_id == int(project_id), Chat.parent_id.is_(None), Chat.is_archived.isnot(True))
            .order_by(Chat.modified_date.desc(), Chat.id.desc())
            .limit(1)
            .all()
        )
        return int(rows[0].id) if rows else None
