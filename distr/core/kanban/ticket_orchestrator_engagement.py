"""
Build user-visible briefs and hidden orchestrator prompts for ticket-board engagement.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any, Dict, Mapping, Optional, Tuple

DISCUSS_PROMPT_MAX_CHARS = 14000

_TAG_RE = re.compile(r"<[^>]+>")
logger = logging.getLogger(__name__)


def strip_html(raw: str) -> str:
    """Best-effort plain text from HTML / ADF snippets."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", str(raw))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _ticket_title(ticket: Mapping[str, Any]) -> str:
    return (ticket.get("title") or "").strip() or "(untitled)"


def build_display_brief(ticket: Mapping[str, Any]) -> str:
    """Short line shown in chat activity when the user sends a ticket to the orchestrator."""
    title = _ticket_title(ticket)
    return f'Sent ticket "{title}" to the orchestrator.'


def _project_context(
    ticket: Mapping[str, Any], board_data: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    ticket = ticket or {}
    board_data = board_data or {}
    linked_project_id = ticket.get("linked_project_id")
    board_project_id = board_data.get("default_project_id")
    effective_project_id = linked_project_id or board_project_id
    project_name = (
        ticket.get("linked_project_name")
        or ticket.get("project_name")
        or board_data.get("default_project_name")
        or board_data.get("project_name")
        or board_data.get("activated_project_name")
        or ""
    )
    project_folder = (
        ticket.get("linked_project_folder")
        or ticket.get("project_folder")
        or ticket.get("folder_location")
        or board_data.get("default_project_folder")
        or board_data.get("project_folder")
        or board_data.get("folder_location")
        or ""
    )
    source_label = (
        "ticket link"
        if linked_project_id
        else ("board default" if board_project_id else "")
    )
    return {
        "has_project": bool(effective_project_id),
        "id": effective_project_id,
        "name": project_name,
        "folder": project_folder,
        "source": source_label,
    }


def activate_engagement_context(
    *,
    local_board_id: Optional[int],
    ticket: Mapping[str, Any],
    board_data: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Mark the linked board and project as active before orchestrator handoff."""
    ctx = dict(board_data or {})
    ticket = ticket or {}
    project_id = ticket.get("linked_project_id") or ctx.get("default_project_id")

    if local_board_id:
        try:
            from distr.core.db import get_session
            from distr.core.db.kanban import KanbanBoard
            from distr.core.db.orm_compat import orm_get_by_id

            with get_session() as session:
                session.query(KanbanBoard).filter(KanbanBoard.in_use.is_(True)).update(
                    {"in_use": False}
                )
                board = orm_get_by_id(session, KanbanBoard, int(local_board_id))
                if board:
                    board.in_use = True
                    session.commit()
                    ctx["activated_board_id"] = int(board.id)
                    ctx["activated_board_name"] = board.name or ""
                    if not project_id and board.default_project_id:
                        project_id = int(board.default_project_id)
                    try:
                        from distr.gui.web.kanban_events import increment_kanban_updated

                        increment_kanban_updated(int(board.id), "board_activated")
                    except Exception:
                        pass
        except Exception:
            logger.debug("activate_engagement_context: board activation failed", exc_info=True)

    if project_id:
        try:
            from distr.core.agent.services.rag.project import activate_project

            result = activate_project(int(project_id))
            if result.get("success"):
                ctx["activated_project_id"] = int(project_id)
                ctx["activated_project_name"] = (
                    result.get("project_name") or ctx.get("default_project_name") or ""
                )
        except Exception:
            logger.debug("activate_engagement_context: project activation failed", exc_info=True)

    return ctx


def build_agent_context(
    ticket: Mapping[str, Any],
    *,
    is_local: bool,
    board_label: str = "",
    source: str = "database",
    board_data: Optional[Mapping[str, Any]] = None,
) -> str:
    """Full orchestrator instructions + ticket context (never shown in chat UI)."""
    ticket = ticket or {}
    board_data = board_data or {}
    title = _ticket_title(ticket)
    desc_raw = ticket.get("description") or ""
    desc = strip_html(desc_raw)
    if len(desc) > DISCUSS_PROMPT_MAX_CHARS:
        desc = desc[:DISCUSS_PROMPT_MAX_CHARS] + "\n…[description truncated for chat size]"

    id_part = (
        f"Local ticket id: {ticket.get('id')}"
        if is_local
        else f"External id: {ticket.get('id')}"
    )
    url_line = f"\n- URL: {ticket['url']}" if ticket.get("url") else ""

    meta: list[str] = []
    if ticket.get("time_estimate"):
        meta.append(f"Estimate: {ticket['time_estimate']}")
    if ticket.get("time_spent"):
        meta.append(f"Spent: {ticket['time_spent']}")
    if ticket.get("priority"):
        meta.append(f"Priority: {ticket['priority']}")
    if ticket.get("complexity"):
        meta.append(f"Complexity: {ticket['complexity']}")
    members = ticket.get("members") or []
    if members:
        meta.append("People: " + ", ".join(str(m) for m in members))
    labels = ticket.get("labels") or []
    if labels:
        meta.append("Labels: " + ", ".join(str(l) for l in labels))
    meta_block = ("\n- " + "\n- ".join(meta)) if meta else ""

    project_context = _project_context(ticket, board_data)
    if project_context["has_project"]:
        project_block = (
            "\n\n**Linked project**\n"
            f"- Project id: {project_context['id']}"
            + (f"\n- Project name: {project_context['name']}" if project_context["name"] else "")
            + (f"\n- Project folder: {project_context['folder']}" if project_context["folder"] else "")
            + (f"\n- Link source: {project_context['source']}" if project_context["source"] else "")
        )
    else:
        project_block = (
            "\n\n**Linked project**\n"
            "- None visible on the ticket or board. If project context matters, ask which project to use."
        )

    activation_lines: list[str] = []
    activated_board = board_data.get("activated_board_name") or board_label
    if board_data.get("activated_board_id") or activated_board:
        activation_lines.append(
            f"- Active board: {activated_board or board_label or '(unknown)'} "
            f"(id {board_data.get('activated_board_id', 'n/a')})"
        )
    if board_data.get("activated_project_id") or board_data.get("activated_project_name"):
        activation_lines.append(
            f"- Active project: {board_data.get('activated_project_name') or project_context.get('name') or '(unknown)'} "
            f"(id {board_data.get('activated_project_id', project_context.get('id', 'n/a'))})"
        )
    activation_block = (
        "\n\n**Work context now active**\n" + "\n".join(activation_lines)
        if activation_lines
        else ""
    )

    todos_block = ""
    todos = ticket.get("todos") or []
    if todos:
        lines = []
        for item in todos:
            mark = "[x]" if item.get("done") else "[ ]"
            lines.append(f"{mark} {item.get('text') or ''}")
        todos_block = "\n**Checklist / subtasks**\n" + "\n".join(lines)

    desc_note = ""
    if not desc and desc_raw and str(desc_raw).strip():
        desc_note = (
            "\n\n*(Jira returned a non-empty description in HTML/ADF that is not expanded "
            "to plain text here — open the issue URL above for the full body, images, "
            "and acceptance details.)*"
        )

    orchestrator_hint = ""
    if not is_local:
        orchestrator_hint = (
            "\n\n**Orchestrator instruction:** This ticket is shown on an **external** board "
            "(Jira/Trello). The context for this turn is **fully in this user message** unless "
            "you see a separate 'Local ticket id'. Do **not** call the ticket-board tool "
            "(`create_ticket` with action `discuss_ticket` or `get_ticket`) using the Jira key "
            "to reload the issue; there may be no local `KanbanTicket` row until the user uses "
            "**Copy to local board**. Answer from this message and the URL; suggest copying to "
            "the board only if they need send-to-project or a local ticket id."
        )

    return (
        "[Ticket Board — orchestrator engage this ticket]\n"
        "The user clicked **Send to Orchestrator** while staying on the ticket board. Treat this "
        "as the start of a live conversation about this exact ticket inside the already-active "
        "project/board context below.\n"
        "You have consumed this ticket for this turn: explain in plain spoken English what the "
        "ticket is about, which project it belongs to, what you think we should do next, and ask "
        "one focused question to move forward.\n"
        "Your first reply must be natural and TTS-friendly, not a markdown dump. Open as if you "
        "already read the ticket and are briefing the user on what we're doing with it.\n"
        "Use project/local-context tools only as needed to confirm folder state or recent project "
        "activity — the board and project are already activated for this handoff.\n"
        "Do not start CLI runs, edit the board, or create workflows unless the user asks.\n"
        "End with one concrete suggested next step and one focused question or offer to proceed. "
        "Do not ask 3-5 generic questions.\n\n"
        f"**Context** — Source: {source} · Board: {board_label or '(unknown)'} · {id_part}"
        f"{url_line}{meta_block}\n\n"
        f"**Title**\n{title}\n"
        f"{todos_block}\n"
        f"**Description**\n{desc or '(none)'}"
        f"{project_block}"
        f"{activation_block}"
        f"{desc_note}"
        f"{orchestrator_hint}"
    )


def build_orchestrator_messages(
    ticket: Mapping[str, Any],
    *,
    is_local: bool,
    board_label: str = "",
    source: str = "database",
    board_data: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, str]:
    """Return (display_brief, agent_context)."""
    return (
        build_display_brief(ticket),
        build_agent_context(
            ticket,
            is_local=is_local,
            board_label=board_label,
            source=source,
            board_data=board_data,
        ),
    )


def record_ticket_engagement_activity(
    chat_id: int,
    display_message: str,
    *,
    board_label: str = "",
) -> None:
    """Persist ticket handoff as compact chat activity (not a user message bubble)."""
    from distr.core.agent.tool_audit import record_tool_execution

    record_tool_execution(
        int(chat_id),
        "ticket_board",
        display_message,
        status="completed",
        instruction_hint=display_message,
        routing_path=board_label or "ticket board",
        chat_visible=False,
    )


def emit_ticket_engagement_memory_event(
    *,
    ticket: Mapping[str, Any],
    is_local: bool,
    board_id: Optional[int],
    project_id: Optional[int],
    display_message: str,
) -> None:
    """Record orchestration memory so Hermes knows a ticket handoff happened."""
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        title = _ticket_title(ticket)
        emit_orchestration_event(
            source="kanban",
            event_type="route_decided",
            status="running",
            ticket_id=int(ticket["id"]) if is_local and ticket.get("id") else None,
            board_id=int(board_id) if board_id else None,
            project_id=int(project_id) if project_id else None,
            summary=f'Ticket "{title}" sent to orchestrator from the board.',
            payload={
                "subtype": "ticket_engaged",
                "surface": "kanban",
                "display_message": display_message,
            },
        )
    except Exception:
        logger.debug("emit_ticket_engagement_memory_event failed", exc_info=True)


def send_ticket_engagement_to_agent(
    chat_id: int,
    display_message: str,
    agent_message: str,
    *,
    speak: bool = True,
    board_label: str = "",
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
) -> None:
    """Record activity in chat, then load the agent chat and process the full prompt in order."""
    display = (display_message or "").strip()
    agent_text = (agent_message or display_message or "").strip()
    if not display or not agent_text:
        raise ValueError("display_message and agent_message are required")

    record_ticket_engagement_activity(chat_id, display, board_label=board_label)

    from distr.core.signals import signal_manager

    # Single ordered handoff: load chat history, then process the orchestrator prompt.
    # Avoids racing web_load_chat_in_agent + web_send_to_agent (tool interruption).
    signal_manager.web_load_chat_and_process_requested.emit(
        int(chat_id),
        agent_text,
        bool(speak),
        True,
    )
