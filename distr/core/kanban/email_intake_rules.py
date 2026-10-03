"""Strict board and project authorization for inbound email work intake."""

from __future__ import annotations

import json
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any


@dataclass(frozen=True)
class EmailLinkInfo:
    board_id: int | None = None
    project_id: int | None = None
    board_name: str = ""
    project_name: str = ""
    sender_email: str = ""


def normalize_email(value: str) -> str:
    return parseaddr(str(value or ""))[1].strip().lower()


def _policy(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        parsed = value
    else:
        try:
            parsed = json.loads(value or "{}")
        except Exception:
            parsed = {}
    section = parsed.get("email_intake") if isinstance(parsed, dict) else None
    return section if isinstance(section, dict) else {}


def resolve_email_link(sender: str) -> EmailLinkInfo:
    """Resolve an exact sender or configured domain to one valid board/project."""
    address = normalize_email(sender)
    if not address or "@" not in address:
        return EmailLinkInfo(sender_email=address)
    domain = address.rsplit("@", 1)[1]

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project

    matches: list[EmailLinkInfo] = []
    with get_session() as session:
        boards = session.query(KanbanBoard).filter(KanbanBoard.archived.is_(False)).all()
        for board in boards:
            project_id = int(getattr(board, "default_project_id", 0) or 0)
            if not project_id:
                continue
            project = session.query(Project).filter(Project.id == project_id).first()
            if project is None:
                continue
            policy = _policy(getattr(board, "orchestrator_policy", None))
            senders = {
                normalize_email(value)
                for key in ("senders", "addresses", "emails")
                for value in (policy.get(key) or [])
                if normalize_email(value)
            }
            domains = {
                str(value or "").strip().lower().lstrip("@")
                for value in (policy.get("domains") or [])
                if str(value or "").strip()
            }
            if address not in senders and domain not in domains:
                continue
            matches.append(EmailLinkInfo(
                board_id=int(board.id),
                project_id=project_id,
                board_name=str(board.name or f"Board {board.id}"),
                project_name=str(project.name or f"Project {project_id}"),
                sender_email=address,
            ))
    return matches[0] if len(matches) == 1 else EmailLinkInfo(sender_email=address)
