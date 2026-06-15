"""
Full Kanban ticket context for Pi / project CLI (Stage 0 §2.5).

Mirrors the richness of send-to-project markdown: board/lane, todos, links,
attachments, optional WhatsApp source — so CLI sees what workflows see.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Pi prompts should stay bounded; metadata + task typically << this cap.
CLI_INSTRUCTION_MAX_CHARS = 16_000


def build_kanban_ticket_cli_instruction(
    session: "Session",
    ticket_id: int,
    *,
    project_name: str = "",
    project_folder: str = "",
    project_id: Optional[int] = None,
    max_total_chars: int = CLI_INSTRUCTION_MAX_CHARS,
) -> str:
    """Load ticket row + relations and build one instruction string for pi.

    Structure:
      1. Structured Kanban metadata (board, lane, checklist, links, files, WhatsApp)
      2. PRIMARY TASK — title + description (truncated last if needed)
    """
    from sqlalchemy.orm import joinedload

    from distr.core.db import WhatsAppMessage
    from distr.core.db.kanban import KanbanLane, KanbanTicket

    t = (
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
    if not t:
        return f"[Kanban ticket #{ticket_id} not found — send minimal instructions only.]"

    board_name = ""
    lane_name = ""
    if t.lane:
        lane_name = t.lane.name or ""
        if t.lane.board:
            board_name = t.lane.board.name or ""

    lines: list[str] = [
        "[KANBAN TICKET CONTEXT — read before acting]",
        f"Ticket ID: {t.id}",
        f"Board: {board_name or '(unknown)'}",
        f"Lane: {lane_name or '(unknown)'}",
        f"Priority: {t.priority or 'medium'}",
        f"Complexity: {getattr(t, 'complexity', None) or 'medium'}",
    ]
    if t.time_estimate:
        lines.append(f"Time estimate: {t.time_estimate}")
    if t.time_spent:
        lines.append(f"Time spent: {t.time_spent}")
    if project_name:
        pid = project_id if project_id is not None else ""
        lines.append(f"Linked project: {project_name} (id={pid})")
    if project_folder:
        lines.append(f"Project folder: {project_folder}")

    if t.external_source or t.external_url or t.external_id:
        lines.append("")
        lines.append("External source:")
        if t.external_source:
            lines.append(f"  - Source: {t.external_source}")
        if t.external_id:
            lines.append(f"  - External ID: {t.external_id}")
        if t.external_url:
            lines.append(f"  - URL: {t.external_url}")

    if getattr(t, "source_provider", None):
        lines.append("")
        lines.append("Ticket source:")
        lines.append(f"  - Provider: {t.source_provider}")
        if getattr(t, "source_contact", None):
            lines.append(f"  - Contact: {t.source_contact}")
        if getattr(t, "source_external_id", None):
            lines.append(f"  - Source ID: {t.source_external_id}")
        if getattr(t, "source_thread_id", None):
            lines.append(f"  - Thread/chat: {t.source_thread_id}")
        if getattr(t, "source_url", None):
            lines.append(f"  - URL: {t.source_url}")

    if t.whatsapp_message_id:
        wm = session.query(WhatsAppMessage).filter(
            WhatsAppMessage.id == t.whatsapp_message_id,
        ).first()
        if wm:
            lines.append("")
            lines.append("WhatsApp source:")
            lines.append(f"  - WhatsApp message DB id: {wm.id}")
            if wm.jid_phone:
                lines.append(f"  - Phone / chat: {wm.jid_phone}")
            elif wm.jid:
                lines.append(f"  - JID: {wm.jid}")
            if wm.media_type or wm.media_local_path:
                lines.append(f"  - Media type: {wm.media_type or 'n/a'}")
                if wm.media_local_path:
                    lines.append(f"  - Media path (relative or resolved via DB): {wm.media_local_path}")
            snippet = (wm.text or wm.caption or "").strip()
            if snippet:
                snippet = snippet.replace("\r\n", "\n")[:800]
                lines.append(f"  - Message excerpt: {snippet}")

    if t.todos:
        lines.append("")
        lines.append("Checklist:")
        for td in sorted(t.todos, key=lambda x: x.position):
            mark = "x" if td.done else " "
            lines.append(f"  - [{mark}] {td.text}")

    if t.links:
        lines.append("")
        lines.append("Links:")
        for lk in t.links:
            lines.append(f"  - {lk.title}: {lk.url}")

    if t.files:
        lines.append("")
        lines.append("Attached files:")
        for fl in t.files:
            lines.append(f"  - {fl.filename} (`{fl.file_path}`)")

    if getattr(t, "context_notes", None):
        notes = (t.context_notes or "").strip()
        if notes:
            lines.append("")
            lines.append("Ticket notes (orchestrator):")
            for note_line in notes.splitlines()[-12:]:
                lines.append(f"  - {note_line}")

    meta_block = "\n".join(lines).strip()
    title = (t.title or "").strip() or f"Ticket #{t.id}"
    description = (t.description or "").strip()

    header = meta_block
    sep = "\n\n--- PRIMARY TASK ---\n"
    primary_plain = f"{title}\n\n{description}".strip() if description else title

    budget = max_total_chars - len(header) - len(sep) - 80
    if budget < 200:
        budget = max_total_chars // 4
    if len(primary_plain) > budget:
        primary_plain = primary_plain[:budget].rstrip() + "\n\n[Description truncated for CLI size limit]"

    return f"{header}{sep}{primary_plain}"
