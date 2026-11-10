"""Repair safe WhatsApp/project-board drift while preserving valid links."""

from __future__ import annotations

from typing import Any

from distr.core.db import WhatsAppPhoneLink, get_session
from distr.core.db.kanban import KanbanBoard
from distr.core.db.projects import Project
from distr.core.kanban.lifecycle import ensure_delivery_lanes


def audit_and_repair_whatsapp_bindings() -> dict[str, Any]:
    """Recover referenced missing boards and mirror project links to its primary board."""
    report: dict[str, Any] = {
        "recovered_boards": [], "mirrored_links": [],
        "removed_duplicate_orphans": [], "unresolved_orphan_links": [],
    }
    with get_session() as session:
        boards = {int(board.id): board for board in session.query(KanbanBoard).all()}
        projects = session.query(Project).filter(Project.deleted_at.is_(None)).all()
        projects_by_board = {
            int(project.kanban_board_id): project
            for project in projects
            if str(project.kanban_board_id or "").isdigit()
        }
        links = session.query(WhatsAppPhoneLink).order_by(WhatsAppPhoneLink.id.asc()).all()

        for link in links:
            board_id = int(link.board_id)
            if board_id in boards:
                continue
            project = projects_by_board.get(board_id)
            if not project:
                valid_duplicate = next((
                    other for other in links
                    if other.id != link.id
                    and other.phone_jid == link.phone_jid
                    and int(other.board_id) in boards
                ), None)
                if valid_duplicate:
                    session.delete(link)
                    report["removed_duplicate_orphans"].append(int(link.id))
                else:
                    report["unresolved_orphan_links"].append(int(link.id))
                continue
            board = KanbanBoard(
                id=board_id,
                name=project.name,
                description=f"Board for project: {project.name}",
                source="database",
                default_project_id=int(project.id),
            )
            session.add(board)
            session.flush()
            ensure_delivery_lanes(session, board_id)
            boards[board_id] = board
            report["recovered_boards"].append(board_id)

        # One WhatsApp JID maps to exactly one link row. Prefer moving a
        # project-secondary board link onto the project's primary board when
        # the primary does not already own that chat — never clone.
        from distr.core.kanban.whatsapp_jid import whatsapp_jid_match_keys

        for project in projects:
            primary_id = int(project.kanban_board_id) if str(project.kanban_board_id or "").isdigit() else 0
            primary = boards.get(primary_id)
            if not primary:
                continue
            project_board_ids = {
                int(board.id) for board in boards.values()
                if int(board.default_project_id or 0) == int(project.id)
            }
            source_links = [link for link in links if int(link.board_id) in project_board_ids]
            primary_keys = set()
            for link in links:
                if int(link.board_id) != primary_id:
                    continue
                primary_keys.update(whatsapp_jid_match_keys(link.phone_jid, link.phone_number))
            for source in source_links:
                if int(source.board_id) == primary_id:
                    continue
                source_keys = set(whatsapp_jid_match_keys(source.phone_jid, source.phone_number))
                if source_keys & primary_keys:
                    continue
                source.board_id = primary_id
                primary_keys.update(source_keys)
                report["mirrored_links"].append({
                    "project_id": int(project.id),
                    "board_id": primary_id,
                    "phone_jid": source.phone_jid,
                    "moved_link_id": int(source.id),
                })
        session.commit()
    return report
