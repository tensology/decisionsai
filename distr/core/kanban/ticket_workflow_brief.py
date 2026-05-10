"""Structured ticket-to-workflow brief helpers."""

from __future__ import annotations

import re
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_RECOMMENDED_SKILLS_RE = re.compile(
    r"(?is)\n?##\s+Recommended Skills\s*\n(?P<body>.*?)(?=\n##\s+|\Z)"
)


def _plain_text(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_recommended_skills(description: str) -> tuple[str, list[str]]:
    match = _RECOMMENDED_SKILLS_RE.search(description or "")
    if not match:
        return description, []
    body = match.group("body") or ""
    skills: list[str] = []
    for line in body.splitlines():
        cleaned = line.strip().lstrip("-* ").strip()
        if not cleaned:
            continue
        skill_match = re.search(r"`([^`]+)`", cleaned)
        if skill_match:
            skills.append(skill_match.group(1).strip())
        else:
            skills.append(cleaned.split("—", 1)[0].strip())
    without = _RECOMMENDED_SKILLS_RE.sub("", description or "").strip()
    return without, [s for s in skills if s]


def _infer_acceptance_criteria(description: str, todos: list[dict]) -> list[str]:
    criteria = [item["text"] for item in todos if item.get("text") and not item.get("done")]
    lowered = description.lower()
    if not any(word in lowered for word in ("test", "verify", "validate", "regression")):
        criteria.append("Verify the change with the most relevant available test or manual check.")
    if not criteria:
        criteria.append("Complete the requested ticket and report the evidence.")
    return criteria[:8]


def build_ticket_workflow_brief(
    session: "Session",
    ticket_id: int,
    *,
    board_id: Optional[int] = None,
    board_name: Optional[str] = None,
    project_id: Optional[Any] = None,
    project_name: Optional[str] = None,
    project_folder: Optional[str] = None,
) -> dict[str, Any]:
    """Return a normalized brief for workflow runs started from a ticket."""
    from sqlalchemy.orm import joinedload

    from distr.core.db.kanban import KanbanLane, KanbanTicket

    ticket = (
        session.query(KanbanTicket)
        .options(
            joinedload(KanbanTicket.lane).joinedload(KanbanLane.board),
            joinedload(KanbanTicket.todos),
            joinedload(KanbanTicket.links),
            joinedload(KanbanTicket.files),
        )
        .filter(KanbanTicket.id == ticket_id)
        .first()
    )
    if not ticket:
        return {
            "source_type": "kanban_ticket",
            "ticket_id": ticket_id,
            "objective": f"Process ticket #{ticket_id}",
            "context": "",
            "acceptance_criteria": ["Report that the ticket could not be loaded."],
        }

    lane_name = ticket.lane.name if ticket.lane else ""
    inferred_board = ticket.lane.board if ticket.lane and ticket.lane.board else None
    actual_board_id = board_id if board_id is not None else (inferred_board.id if inferred_board else None)
    actual_board_name = board_name or (inferred_board.name if inferred_board else "")

    raw_description = _plain_text(ticket.description or "")
    description, recommended_skills = _extract_recommended_skills(raw_description)
    todos = [
        {
            "text": (todo.text or "").strip(),
            "done": bool(todo.done),
            "position": int(todo.position or 0),
        }
        for todo in sorted(ticket.todos or [], key=lambda item: item.position or 0)
        if (todo.text or "").strip()
    ]
    links = [
        {"title": (link.title or "").strip(), "url": (link.url or "").strip()}
        for link in (ticket.links or [])
        if (link.url or "").strip()
    ]
    attachments = [
        {"filename": (file.filename or "").strip(), "path": (file.file_path or "").strip()}
        for file in (ticket.files or [])
        if (file.file_path or "").strip()
    ]
    objective = (ticket.title or "").strip() or f"Ticket #{ticket.id}"

    context_lines = []
    if description:
        context_lines.append(description)
    if links:
        context_lines.append("Links:\n" + "\n".join(f"- {item['title'] or 'link'}: {item['url']}" for item in links))
    if attachments:
        context_lines.append("Attachments:\n" + "\n".join(f"- {item['filename']}: {item['path']}" for item in attachments))

    return {
        "source_type": "kanban_ticket",
        "ticket_id": int(ticket.id),
        "ticket_title": objective,
        "objective": objective,
        "context": "\n\n".join(context_lines).strip(),
        "priority": ticket.priority or "medium",
        "board_id": actual_board_id,
        "board_name": actual_board_name or "",
        "lane_name": lane_name or "",
        "project_id": str(project_id) if project_id is not None else None,
        "project_name": project_name or "",
        "project_folder": project_folder or "",
        "external": {
            "source": ticket.external_source or "",
            "id": ticket.external_id or "",
            "url": ticket.external_url or "",
        },
        "todos": todos,
        "links": links,
        "attachments": attachments,
        "recommended_skills": recommended_skills,
        "acceptance_criteria": _infer_acceptance_criteria(description, todos),
        "verification_requirements": [
            "Do not mark the workflow complete until the acceptance criteria have evidence.",
            "If blocked, report the blocker and the last observed evidence instead of looping.",
        ],
    }


def render_ticket_workflow_brief(brief: dict[str, Any]) -> str:
    """Render a compact, deterministic prompt block from a brief dict."""
    lines = [
        "[TICKET WORKFLOW BRIEF]",
        f"Ticket ID: {brief.get('ticket_id', '')}",
        f"Objective: {brief.get('objective') or brief.get('ticket_title') or ''}",
        f"Priority: {brief.get('priority') or 'medium'}",
    ]
    if brief.get("board_name"):
        lines.append(f"Board: {brief['board_name']}")
    if brief.get("lane_name"):
        lines.append(f"Lane: {brief['lane_name']}")
    if brief.get("project_name"):
        lines.append(f"Project: {brief['project_name']}")
    if brief.get("project_folder"):
        lines.append(f"Project folder: {brief['project_folder']}")
    external = brief.get("external") or {}
    if external.get("source") or external.get("id") or external.get("url"):
        lines.append(f"External: {external.get('source', '')} {external.get('id', '')} {external.get('url', '')}".strip())
    context = (brief.get("context") or "").strip()
    if context:
        lines.extend(["", "Context:", context])
    if brief.get("recommended_skills"):
        lines.extend(["", "Recommended skills:"])
        lines.extend(f"- {name}" for name in brief["recommended_skills"])
    if brief.get("acceptance_criteria"):
        lines.extend(["", "Acceptance criteria:"])
        lines.extend(f"- {item}" for item in brief["acceptance_criteria"])
    if brief.get("verification_requirements"):
        lines.extend(["", "Verification requirements:"])
        lines.extend(f"- {item}" for item in brief["verification_requirements"])
    return "\n".join(str(line) for line in lines).strip()
