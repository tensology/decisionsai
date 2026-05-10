"""
Ticket Board Tool — create, list, and manage tickets on Ticket Boards.

Replaces the old CreateCursorTicketTool. Works with the database-backed
KanbanBoard / KanbanLane / KanbanTicket models and supports attaching files
(images, documents, etc.) that were received in the conversation thread
(e.g. from Telegram).
"""
import html
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from distr.core.agent.tool_voice_format import voice_then_reference
from distr.core.db.orm_compat import orm_get_by_id
from distr.core.integrations.whatsapp.paths import resolve_whatsapp_media_disk_path
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.agent.ticket_intent import (
    format_skill_recommendations_markdown,
    recommend_skills_for_ticket,
)

logger = logging.getLogger(__name__)


def _yaml_scalar(s: str) -> str:
    """YAML front-matter safe string (JSON double-quoted string is valid YAML 1.2)."""
    return json.dumps(s if s is not None else "", ensure_ascii=False)


def _plain_desc_for_ticket_export(raw: str) -> str:
    """Match web send-to-project: strip HTML-ish ticket descriptions for .tickets markdown."""
    if not raw:
        return "(no description)"
    if "<" not in raw:
        return (raw.strip() or "(no description)")
    t = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    t = re.sub(r"</p\s*>", "\n\n", t)
    t = re.sub(r"</(h[1-6]|div|li|tr)\s*>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return (html.unescape(t).strip() or "(no description)")


def _append_recommended_skills(title: str, description: str, text: str = "") -> tuple[str, list]:
    recommendations = recommend_skills_for_ticket(f"{title}\n\n{description}\n\n{text}")
    skills_markdown = format_skill_recommendations_markdown(recommendations)
    if skills_markdown and "## Recommended Skills" not in (description or ""):
        description = (description or "").strip()
        description = f"{description}\n\n{skills_markdown}".strip()
    return description, recommendations


def _read_yaml_frontmatter_field(path: str, field: str) -> Optional[str]:
    """Return a simple `field: value` from YAML front matter (first --- block only)."""
    try:
        with open(path, encoding="utf-8") as fh:
            in_fm = False
            for line in fh:
                stripped = line.strip()
                if stripped == "---":
                    if not in_fm:
                        in_fm = True
                        continue
                    break
                if not in_fm:
                    continue
                if stripped.startswith(f"{field}:"):
                    return stripped.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _find_existing_export_for_ticket(tickets_folder: str, kanban_ticket_id: int) -> Optional[str]:
    """Find an existing `.tickets/**/*.md` export for this Kanban ticket (updates in place)."""
    needle = f"source: kanban_ticket_{kanban_ticket_id}"
    matches: list[tuple[float, str]] = []
    try:
        for root, _, files in os.walk(tickets_folder):
            for name in files:
                if not name.endswith(".md"):
                    continue
                full = os.path.join(root, name)
                if not os.path.isfile(full):
                    continue
                try:
                    with open(full, encoding="utf-8") as fh:
                        head = fh.read(8000)
                    if needle not in head:
                        continue
                    matches.append((os.path.getmtime(full), full))
                except OSError:
                    continue
    except OSError:
        return None
    if not matches:
        return None
    matches.sort(key=lambda x: x[0])
    return matches[-1][1]


def _sanitize_ticket_title(title: str) -> str:
    """Keep Kanban titles short and avoid raw instruction dumps."""
    t = (title or "").strip()
    if not t:
        return t
    tl = t.lower()
    junk_markers = (
        "deliverables:", "requirements:", "also review", "reproduce steps",
        "## ", "```",
    )
    if any(m in tl for m in junk_markers) or "\n" in t or len(t) > 140:
        parts = re.split(r"[.!?]\s+", t)
        first = parts[0].strip() if parts else t
        if len(first) >= 8:
            t = first[:120]
        else:
            t = t.split("\n")[0].strip()[:120]
    meta_prefixes = ("investigate and fix ", "create a ticket", "user wants ", "the user ")
    for p in meta_prefixes:
        if tl.startswith(p):
            t = t[len(p) :].strip()
            tl = t.lower()
            break
    return t[:200]


class KanbanTicketInput(BaseModel):
    """Input schema for KanbanTicketTool."""
    text: str = Field(default="", description="Free-form instruction text (the tool parses board/lane/title from it)")
    action: str = Field(default="create_ticket", description="Action: list_boards, get_active_board, create_board, create_ticket, list_tickets, list_trello_tickets, list_jira_tickets, get_ticket, discuss_ticket, update_ticket, move_ticket, delete_ticket, attach_file, add_todo, toggle_todo, add_link, send_to_project, send_to_cli, update_external_ticket, move_external_ticket, comment_external_ticket, activate_board, checkin_overview, whatsapp_list_chats, whatsapp_list_messages, whatsapp_snapshot_to_ticket, whatsapp_send_message")
    board_name: str = Field(default="", description="Board name (fuzzy matched)")
    board_id: int = Field(default=0, description="Board ID (exact)")
    lane_name: str = Field(default="", description="Lane name (fuzzy matched, defaults to Backlog)")
    title: str = Field(default="", description="Ticket title")
    description: str = Field(default="", description="Ticket description")
    priority: str = Field(default="medium", description="Priority: low, medium, high, critical")
    ticket_id: int = Field(default=0, description="Ticket ID for get/update/move/delete/discuss actions")
    external_issue_key: str = Field(
        default="",
        description=(
            "Jira-style issue key (e.g. PROJ-42) for discuss_ticket when only the external key is known; "
            "matches a local board ticket's external_id. Do not use for keys that only exist on Jira when the user "
            "message already includes '[Ticket Board — discuss this ticket]' — context is already in-thread."
        ),
    )
    external_item_id: str = Field(default="", description="Remote Trello card ID or Jira issue key for external ticket update/move/comment actions")
    comment_text: str = Field(default="", description="Comment body for comment_external_ticket")
    file_path: str = Field(default="", description="File path for attach_file action")
    url: str = Field(default="", description="URL for add_link action")
    todo_text: str = Field(default="", description="Text for add_todo/toggle_todo action")
    linked_project_id: int = Field(default=0, description="Link ticket to a project by ID")
    linked_workflow_id: int = Field(default=0, description="Link ticket to a workflow by ID")
    send_to_cli: bool = Field(default=False, description="If True, send ticket directly to project CLI instead of running a workflow")
    jid_phone: str = Field(default="", description="WhatsApp chat phone/group key")
    jid: str = Field(default="", description="WhatsApp full JID")
    message_ids: List[int] = Field(default_factory=list, description="WhatsApp message IDs for read/snapshot actions")
    message_limit: int = Field(default=50, description="Limit for WhatsApp list actions")
    unprocessed_only: bool = Field(default=False, description="Filter only unprocessed WhatsApp messages")


class KanbanTicketTool(BaseTool):
    """Create and manage tickets on Ticket Boards.

    ACTIONS (pass as the 'action' parameter):
      list_boards        — list all boards
      get_active_board   — show which board is currently active/in use
      create_board       — create a new board (requires board_name)
      delete_board       — delete a board (requires board_id or board_name)
      list_lanes         — list lanes for a board (requires board_id or board_name)
      create_ticket      — create a ticket (requires board_name or board_id, plus title)
      list_tickets       — list tickets in a board or lane
      get_ticket         — get ticket details (requires ticket_id)
      discuss_ticket     — load ticket into context for Q&A (ticket_id, external_issue_key, or recent #id / issue key in text); use when user wants to talk through a ticket without sending to project yet
      update_ticket      — update a ticket (requires ticket_id)
      move_ticket        — move ticket to a different lane (requires ticket_id, lane_name)
      update_external_ticket  — update a remote Trello/Jira item (requires external_item_id or external_issue_key)
      move_external_ticket    — move a remote Trello/Jira item (requires external_item_id or external_issue_key and lane_name/status)
      comment_external_ticket — add a comment to a remote Trello/Jira item
      delete_ticket      — delete a ticket (requires ticket_id)
      attach_file        — attach a local file to a ticket (requires ticket_id, file_path)
      delete_file        — remove an attached file (requires ticket_id, file_path as filename)
      add_todo           — add a checklist item (requires ticket_id, text)
      toggle_todo        — toggle a checklist item done/undone (requires ticket_id, todo_text)
      delete_todo        — remove a checklist item (requires ticket_id, todo_text)
      add_link           — add a URL link (requires ticket_id, title, url)
      delete_link        — remove a link (requires ticket_id, url)
      send_to_project    — send ticket to linked project's .tickets folder (requires ticket_id)
      checkin_overview          — show orchestrator view of active board check-ins and workflow runs
      whatsapp_list_chats       — list WhatsApp chats available to the agent
      whatsapp_list_messages    — read latest WhatsApp messages for a chat
      whatsapp_snapshot_to_ticket — create a ticket snapshot from WhatsApp messages
      whatsapp_send_message     — send a WhatsApp message to a chat/group

    REQUIRED PARAMETERS:
      action  — one of the actions above
      text    — free-form instruction (the tool will parse board/lane/title from it)

    OPTIONAL PARAMETERS:
      board_name   — board name (fuzzy matched)
      board_id     — board ID (exact)
      lane_name    — lane name (fuzzy matched, defaults to first lane / "Backlog")
      title        — ticket title
      description  — ticket description
      priority     — low / medium / high / critical
      ticket_id    — ticket ID for get/update/attach/todo/link/discuss actions
      external_issue_key — Jira key for discuss_ticket when the local numeric id is unknown
      external_item_id — Trello card ID or Jira issue key for remote update/move/comment actions
      comment_text — comment body for remote comments
      file_path    — local file path for attach_file action
      url          — URL for add_link action
      todo_text    — text for add_todo action
      jid_phone    — WhatsApp phone/group key for WhatsApp actions
      jid          — explicit WhatsApp JID for WhatsApp send action
      message_ids  — message IDs used by WhatsApp snapshot action
      message_limit — fetch limit for WhatsApp list actions

    CONVERSATION CONTEXT:
      When creating a ticket, the tool automatically gathers the recent conversation
      thread (including references to files/images received from Telegram) and uses
      it to build a rich ticket description. If images or documents were mentioned
      or received in the thread, they are attached to the ticket automatically.
    """

    name: str = "create_ticket"
    args_schema: type[BaseModel] = KanbanTicketInput
    description: str = (
        "Full CRUD for Ticket boards and tickets. "
        "Use action='create_ticket' with board_name and title to create a ticket. "
        "When the user asks for a Trello card or Jira ticket, still use action='create_ticket'; "
        "the tool will create it on the matching remote board instead of the local Kanban database. "
        "Use action='list_boards' to see available boards (local, Trello, and Jira). "
        "Use action='get_active_board' to see the active local board in use. "
        "Use action='list_trello_tickets' or action='list_jira_tickets' with board_name to read external board tickets. "
        "Use action='discuss_ticket' when the user wants to talk through a **local** ticket (numeric id or row copied to "
        "the board with external_id). Skip discuss_ticket with external_issue_key if the user message already starts "
        "with '[Ticket Board — discuss this ticket]' from the UI — that message is the source of truth for external "
        "Jira/Trello cards that were not copied to a local row. "
        "Also use discuss_ticket when the user says 'let's talk about this ticket' without that banner — pass ticket_id, "
        "external_issue_key, or text with #id / Jira key. "
        "Use action='create_board' with board_name to create a new board. "
        "Use action='activate_board' with board_name to set a board as the active/default board. "
        "Use action='delete_ticket' with ticket_id to delete a ticket. "
        "Use action='move_ticket' with ticket_id and lane_name to move a ticket. "
        "Use action='update_external_ticket', 'move_external_ticket', or 'comment_external_ticket' for follow-up changes "
        "to Trello/Jira items; pass external_item_id or external_issue_key plus provider context in text/board_name. "
        "Use action='attach_file' with ticket_id and file_path to attach files. "
        "Use action='send_to_project' with ticket_id to send ticket to the linked project folder. "
        "Use action='send_to_cli' with ticket_id to send ticket to pi coding agent for execution. "
        "Use action='checkin_overview' to get active board check-ins and workflow run phases. "
        "Use action='whatsapp_list_chats' to list WhatsApp chats. "
        "Use action='whatsapp_list_messages' with jid_phone to read recent messages. "
        "Use action='whatsapp_snapshot_to_ticket' with board_name/board_id and jid_phone or message_ids to snapshot into a ticket. "
        "Use action='whatsapp_send_message' with jid or jid_phone plus text to reply in WhatsApp. "
        "The tool automatically gathers conversation context and attaches any "
        "images/documents from the chat thread to the ticket. "
        "IMPORTANT: When user says 'create a ticket', call this tool with "
        "action='create_ticket'. Pass the user's full instruction as 'text'. "
        "BOARD SELECTION: When creating a ticket, if there are multiple boards "
        "and the user hasn't specified one, call action='list_boards' first and "
        "ASK the user which board to use. If there is only one board, use it "
        "automatically but ALWAYS tell the user which board the ticket was added to. "
        "ACTIVE BOARD: When user says 'I'm working on board X' or 'use board X', "
        "call action='activate_board' with board_name. After that, all ticket "
        "commands default to this board without needing to specify it again. "
        "LINKING: You can link a ticket to a project via linked_project_id or "
        "to a workflow via linked_workflow_id when creating or updating a ticket."
    )

    chat_manager: Any = Field(default=None, exclude=True)
    llm_service: Any = Field(default=None, exclude=True)
    event_queue: Any = Field(default=None, exclude=True)

    # Track last created ticket for follow-up commands
    _last_ticket_id: Optional[int] = None
    _last_board_id: Optional[int] = None

    def __init__(self, chat_manager=None, llm_service=None, event_queue=None, **kwargs):
        super().__init__(**kwargs)
        self.chat_manager = chat_manager
        self.llm_service = llm_service
        self.event_queue = event_queue

    def get_triggers(self) -> list[str]:
        return [
            "create a ticket", "create ticket", "make a ticket",
            "add a ticket", "new ticket", "add ticket",
            "create a ticket board ticket", "ticket board ticket",
            "add to board", "add to the board",
            "list boards", "show boards", "my boards",
            "active board", "current board", "board in use", "in use board",
            "which board is active", "what is the active board",
            "list tickets", "show tickets",
            "let's talk about this ticket", "lets talk about this ticket",
            "talk about this ticket", "discuss this ticket", "load this ticket",
            "let's discuss this ticket", "help me think through this ticket",
        ]

    # ── DB helpers ────────────────────────────────────────────────────────

    def _get_session(self):
        from distr.core.db import get_session
        return get_session()

    def _source_chat_id_for_new_ticket(self) -> Optional[int]:
        """Chat thread linked for lane-move notifications (board agent / Kanban UI)."""
        if not self.chat_manager:
            return None
        try:
            cid = self.chat_manager.get_current_chat()
            return int(cid) if cid else None
        except Exception:
            return None

    def _all_boards(self) -> List[Dict]:
        from distr.core.db.kanban import KanbanBoard
        with self._get_session() as s:
            boards = (
                s.query(KanbanBoard)
                .filter(
                    KanbanBoard.source == "database",
                    (KanbanBoard.archived == False) | (KanbanBoard.archived == None),
                )
                .order_by(KanbanBoard.name)
                .all()
            )
            return [{"id": b.id, "name": b.name, "description": b.description or "",
                     "default_project_id": b.default_project_id} for b in boards]

    def _find_board(self, board_id: Optional[int] = None, board_name: Optional[str] = None) -> Optional[Dict]:
        """Find a board by ID or fuzzy name match. Falls back to the in_use board if nothing specified."""
        from distr.core.db.kanban import KanbanBoard
        with self._get_session() as s:
            if board_id:
                b = orm_get_by_id(s, KanbanBoard, board_id)
                if b:
                    return {"id": b.id, "name": b.name, "description": b.description or "",
                            "default_project_id": b.default_project_id}
                return None
            if board_name:
                name_lower = board_name.strip().lower()
                # Strip spaces/punctuation for loose comparison
                name_stripped = re.sub(r'[^a-z0-9]', '', name_lower)
                boards = (
                    s.query(KanbanBoard)
                    .filter(
                        KanbanBoard.source == "database",
                        (KanbanBoard.archived == False) | (KanbanBoard.archived == None),
                    )
                    .all()
                )
                # Exact match first
                for b in boards:
                    if b.name.lower() == name_lower:
                        return {"id": b.id, "name": b.name, "description": b.description or "",
                                "default_project_id": b.default_project_id}
                # Substring match
                for b in boards:
                    if name_lower in b.name.lower() or b.name.lower() in name_lower:
                        return {"id": b.id, "name": b.name, "description": b.description or "",
                                "default_project_id": b.default_project_id}
                # Stripped match (handles STT: "mary pack" vs "merrypak")
                for b in boards:
                    board_stripped = re.sub(r'[^a-z0-9]', '', b.name.lower())
                    if name_stripped in board_stripped or board_stripped in name_stripped:
                        return {"id": b.id, "name": b.name, "description": b.description or "",
                                "default_project_id": b.default_project_id}
                # Word overlap — if most words match, it's probably the right board
                name_words = set(name_lower.split())
                best_overlap = 0
                best_board = None
                for b in boards:
                    board_words = set(b.name.lower().split())
                    overlap = len(name_words & board_words)
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_board = b
                if best_board and best_overlap > 0:
                    return {"id": best_board.id, "name": best_board.name, "description": best_board.description or "",
                            "default_project_id": best_board.default_project_id}
                # Single board? Use it.
                if len(boards) == 1:
                    b = boards[0]
                    return {"id": b.id, "name": b.name, "description": b.description or "",
                            "default_project_id": b.default_project_id}
            else:
                active_board, _was_recovered = self._get_active_board(auto_recover=True)
                if active_board:
                    return active_board
        return None

    def _get_active_board(self, auto_recover: bool = False) -> tuple[Optional[Dict], bool]:
        """Return active board and whether it was auto-recovered."""
        from distr.core.db.kanban import KanbanBoard
        with self._get_session() as s:
            active = (
                s.query(KanbanBoard)
                .filter(
                    KanbanBoard.in_use == True,
                    KanbanBoard.source == "database",
                    (KanbanBoard.archived == False) | (KanbanBoard.archived == None),
                )
                .order_by(KanbanBoard.modified_date.desc(), KanbanBoard.id.desc())
                .first()
            )
            if active:
                return ({
                    "id": active.id,
                    "name": active.name,
                    "description": active.description or "",
                    "default_project_id": active.default_project_id,
                }, False)

            boards = (
                s.query(KanbanBoard)
                .filter(
                    KanbanBoard.source == "database",
                    (KanbanBoard.archived == False) | (KanbanBoard.archived == None),
                )
                .order_by(KanbanBoard.modified_date.desc(), KanbanBoard.id.desc())
                .all()
            )
            if not boards:
                return (None, False)

            # Keep behavior deterministic for callers even when no active board exists.
            candidate = boards[0]
            if not auto_recover:
                return ({
                    "id": candidate.id,
                    "name": candidate.name,
                    "description": candidate.description or "",
                    "default_project_id": candidate.default_project_id,
                }, False)

            # Self-heal: enforce one active board so "in use" queries remain consistent.
            s.query(KanbanBoard).filter(KanbanBoard.in_use == True).update({"in_use": False})
            candidate.in_use = True
            s.commit()
            return ({
                "id": candidate.id,
                "name": candidate.name,
                "description": candidate.description or "",
                "default_project_id": candidate.default_project_id,
            }, True)

    def _get_lanes(self, board_id: int) -> List[Dict]:
        from distr.core.db.kanban import KanbanLane
        with self._get_session() as s:
            lanes = s.query(KanbanLane).filter_by(board_id=board_id).order_by(KanbanLane.position).all()
            return [{"id": l.id, "name": l.name, "position": l.position} for l in lanes]

    def _find_lane(self, board_id: int, lane_name: Optional[str] = None) -> Optional[Dict]:
        lanes = self._get_lanes(board_id)
        if not lanes:
            return None
        if not lane_name:
            # Default to first lane (usually "Backlog")
            return lanes[0]
        name_lower = lane_name.strip().lower()
        for l in lanes:
            if l["name"].lower() == name_lower:
                return l
        for l in lanes:
            if name_lower in l["name"].lower() or l["name"].lower() in name_lower:
                return l
        return lanes[0]  # fallback to first

    # ── Conversation context ─────────────────────────────────────────────

    def _get_conversation_context(self, max_messages: int = 30) -> tuple[str, List[str]]:
        """Return (conversation_text, list_of_file_paths_mentioned).

        Scans recent messages for file paths (images/docs saved from Telegram)
        and builds a text summary of the conversation thread.
        """
        file_paths: List[str] = []
        conversation_lines: List[str] = []

        if not self.chat_manager:
            return "", file_paths

        try:
            current_chat_id = self.chat_manager.get_current_chat()
            if not current_chat_id:
                return "", file_paths

            history = self.chat_manager.get_chat_history(current_chat_id)
            recent = [m for m in history if m.get("role") in ("user", "assistant")][-max_messages:]

            # Regex to find file paths in messages
            path_re = re.compile(r'(?:/[^\s\]]+|~/[^\s\]]+)')
            telegram_file_re = re.compile(r'\[Telegram \w+ saved to ([^\]]+)\]')

            for msg in recent:
                role = msg.get("role", "unknown")
                content = (msg.get("content") or "").strip()
                if not content:
                    continue

                prefix = "User" if role == "user" else "Assistant"
                conversation_lines.append(f"{prefix}: {content}")

                # Extract file paths
                for m in telegram_file_re.finditer(content):
                    fp = m.group(1).strip()
                    if os.path.exists(fp):
                        file_paths.append(fp)
                for m in path_re.finditer(content):
                    fp = os.path.expanduser(m.group(0).strip())
                    if os.path.isfile(fp) and fp not in file_paths:
                        # Skip .tickets/ markdown files — those are project ticket
                        # artifacts, not user-provided attachments.
                        if '/.tickets/' in fp and fp.endswith('.md'):
                            continue
                        file_paths.append(fp)

        except Exception as e:
            logger.error("Error gathering conversation context: %s", e, exc_info=True)

        return "\n".join(conversation_lines), file_paths

    # ── File attachment ───────────────────────────────────────────────────

    def _attach_file_to_ticket(self, ticket_id: int, file_path: str) -> Optional[str]:
        """Copy file into kanban_uploads and create a KanbanTicketFile record."""
        from distr.core.db.kanban import KanbanTicketFile
        from distr.core.paths import DB_DIR

        if not os.path.isfile(file_path):
            return None

        upload_dir = os.path.join(DB_DIR, "kanban_uploads", str(ticket_id))
        os.makedirs(upload_dir, exist_ok=True)

        safe_name = os.path.basename(file_path)
        dest = os.path.join(upload_dir, safe_name)

        # Avoid overwriting — add timestamp if collision
        if os.path.exists(dest):
            stem, ext = os.path.splitext(safe_name)
            ts = datetime.now().strftime("%H%M%S")
            safe_name = f"{stem}_{ts}{ext}"
            dest = os.path.join(upload_dir, safe_name)

        shutil.copy2(file_path, dest)

        with self._get_session() as s:
            rec = KanbanTicketFile(ticket_id=ticket_id, filename=safe_name, file_path=dest)
            s.add(rec)
            s.flush()
            return safe_name

    # ── LLM-based ticket summarisation ────────────────────────────────────

    def _summarise_for_ticket(self, raw_text: str) -> Dict[str, str]:
        """Use the LLM to extract ticket(s) from raw text.

        If the text contains multiple distinct tasks/action items, returns
        multiple tickets. Otherwise returns a single ticket.

        Returns {"title": ..., "description": ...} for single mode,
        or a list of those dicts for bulk mode.
        """
        # Detect bulk mode
        is_bulk = self._detect_bulk_mode(raw_text)

        if is_bulk and self.llm_service and hasattr(self.llm_service, '_model_name'):
            try:
                prompt = (
                    "You are a project manager creating work tickets from meeting notes or a task list.\n"
                    "Extract ALL distinct tasks, action items, or work items from the text below.\n\n"
                    "For each item, provide:\n"
                    "- title: concise ticket title (max 10 words)\n"
                    "- description: actionable description of what needs to be done\n"
                    "- priority: low, medium, high, or critical\n\n"
                    "RULES:\n"
                    "- Each ticket should be a SEPARATE, independent work item\n"
                    "- Write descriptions as actionable tasks, not summaries\n"
                    "- Do NOT combine multiple tasks into one ticket\n"
                    "- Do NOT reference the meeting or conversation\n\n"
                    "Reply with a JSON array only (no markdown fences):\n"
                    '[{"title": "...", "description": "...", "priority": "medium"}, ...]\n\n'
                    f"Text:\n{raw_text[:4000]}"
                )
                result = self._call_llm_sync(prompt)
                if result:
                    import json as _json
                    # Strip markdown fences
                    cleaned = result.strip()
                    if cleaned.startswith("```"):
                        cleaned = re.sub(r'^```\w*\s*', '', cleaned)
                        cleaned = re.sub(r'\s*```\s*$', '', cleaned)
                    parsed = _json.loads(cleaned)
                    if isinstance(parsed, list) and len(parsed) > 1:
                        return parsed  # Return list for bulk mode
            except Exception as e:
                logger.warning("Bulk ticket extraction failed, falling back to single: %s", e)

        # Single ticket mode (original behavior)
        if self.llm_service and hasattr(self.llm_service, '_model_name'):
            try:
                prompt = (
                    "You are a project manager creating a work ticket. "
                    "Given the following conversation, extract:\n"
                    "1. A concise ticket title (max 10 words) — describe the ISSUE or TASK, not the act of creating a ticket.\n"
                    "2. A clear, actionable description written as a task — what needs to be done, investigated, or fixed.\n\n"
                    "RULES:\n"
                    "- Write the description as an actionable work item (e.g. 'Investigate why Iridium is rejecting emails...').\n"
                    "- Do NOT write 'Create a ticket about...' or 'The user wants...' — write the actual task.\n"
                    "- Do NOT reference the conversation or the user — just describe the work.\n"
                    "- Do NOT paste the user's full instruction into the title — summarize into a short headline.\n"
                    "- Do NOT put markdown headings, bullet lists, or section labels like Deliverables or Requirements in the title.\n"
                    "- If files or images are mentioned as relevant to the issue, note them.\n"
                    "- Do NOT reference .tickets/*.md files — those are internal artifacts.\n\n"
                    "Reply ONLY in this exact format:\n"
                    "Title: <title>\n"
                    "Description: <description>\n\n"
                    f"Conversation:\n{raw_text[:3000]}"
                )
                result = self._call_llm_sync(prompt)
                if result:
                    title_match = re.search(r'^Title:\s*(.+)', result, re.MULTILINE | re.IGNORECASE)
                    desc_match = re.search(r'^Description:\s*(.+)', result, re.MULTILINE | re.IGNORECASE | re.DOTALL)
                    title = title_match.group(1).strip() if title_match else ""
                    desc = desc_match.group(1).strip() if desc_match else result
                    if title:
                        title = _sanitize_ticket_title(title)
                        return {"title": title, "description": desc}
            except Exception as e:
                logger.warning("LLM summarisation failed, using fallback: %s", e)

        # Fallback
        lines = [l.strip() for l in raw_text.strip().split("\n") if l.strip()]
        title = _sanitize_ticket_title(lines[0][:80] if lines else "New Ticket")
        desc = "\n".join(lines[1:]) if len(lines) > 1 else raw_text[:500]
        return {"title": title, "description": desc}

    def _detect_bulk_mode(self, text: str) -> bool:
        """Detect if text contains multiple distinct tasks/items."""
        if len(text) < 200:
            return False
        # Count bullet points, numbered items, action items
        lines = text.strip().split("\n")
        bullet_count = sum(1 for l in lines if re.match(r'^\s*[-•*]\s+', l))
        numbered_count = sum(1 for l in lines if re.match(r'^\s*\d+[.)]\s+', l))
        action_count = sum(1 for l in lines if re.match(r'^\s*(TODO|ACTION|TASK|ITEM)\s*:', l, re.IGNORECASE))
        return (bullet_count >= 3) or (numbered_count >= 3) or (action_count >= 2) or (len(text) > 1000)

    def _call_llm_sync(self, prompt: str) -> Optional[str]:
        """Synchronous LLM call for ticket summarisation."""
        try:
            import ollama
            model = getattr(self.llm_service, '_model_name', 'qwen3:8b')
            resp = ollama.chat(model=model, messages=[
                {"role": "system", "content": "You are a concise project manager assistant."},
                {"role": "user", "content": prompt},
            ])
            return resp.get("message", {}).get("content", "")
        except Exception:
            pass
        try:
            from openai import OpenAI
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            api_key = settings.get("openai_key", "")
            if api_key:
                client = OpenAI(api_key=api_key)
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a concise project manager assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=500,
                )
                return resp.choices[0].message.content
        except Exception:
            pass
        return None

    def _load_connected_accounts(self) -> list[dict]:
        """Return configured third-party accounts in a consistent shape."""
        import json as _json
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        raw = settings.get("connected_accounts") or "[]"
        if isinstance(raw, str):
            try:
                accounts = _json.loads(raw)
            except Exception:
                return []
        else:
            accounts = raw if isinstance(raw, list) else []
        return [acct for acct in accounts if isinstance(acct, dict)]

    def _account_for_provider(self, provider: str) -> Optional[dict]:
        provider = (provider or "").lower().strip()
        for acct in self._load_connected_accounts():
            if (acct.get("provider") or "").lower() != provider:
                continue
            if acct.get("is_valid") is False:
                continue
            if provider == "trello" and acct.get("api_key") and acct.get("api_token"):
                return acct
            if provider == "jira" and acct.get("email") and acct.get("api_token"):
                return acct
        return None

    def _remote_provider_from_request(self, text: str, board_name: str = "") -> Optional[str]:
        haystack = f"{text or ''} {board_name or ''}".lower()
        if "trello" in haystack:
            return "trello"
        if "jira" in haystack:
            return "jira"
        return None

    def _external_item_id_from_request(self, provider: str, external_item_id: str = "", external_issue_key: str = "", text: str = "") -> str:
        explicit = (external_item_id or external_issue_key or "").strip()
        if explicit:
            return explicit
        if provider == "jira":
            return self._parse_jira_key_from_text(text or "") or ""
        if provider == "trello":
            patterns = (
                r"\b(?:card|trello\s+card)\s+(?:id\s+)?([a-f0-9]{8,32})\b",
                r"\btrello\.com/c/([A-Za-z0-9]+)\b",
                r"\b([a-f0-9]{24})\b",
            )
            for pattern in patterns:
                match = re.search(pattern, text or "", re.IGNORECASE)
                if match:
                    return match.group(1)
        return ""

    def _find_external_board(self, provider: str, board_id: Any = None, board_name: str = "", text: str = "") -> tuple[Optional[dict], list[dict]]:
        boards = self._fetch_external_boards().get(provider, [])
        if not boards:
            return None, []

        wanted_id = str(board_id or "").strip()
        if wanted_id:
            for board in boards:
                if str(board.get("id") or "") == wanted_id:
                    return board, boards

        candidates = [board_name or ""]
        cleaned_text = (text or "").lower()
        for marker in (provider, "board", "card", "ticket", "issue"):
            cleaned_text = cleaned_text.replace(marker, " ")
        candidates.append(cleaned_text)

        for candidate in candidates:
            candidate = re.sub(r"\s+", " ", candidate.lower()).strip()
            if not candidate:
                continue
            for board in boards:
                external_name = (board.get("name") or "").lower()
                if external_name and (external_name in candidate or candidate in external_name):
                    return board, boards

        if len(boards) == 1:
            return boards[0], boards
        return None, boards

    def _jira_domain(self, acct: dict) -> str:
        domain = (acct.get("domain") or "").strip()
        if domain:
            return domain.replace("https://", "").replace("http://", "").strip("/")
        server_url = (acct.get("server_url") or "").strip().rstrip("/")
        if server_url:
            return server_url.replace("https://", "").replace("http://", "").split("/")[0]
        return ""

    def _jira_project_key_for_board(self, acct: dict, board: dict) -> str:
        key = (board.get("project_key") or board.get("projectKey") or "").strip()
        if key:
            return key
        domain = self._jira_domain(acct)
        if not domain or not board.get("id"):
            return ""
        try:
            import requests
            from requests.auth import HTTPBasicAuth

            resp = requests.get(
                f"https://{domain}/rest/agile/1.0/board/{board['id']}/configuration",
                auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                location = (resp.json() or {}).get("location") or {}
                return (location.get("projectKey") or "").strip()
        except Exception:
            logger.debug("Could not fetch Jira board configuration", exc_info=True)
        return ""

    def _create_external_ticket(
        self,
        provider: str,
        board: dict,
        text: str = "",
        lane_name: str = "",
        title: str = "",
        description: str = "",
        priority: str = "medium",
    ) -> str:
        provider = provider.lower().strip()
        acct = self._account_for_provider(provider)
        if not acct:
            return voice_then_reference(
                f"Connect a valid {provider.title()} account before I create that remotely.",
                f"No valid {provider.title()} account is configured. Open Settings → Advanced → {provider.title()} and validate the account.",
            )

        if not title and not description:
            summary = self._summarise_for_ticket(text)
            if isinstance(summary, list):
                summary = summary[0] if summary else {}
            title = (summary or {}).get("title") or ""
            description = (summary or {}).get("description") or ""
        elif not title:
            title = description[:80]
        elif not description:
            description = text or title

        title = _sanitize_ticket_title(title or "") or "New Ticket"
        description = (description or text or title).strip()
        description, skill_recommendations = _append_recommended_skills(title, description, text)

        if provider == "trello":
            from distr.core.integrations.trello_api import TrelloAPI

            api = TrelloAPI(acct["api_key"], acct["api_token"])
            lists = api.get_lists(str(board["id"]))
            if not lists:
                return voice_then_reference(
                    "I found that Trello board, but it has no writable lists.",
                    f"Trello board '{board.get('name')}' has no open lists.",
                )
            target_list = None
            if lane_name:
                lane_lower = lane_name.lower().strip()
                for item in lists:
                    list_name = (item.get("name") or "").lower()
                    if lane_lower == list_name or lane_lower in list_name or list_name in lane_lower:
                        target_list = item
                        break
            target_list = target_list or lists[0]
            created = api.create_card(str(target_list["id"]), title, description)
            if not created:
                return voice_then_reference(
                    "Trello did not create the card.",
                    f"Trello create failed for board '{board.get('name')}', list '{target_list.get('name')}'.",
                )
            card_id = created.get("id") or ""
            card_url = created.get("url") or ""
            ref = (
                f"Created Trello card '{created.get('name') or title}' on board '{board.get('name')}', "
                f"list '{target_list.get('name')}' (ID {card_id})"
            )
            if card_url:
                ref += f"\nURL: {card_url}"
            if skill_recommendations:
                ref += "\nRecommended skills: " + ", ".join(rec.name for rec in skill_recommendations)
            return voice_then_reference(f"I created the Trello card {title}.", ref)

        if provider == "jira":
            import requests
            from requests.auth import HTTPBasicAuth

            domain = self._jira_domain(acct)
            project_key = self._jira_project_key_for_board(acct, board)
            if not domain or not project_key:
                return voice_then_reference(
                    "I found Jira, but I could not determine the project key for that board.",
                    f"Missing Jira domain or project key for board '{board.get('name')}' (ID {board.get('id')}).",
                )
            payload = {
                "fields": {
                    "project": {"key": project_key},
                    "summary": title,
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": description or title}],
                            }
                        ],
                    },
                    "issuetype": {"name": "Task"},
                }
            }
            resp = requests.post(
                f"https://{domain}/rest/api/3/issue",
                auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json=payload,
                timeout=10,
            )
            if resp.status_code not in (200, 201):
                return voice_then_reference(
                    "Jira did not create the issue.",
                    f"Jira create failed with HTTP {resp.status_code}: {resp.text[:500]}",
                )
            data = resp.json() or {}
            issue_key = data.get("key") or data.get("id") or ""
            issue_url = f"https://{domain}/browse/{issue_key}" if issue_key else ""
            ref = f"Created Jira issue '{title}' on board '{board.get('name')}' (Project {project_key}, Key {issue_key})"
            if issue_url:
                ref += f"\nURL: {issue_url}"
            if skill_recommendations:
                ref += "\nRecommended skills: " + ", ".join(rec.name for rec in skill_recommendations)
            return voice_then_reference(f"I created the Jira issue {issue_key or title}.", ref)

        return voice_then_reference(
            "I do not know how to create tickets for that remote provider yet.",
            f"Unsupported external ticket provider: {provider}",
        )

    def _trello_list_for_lane(self, board: dict, lane_name: str) -> tuple[Optional[dict], list[dict]]:
        from distr.core.integrations.trello_api import TrelloAPI

        acct = self._account_for_provider("trello")
        if not acct:
            return None, []
        api = TrelloAPI(acct["api_key"], acct["api_token"])
        lists = api.get_lists(str(board["id"]))
        if not lists:
            return None, []
        lane_lower = (lane_name or "").lower().strip()
        if lane_lower:
            for item in lists:
                list_name = (item.get("name") or "").lower()
                if lane_lower == list_name or lane_lower in list_name or list_name in lane_lower:
                    return item, lists
        return lists[0], lists

    def _jira_transition_id_for_status(self, acct: dict, issue_key: str, status_name: str) -> tuple[str, list[str]]:
        import requests
        from requests.auth import HTTPBasicAuth

        domain = self._jira_domain(acct)
        if not domain:
            return "", []
        resp = requests.get(
            f"https://{domain}/rest/api/3/issue/{issue_key}/transitions",
            auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code != 200:
            return "", []
        transitions = (resp.json() or {}).get("transitions") or []
        wanted = (status_name or "").lower().strip()
        names = [(item.get("name") or "").strip() for item in transitions]
        for item in transitions:
            name = (item.get("name") or "").lower().strip()
            if wanted and (wanted == name or wanted in name or name in wanted):
                return str(item.get("id") or ""), names
        return "", names

    def _action_external_ticket(
        self,
        action: str,
        text: str = "",
        board_name: str = "",
        board_id: Any = None,
        lane_name: str = "",
        title: str = "",
        description: str = "",
        external_item_id: str = "",
        external_issue_key: str = "",
        comment_text: str = "",
    ) -> str:
        provider = self._remote_provider_from_request(text, board_name)
        if not provider:
            return voice_then_reference(
                "Tell me whether this is for Trello or Jira.",
                "Remote ticket action requires provider context: include Trello or Jira in text/board_name.",
            )

        item_id = self._external_item_id_from_request(
            provider,
            external_item_id=external_item_id,
            external_issue_key=external_issue_key,
            text=text,
        )
        if not item_id:
            return voice_then_reference(
                f"I need the {provider.title()} item id before I can change it.",
                f"Missing remote item id. Pass external_item_id for Trello or external_issue_key for Jira.",
            )

        acct = self._account_for_provider(provider)
        if not acct:
            return voice_then_reference(
                f"Connect a valid {provider.title()} account before I change that remotely.",
                f"No valid {provider.title()} account is configured.",
            )

        if provider == "trello":
            from distr.core.integrations.trello_api import TrelloAPI

            api = TrelloAPI(acct["api_key"], acct["api_token"])
            if action == "update_external_ticket":
                updated = api.update_card(item_id, name=title or None, desc=description or None)
                if not updated:
                    return voice_then_reference(
                        "Trello did not update the card.",
                        f"Trello update failed for card {item_id}.",
                    )
                ref = f"Updated Trello card {item_id}"
                if updated.get("url"):
                    ref += f"\nURL: {updated['url']}"
                return voice_then_reference("I updated that Trello card.", ref)

            if action == "move_external_ticket":
                external_board, external_boards = self._find_external_board(provider, board_id=board_id, board_name=board_name, text=text)
                if not external_board:
                    if external_boards:
                        names = ", ".join(f"'{b.get('name')}' (ID {b.get('id')})" for b in external_boards[:8])
                        return voice_then_reference("Tell me which Trello board that card is on.", f"Available Trello boards: {names}")
                    return voice_then_reference("I could not find connected Trello boards.", "No Trello boards are available.")
                target_list, lists = self._trello_list_for_lane(external_board, lane_name)
                if not target_list:
                    return voice_then_reference(
                        "I could not find that Trello list.",
                        f"Available lists: {', '.join((item.get('name') or '') for item in lists) or '(none)'}",
                    )
                moved = api.move_card(item_id, str(target_list["id"]))
                if not moved:
                    return voice_then_reference("Trello did not move the card.", f"Trello move failed for card {item_id}.")
                return voice_then_reference(
                    f"I moved that Trello card to {target_list.get('name')}.",
                    f"Moved Trello card {item_id} to list '{target_list.get('name')}' on board '{external_board.get('name')}'.",
                )

            comment = (comment_text or description or text or "").strip()
            if not comment:
                return voice_then_reference("Give me the comment text to add.", "Missing comment_text.")
            created = api.add_comment_to_card(item_id, comment)
            if not created:
                return voice_then_reference("Trello did not add the comment.", f"Trello comment failed for card {item_id}.")
            return voice_then_reference("I added the comment to that Trello card.", f"Commented on Trello card {item_id}.")

        if provider == "jira":
            import requests
            from requests.auth import HTTPBasicAuth

            domain = self._jira_domain(acct)
            if not domain:
                return voice_then_reference("I could not find the Jira domain.", "Jira account is missing domain/server_url.")

            if action == "update_external_ticket":
                fields: dict[str, Any] = {}
                if title:
                    fields["summary"] = title
                if description:
                    fields["description"] = {
                        "type": "doc",
                        "version": 1,
                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
                    }
                if not fields:
                    return voice_then_reference("Tell me what to update on the Jira issue.", "No Jira update fields were provided.")
                resp = requests.put(
                    f"https://{domain}/rest/api/3/issue/{item_id}",
                    auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json={"fields": fields},
                    timeout=10,
                )
                if resp.status_code not in (200, 204):
                    return voice_then_reference("Jira did not update the issue.", f"Jira update failed with HTTP {resp.status_code}: {resp.text[:500]}")
                return voice_then_reference("I updated that Jira issue.", f"Updated Jira issue {item_id}\nURL: https://{domain}/browse/{item_id}")

            if action == "move_external_ticket":
                transition_id, transition_names = self._jira_transition_id_for_status(acct, item_id, lane_name)
                if not transition_id:
                    return voice_then_reference(
                        "I could not find a matching Jira transition.",
                        f"Available Jira transitions for {item_id}: {', '.join(transition_names) or '(none)'}",
                    )
                resp = requests.post(
                    f"https://{domain}/rest/api/3/issue/{item_id}/transitions",
                    auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json={"transition": {"id": transition_id}},
                    timeout=10,
                )
                if resp.status_code not in (200, 204):
                    return voice_then_reference("Jira did not transition the issue.", f"Jira transition failed with HTTP {resp.status_code}: {resp.text[:500]}")
                return voice_then_reference(f"I moved that Jira issue to {lane_name}.", f"Transitioned Jira issue {item_id} using transition {transition_id}.")

            comment = (comment_text or description or "").strip()
            if not comment:
                return voice_then_reference("Give me the comment text to add.", "Missing comment_text.")
            payload = {
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment}]}],
                }
            }
            resp = requests.post(
                f"https://{domain}/rest/api/3/issue/{item_id}/comment",
                auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json=payload,
                timeout=10,
            )
            if resp.status_code not in (200, 201, 204):
                return voice_then_reference("Jira did not add the comment.", f"Jira comment failed with HTTP {resp.status_code}: {resp.text[:500]}")
            return voice_then_reference("I added the comment to that Jira issue.", f"Commented on Jira issue {item_id}.")

        return voice_then_reference("I do not know that remote provider.", f"Unsupported external ticket provider: {provider}")

    # ── Main dispatch ─────────────────────────────────────────────────────

    def _run(
        self,
        text: str = "",
        action: str = "create_ticket",
        board_name: str = "",
        board_id: int = 0,
        lane_name: str = "",
        title: str = "",
        description: str = "",
        priority: str = "medium",
        ticket_id: int = 0,
        file_path: str = "",
        url: str = "",
        todo_text: str = "",
        linked_project_id: int = 0,
        linked_workflow_id: int = 0,
        send_to_cli: bool = False,
        jid_phone: str = "",
        jid: str = "",
        message_ids: Optional[List[int]] = None,
        message_limit: int = 50,
        unprocessed_only: bool = False,
        external_issue_key: str = "",
        external_item_id: str = "",
        comment_text: str = "",
        **kwargs,
    ) -> str:
        try:
            action = (action or "create_ticket").strip().lower().replace(" ", "_")
            message_ids = message_ids or []
            logger.info("KanbanTicketTool: action=%s board_name=%s board_id=%s title=%s",
                        action, board_name, board_id, title[:50] if title else "")

            # If the model routes to list_boards for an active-board question,
            # force a concise active-board response instead of dumping all boards.
            text_norm = (text or "").strip().lower()
            if action == "list_boards" and any(
                phrase in text_norm
                for phrase in (
                    "which board is active",
                    "what is the active board",
                    "what's the active board",
                    "active board",
                    "current board",
                    "board in use",
                    "in use board",
                )
            ):
                return self._action_get_active_board()

            # Natural phrasing: user wants to explore a ticket in chat without a formal action name.
            if action == "create_ticket" and any(
                phrase in text_norm
                for phrase in (
                    "let's talk about this ticket",
                    "lets talk about this ticket",
                    "talk about this ticket",
                    "discuss this ticket",
                    "let's discuss this ticket",
                    "lets discuss this ticket",
                    "load this ticket",
                    "open this ticket",
                    "think through this ticket",
                    "help me with this ticket",
                )
            ):
                action = "discuss_ticket"

            if action == "list_boards":
                return self._action_list_boards()
            elif action in ("get_active_board", "active_board", "which_board_is_active", "current_board"):
                return self._action_get_active_board()
            elif action == "create_board":
                return self._action_create_board(board_name or text)
            elif action == "delete_board":
                return self._action_delete_board(board_id or None, board_name or None)
            elif action == "list_lanes":
                return self._action_list_lanes(board_id or None, board_name or None)
            elif action == "create_ticket":
                return self._action_create_ticket(
                    text=text, board_name=board_name, board_id=board_id or None,
                    lane_name=lane_name, title=title, description=description,
                    priority=priority,
                    linked_project_id=linked_project_id or None,
                    linked_workflow_id=linked_workflow_id or None,
                    send_to_cli=send_to_cli,
                )
            elif action == "list_tickets":
                return self._action_list_tickets(board_id or None, board_name or None, lane_name or None)
            elif action in ("list_trello_tickets", "list_jira_tickets", "list_external_tickets"):
                provider = "trello" if "trello" in action else "jira" if "jira" in action else (text.split()[0] if text else "trello")
                ext_board_id = str(board_id) if board_id else ""
                if not ext_board_id and board_name:
                    # Try to find the external board by name
                    ext = self._fetch_external_boards()
                    for b in ext.get(provider, []):
                        if board_name.lower() in b["name"].lower():
                            ext_board_id = b["id"]
                            break
                if not ext_board_id:
                    return voice_then_reference(
                        f"Say which {provider} board to use, or list boards first.",
                        f"Please specify a board. Use action='list_boards' to see available {provider} boards.",
                    )
                tickets = self._fetch_external_tickets(provider, ext_board_id)
                if not tickets:
                    return voice_then_reference(
                        "That board came back with no tickets.",
                        f"No tickets found on {provider} board {ext_board_id}.",
                    )
                lines = [f"#{t['id']} {t['title']}" + (f" [{t.get('status', '')}]" if t.get('status') else "") for t in tickets[:30]]
                ref = f"{provider.title()} board tickets ({len(tickets)}):\n" + "\n".join(lines)
                spoken = f"I pulled your {provider} board; there are {len(tickets)} items. Details are below."
                return voice_then_reference(spoken, ref)
            elif action in ("update_external_ticket", "move_external_ticket", "comment_external_ticket"):
                return self._action_external_ticket(
                    action=action,
                    text=text,
                    board_name=board_name,
                    board_id=board_id or None,
                    lane_name=lane_name,
                    title=title,
                    description=description,
                    external_item_id=external_item_id or kwargs.get("external_card_id", ""),
                    external_issue_key=external_issue_key,
                    comment_text=comment_text or kwargs.get("comment", ""),
                )
            elif action == "get_ticket":
                return self._action_get_ticket(ticket_id or self._last_ticket_id)
            elif action in ("discuss_ticket", "talk_about_ticket", "prepare_ticket_discussion"):
                return self._action_discuss_ticket(
                    ticket_id=ticket_id,
                    text=text,
                    external_issue_key=external_issue_key,
                )
            elif action == "update_ticket":
                return self._action_update_ticket(
                    ticket_id or self._last_ticket_id, title=title,
                    description=description, priority=priority, lane_name=lane_name,
                    linked_project_id=linked_project_id or None,
                    linked_workflow_id=linked_workflow_id or None,
                    send_to_cli=send_to_cli,
                )
            elif action == "move_ticket":
                return self._action_move_ticket(ticket_id or self._last_ticket_id, lane_name)
            elif action == "delete_ticket":
                return self._action_delete_ticket(ticket_id or self._last_ticket_id)
            elif action == "attach_file":
                return self._action_attach_file(ticket_id or self._last_ticket_id, file_path)
            elif action == "delete_file":
                return self._action_delete_file(ticket_id or self._last_ticket_id, file_path)
            elif action == "add_todo":
                return self._action_add_todo(ticket_id or self._last_ticket_id, todo_text or text)
            elif action == "toggle_todo":
                return self._action_toggle_todo(ticket_id or self._last_ticket_id, todo_text or text)
            elif action == "delete_todo":
                return self._action_delete_todo(ticket_id or self._last_ticket_id, todo_text or text)
            elif action == "add_link":
                return self._action_add_link(ticket_id or self._last_ticket_id, title, url)
            elif action == "delete_link":
                return self._action_delete_link(ticket_id or self._last_ticket_id, url)
            elif action == "send_to_project":
                return self._action_send_to_project(ticket_id or self._last_ticket_id)
            elif action in ("activate_board", "set_board", "use_board"):
                return self._action_activate_board(board_id or None, board_name or text)
            elif action in ("send_to_cli", "push_to_cli", "run_cli"):
                return self._action_send_to_cli(ticket_id or self._last_ticket_id)
            elif action in ("checkin_overview", "workflow_overview", "board_overview", "agent_status"):
                return self._action_checkin_overview()
            elif action in ("whatsapp_list_chats", "wa_list_chats"):
                return self._action_whatsapp_list_chats(limit=message_limit)
            elif action in ("whatsapp_list_messages", "wa_list_messages", "whatsapp_read"):
                return self._action_whatsapp_list_messages(
                    jid_phone=jid_phone or text,
                    limit=message_limit,
                    unprocessed_only=unprocessed_only,
                )
            elif action in ("whatsapp_snapshot_to_ticket", "wa_snapshot_to_ticket", "snapshot_whatsapp"):
                return self._action_whatsapp_snapshot_to_ticket(
                    board_id=board_id or None,
                    board_name=board_name,
                    jid_phone=jid_phone,
                    message_ids=message_ids,
                    title=title,
                )
            elif action in ("whatsapp_send_message", "wa_send_message", "send_whatsapp"):
                return self._action_whatsapp_send_message(jid=jid, jid_phone=jid_phone, text=text or description or title)
            else:
                ref = (
                    f"Unknown action '{action}'. Valid actions: list_boards, get_active_board, create_board, delete_board, "
                    "activate_board, list_lanes, create_ticket, list_tickets, get_ticket, discuss_ticket, update_ticket, "
                    "move_ticket, update_external_ticket, move_external_ticket, comment_external_ticket, "
                    "delete_ticket, attach_file, delete_file, add_todo, toggle_todo, "
                    "delete_todo, add_link, delete_link, send_to_project, send_to_cli, checkin_overview, "
                    "whatsapp_list_chats, whatsapp_list_messages, whatsapp_snapshot_to_ticket, whatsapp_send_message"
                )
                return voice_then_reference(
                    "That ticket-board action name did not match anything I know how to run.",
                    ref,
                )

        except Exception as e:
            logger.error("KanbanTicketTool error: %s", e, exc_info=True)
            return voice_then_reference(
                "Something went wrong while running that ticket command.",
                f"Error: {e}",
            )

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)

    # ── Ticket text helpers (get_ticket / discuss_ticket) ───────────────────

    def _plain_ticket_description(self, raw: Optional[str]) -> str:
        s = (raw or "").strip()
        if not s:
            return "(none)"
        if "<" not in s and ">" not in s:
            return s
        plain = re.sub(r"<[^>]+>", " ", s)
        plain = re.sub(r"\s+", " ", plain).strip()
        return plain or "(none)"

    def _parse_jira_key_from_text(self, txt: str) -> Optional[str]:
        if not txt:
            return None
        m = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", txt.upper())
        return m.group(1) if m else None

    def _parse_numeric_ticket_id_from_text(self, txt: str) -> Optional[int]:
        if not txt:
            return None
        for pat in (r"\bticket\s*#?\s*(\d+)\b", r"#\s*(\d+)\b"):
            m = re.search(pat, txt, re.I)
            if m:
                return int(m.group(1))
        return None

    def _find_ticket_id_by_external_key(self, key: str) -> Optional[int]:
        key_u = (key or "").strip().upper()
        if not key_u:
            return None
        from sqlalchemy import func

        from distr.core.db.kanban import KanbanTicket

        with self._get_session() as s:
            t = (
                s.query(KanbanTicket)
                .filter(KanbanTicket.external_id.isnot(None))
                .filter(func.upper(KanbanTicket.external_id) == key_u)
                .first()
            )
            return int(t.id) if t else None

    def _discuss_ticket_no_local_shadow_for_key(self, key: str) -> str:
        """External Jira/Trello key with no local KanbanTicket row — do not sound like missing chat context."""
        key = (key or "").strip()
        return voice_then_reference(
            "That issue is not copied onto a local Ticket Board row yet, but the ticket details the user already "
            "put in this chat are enough to discuss — continue from there.",
            (
                f"[discuss_ticket] No KanbanTicket with external_id '{key}'. "
                "If this thread began with '[Ticket Board — discuss this ticket]', the user message already carries "
                "title, URL, priority, and labels for that external issue — do not report missing context or call this "
                "tool again for the same key. Suggest **Copy to local board** only if they need a database ticket id or "
                "send-to-project from the app."
            ),
        )

    def _ticket_detail_parts(self, t) -> List[str]:
        """Body lines for get_ticket / discuss_ticket (ORM KanbanTicket inside an open session)."""
        files = [(f.filename or "").strip() for f in t.files] if t.files else []
        todos = [
            f"{'[x]' if td.done else '[ ]'} {(td.text or '').strip()}"
            for td in t.todos
        ] if t.todos else []
        links = [f"{(l.title or '').strip()}: {(l.url or '').strip()}" for l in t.links] if t.links else []
        desc = self._plain_ticket_description(t.description)
        parts = [
            f"Ticket #{t.id}: {t.title}",
            f"Lane: {t.lane.name if t.lane else '?'}",
            f"Priority: {t.priority}",
            f"Description: {desc}",
            f"Send to CLI: {'Yes' if t.send_to_cli else 'No'}",
        ]
        ext_id = getattr(t, "external_id", None)
        if ext_id:
            parts.append(f"External ID: {ext_id}")
        if files:
            parts.append(f"Files: {', '.join(files)}")
        if todos:
            parts.append(f"Todos: {'; '.join(todos)}")
        if links:
            parts.append(f"Links: {'; '.join(links)}")
        return parts

    # ── Action implementations ────────────────────────────────────────────

    def _action_list_boards(self) -> str:
        active_board, _ = self._get_active_board(auto_recover=True)
        active_id = active_board["id"] if active_board else None
        boards = self._all_boards()
        lines = []
        for b in boards:
            marker = ", ACTIVE" if active_id is not None and b["id"] == active_id else ""
            lines.append(f"Board '{b['name']}' (ID {b['id']}, local{marker})")

        # Also fetch external boards
        try:
            ext = self._fetch_external_boards()
            seen = set()
            for b in ext.get("trello", []):
                key = ("trello", str(b.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"Board '{b['name']}' (Trello, ID {b['id']})")
            for b in ext.get("jira", []):
                key = ("jira", str(b.get("id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"Board '{b['name']}' (Jira, ID {b['id']})")
        except Exception as e:
            logger.debug("Could not fetch external boards: %s", e)

        if not lines:
            return voice_then_reference(
                "You do not have any ticket boards yet; create one from the Board view.",
                "No Ticket boards found. You can create one in the Board UI.",
            )
        ref = "Available boards:\n" + "\n".join(lines)
        n = len(lines)
        spoken = (
            "You have one board available; details are below."
            if n == 1
            else f"I listed {n} boards, including local ones and any linked Trello or Jira boards."
        )
        return voice_then_reference(spoken, ref)

    def _action_get_active_board(self) -> str:
        board, was_recovered = self._get_active_board(auto_recover=True)
        if not board:
            return voice_then_reference(
                "There are no local ticket boards yet.",
                "No local Ticket boards found.",
            )
        if was_recovered:
            ref = f"No board was previously marked active. I set '{board['name']}' (ID {board['id']}) as active."
            spoken = f"No board was active before, so I set {board['name']} as your active board."
            return voice_then_reference(spoken, ref)
        ref = f"Active board is '{board['name']}' (ID {board['id']})."
        spoken = f"Your active board is {board['name']}."
        return voice_then_reference(spoken, ref)

    def _fetch_external_boards(self) -> Dict:
        """Fetch Trello and Jira boards from connected accounts."""
        result = {"trello": [], "jira": []}
        for acct in self._load_connected_accounts():
            provider = (acct.get("provider") or "").lower()
            if provider == "trello" and acct.get("api_key") and acct.get("api_token"):
                try:
                    import requests
                    resp = requests.get(
                        "https://api.trello.com/1/members/me/boards",
                        params={"key": acct["api_key"], "token": acct["api_token"], "fields": "name,url,closed"},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        for b in resp.json():
                            if not b.get("closed", False):
                                result["trello"].append({"id": b["id"], "name": b["name"], "url": b.get("url", "")})
                except Exception:
                    pass
            elif provider == "jira" and acct.get("email") and acct.get("api_token"):
                try:
                    import requests
                    from requests.auth import HTTPBasicAuth
                    domain = self._jira_domain(acct)
                    if domain:
                        resp = requests.get(
                            f"https://{domain}/rest/agile/1.0/board",
                            auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                            headers={"Accept": "application/json"}, timeout=10,
                        )
                        if resp.status_code == 200:
                            for b in resp.json().get("values", []):
                                location = b.get("location") or {}
                                result["jira"].append({
                                    "id": str(b["id"]),
                                    "name": b["name"],
                                    "project_key": location.get("projectKey") or "",
                                })
                except Exception:
                    pass
        return result

    def _fetch_external_tickets(self, provider: str, board_id: str) -> List[Dict]:
        """Fetch tickets/cards from an external Trello or Jira board."""
        import json as _json
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        raw = settings.get("connected_accounts") or "[]"
        if isinstance(raw, str):
            try:
                accounts = _json.loads(raw)
            except Exception:
                accounts = []
        else:
            accounts = raw if isinstance(raw, list) else []

        tickets = []
        for acct in accounts:
            acct_provider = (acct.get("provider") or "").lower()
            if provider == "trello" and acct_provider == "trello" and acct.get("api_key") and acct.get("api_token"):
                try:
                    import requests
                    resp = requests.get(
                        f"https://api.trello.com/1/boards/{board_id}/cards",
                        params={"key": acct["api_key"], "token": acct["api_token"], "fields": "name,desc,url,idList"},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        for c in resp.json():
                            tickets.append({"id": c["id"], "title": c["name"], "description": c.get("desc", "")[:300], "url": c.get("url", "")})
                    break
                except Exception:
                    pass
            elif provider == "jira" and acct_provider == "jira" and acct.get("email") and acct.get("api_token"):
                try:
                    import requests
                    from requests.auth import HTTPBasicAuth
                    domain = acct.get("domain") or ""
                    if not domain:
                        server_url = (acct.get("server_url") or "").strip().rstrip("/")
                        if server_url:
                            domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
                    if domain:
                        resp = requests.get(
                            f"https://{domain}/rest/agile/1.0/board/{board_id}/issue",
                            auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                            headers={"Accept": "application/json"},
                            params={"maxResults": 50}, timeout=10,
                        )
                        if resp.status_code == 200:
                            for issue in resp.json().get("issues", []):
                                fields = issue.get("fields", {})
                                tickets.append({
                                    "id": issue["key"],
                                    "title": fields.get("summary", ""),
                                    "description": (str(fields.get("description", "")) or "")[:300],
                                    "status": fields.get("status", {}).get("name", ""),
                                    "url": f"https://{domain}/browse/{issue['key']}",
                                })
                    break
                except Exception:
                    pass
        return tickets

    def _action_list_lanes(self, board_id=None, board_name=None) -> str:
        board = self._find_board(board_id, board_name)
        if not board:
            return voice_then_reference(
                "I could not find that board.",
                "Board not found. Use action='list_boards' to see available boards.",
            )
        lanes = self._get_lanes(board["id"])
        names = [l["name"] for l in lanes]
        ref = f"Lanes in '{board['name']}': {', '.join(names)}"
        spoken = f"Here are the lanes on {board['name']}."
        return voice_then_reference(spoken, ref)

    def _action_create_ticket(self, text="", board_name="", board_id=None,
                               lane_name="", title="", description="",
                               priority="medium",
                               linked_project_id=None, linked_workflow_id=None,
                               send_to_cli=False) -> str:
        remote_provider = self._remote_provider_from_request(text, board_name)
        if remote_provider:
            external_board, external_boards = self._find_external_board(
                remote_provider,
                board_id=board_id,
                board_name=board_name,
                text=text,
            )
            if not external_board:
                if external_boards:
                    board_list = ", ".join(
                        f"'{b.get('name')}' (ID {b.get('id')})"
                        for b in external_boards[:8]
                    )
                    return voice_then_reference(
                        f"Tell me which {remote_provider.title()} board to use.",
                        f"Available {remote_provider.title()} boards: {board_list}",
                    )
                return voice_then_reference(
                    f"I could not find any connected {remote_provider.title()} boards.",
                    f"No {remote_provider.title()} boards are available. Check the account connection in Settings.",
                )
            return self._create_external_ticket(
                remote_provider,
                external_board,
                text=text,
                lane_name=lane_name,
                title=title,
                description=description,
                priority=priority,
            )

        # Resolve board
        board = self._find_board(board_id, board_name)
        if not board:
            # Try to extract board name from text
            boards = self._all_boards()
            if boards:
                text_lower = (text or "").lower()
                for b in boards:
                    if b["name"].lower() in text_lower:
                        board = b
                        break
            if not board:
                if len(boards) == 1:
                    board = boards[0]
                elif len(boards) > 1:
                    board_list = ", ".join(f"'{b['name']}' (ID {b['id']})" for b in boards)
                    ref = f"There are multiple boards. Please specify which one: {board_list}"
                    spoken = "You have several boards; tell me which one to use."
                    return voice_then_reference(spoken, ref)
                else:
                    return voice_then_reference(
                        "There are no boards yet; create one first from the Board view or ask me to set one up.",
                        "No boards found. Create one first with action='create_board'.",
                    )

        # Resolve lane
        lane = self._find_lane(board["id"], lane_name)
        if not lane:
            return voice_then_reference(
                "That board has no lanes configured yet.",
                f"No lanes found in board '{board['name']}'.",
            )

        # Gather conversation context for rich ticket content
        conv_files = []
        if not title and not description:
            conv_text, conv_files = self._get_conversation_context(max_messages=10)
            raw = text
            if conv_text:
                raw = f"User instruction: {text}\n\nRecent conversation:\n{conv_text}"
            summary = self._summarise_for_ticket(raw)

            # Bulk mode — summary is a list of dicts
            if isinstance(summary, list):
                return self._create_bulk_tickets(
                    board, lane, summary, conv_files,
                    linked_project_id, linked_workflow_id,
                )

            title = summary["title"]
            description = summary["description"]
        elif not title:
            title = description[:80]
        elif not description:
            description = text or title

        title = _sanitize_ticket_title(title or "") or "New Ticket"

        # Create the ticket in DB
        description, skill_recommendations = _append_recommended_skills(title, description, text)

        from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard as KB
        with self._get_session() as s:
            lane_obj = orm_get_by_id(s, KanbanLane, lane["id"])
            max_pos = max([t.position for t in lane_obj.tickets], default=-1) if lane_obj else -1

            # Check if board has a default project
            board_obj = orm_get_by_id(s, KB, board["id"])
            effective_project_id = linked_project_id or (board_obj.default_project_id if board_obj else None)
            effective_workflow_id = linked_workflow_id or None
            effective_send_to_cli = send_to_cli or (board_obj.send_to_cli if board_obj else False)
            if effective_send_to_cli:
                effective_workflow_id = None  # CLI and workflow are mutually exclusive

            ticket = KanbanTicket(
                lane_id=lane["id"],
                title=title,
                description=description,
                priority=priority or "medium",
                position=max_pos + 1,
                linked_project_id=effective_project_id,
                linked_workflow_id=effective_workflow_id,
                send_to_cli=effective_send_to_cli,
                source_chat_id=self._source_chat_id_for_new_ticket(),
            )
            s.add(ticket)
            s.flush()
            ticket_id = ticket.id

        self._last_ticket_id = ticket_id
        self._last_board_id = board["id"]

        # Auto-attach files found in conversation
        attached = []
        for fp in conv_files:
            name = self._attach_file_to_ticket(ticket_id, fp)
            if name:
                attached.append(name)

        result = f"Created ticket '{title}' in board '{board['name']}', lane '{lane['name']}' (ID {ticket_id})"
        if attached:
            result += f". Attached {len(attached)} file(s): {', '.join(attached)}"
        if skill_recommendations:
            result += ". Recommended skills: " + ", ".join(rec.name for rec in skill_recommendations)
        logger.info("KanbanTicketTool: %s", result)
        spoken = f"I added a ticket called {title} on {board['name']}."
        if attached:
            spoken += " I also attached files from our conversation."
        return voice_then_reference(spoken, result)

    def _create_bulk_tickets(self, board, lane, tickets_data, conv_files,
                              linked_project_id, linked_workflow_id):
        """Create multiple tickets from a list of {title, description, priority} dicts."""
        from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard as KB

        created = []
        with self._get_session() as s:
            lane_obj = orm_get_by_id(s, KanbanLane, lane["id"])
            max_pos = max([t.position for t in lane_obj.tickets], default=-1) if lane_obj else -1
            board_obj = orm_get_by_id(s, KB, board["id"])
            effective_project_id = linked_project_id or (board_obj.default_project_id if board_obj else None)
            effective_workflow_id = linked_workflow_id or None
            effective_send_to_cli = board_obj.send_to_cli if board_obj else False
            if effective_send_to_cli:
                effective_workflow_id = None

            for item in tickets_data:
                if not isinstance(item, dict):
                    continue
                t_title = _sanitize_ticket_title((item.get("title") or "Untitled")[:200])
                t_desc = (item.get("description") or "")[:2000]
                t_desc, _skill_recs = _append_recommended_skills(t_title, t_desc, "")
                t_priority = item.get("priority", "medium")
                if t_priority not in ("low", "medium", "high", "critical"):
                    t_priority = "medium"
                max_pos += 1
                ticket = KanbanTicket(
                    lane_id=lane["id"], title=t_title, description=t_desc,
                    priority=t_priority, position=max_pos,
                    linked_project_id=effective_project_id,
                    linked_workflow_id=effective_workflow_id,
                    send_to_cli=effective_send_to_cli,
                    source_chat_id=self._source_chat_id_for_new_ticket(),
                )
                s.add(ticket)
                s.flush()
                created.append({"id": ticket.id, "title": t_title})

                # Attach conversation files to first ticket only
                if conv_files and len(created) == 1:
                    for fp in conv_files:
                        self._attach_file_to_ticket(ticket.id, fp)

        self._last_board_id = board["id"]
        if created:
            self._last_ticket_id = created[-1]["id"]

        titles = [f"#{c['id']} {c['title']}" for c in created]
        ref = f"Created {len(created)} ticket(s) in board '{board['name']}', lane '{lane['name']}':\n" + "\n".join(titles)
        spoken = f"I created {len(created)} tickets on {board['name']} in the {lane['name']} lane."
        return voice_then_reference(spoken, ref)

    def _action_list_tickets(self, board_id=None, board_name=None, lane_name=None) -> str:
        board = self._find_board(board_id, board_name)
        if not board:
            return voice_then_reference(
                "I could not find that board.",
                "Board not found.",
            )
        from distr.core.db.kanban import KanbanTicket, KanbanLane
        with self._get_session() as s:
            query = s.query(KanbanTicket).join(KanbanLane).filter(KanbanLane.board_id == board["id"])
            if lane_name:
                query = query.filter(KanbanLane.name.ilike(f"%{lane_name}%"))
            tickets = query.order_by(KanbanLane.position, KanbanTicket.position).all()
            if not tickets:
                return voice_then_reference(
                    f"There are no tickets on {board['name']} right now.",
                    f"No tickets in board '{board['name']}'.",
                )
            lines = []
            for t in tickets:
                lane_n = t.lane.name if t.lane else "?"
                files_count = len(t.files) if t.files else 0
                extra = f" ({files_count} files)" if files_count else ""
                lines.append(f"[{lane_n}] #{t.id} {t.title} ({t.priority}){extra}")
            ref = f"Tickets in '{board['name']}':\n" + "\n".join(lines)
            spoken = f"I listed the tickets on {board['name']}; lanes and priorities are below."
            return voice_then_reference(spoken, ref)

    def _action_get_ticket(self, ticket_id) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to open that.",
                "No ticket ID provided.",
            )
        from distr.core.db.kanban import KanbanTicket
        with self._get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                return voice_then_reference(
                    "I could not find that ticket.",
                    f"Ticket #{ticket_id} not found.",
                )
            ref = "\n".join(self._ticket_detail_parts(t))
            spoken_title = t.title
        return voice_then_reference(f"Opened ticket {spoken_title}.", ref)

    def _action_discuss_ticket(
        self,
        ticket_id: int = 0,
        text: str = "",
        external_issue_key: str = "",
    ) -> str:
        """Load full ticket context for conversational exploration (not send_to_project)."""
        tid = int(ticket_id or 0)
        ext = (external_issue_key or "").strip()
        txt = text or ""
        resolved: int = 0

        if tid:
            resolved = tid
        elif ext:
            found = self._find_ticket_id_by_external_key(ext)
            if not found:
                return self._discuss_ticket_no_local_shadow_for_key(ext)
            resolved = found
        else:
            jk = self._parse_jira_key_from_text(txt)
            if jk:
                found = self._find_ticket_id_by_external_key(jk)
                if not found:
                    return self._discuss_ticket_no_local_shadow_for_key(jk)
                resolved = found
            else:
                nid = self._parse_numeric_ticket_id_from_text(txt)
                if nid:
                    resolved = nid
                elif self._last_ticket_id:
                    resolved = int(self._last_ticket_id)
                else:
                    return voice_then_reference(
                        "I need a ticket number, a Jira-style key on the board, or a ticket we touched earlier in this chat.",
                        "discuss_ticket: pass ticket_id, external_issue_key, or text with #42 / PROJ-123; or create/list a ticket first.",
                    )

        from distr.core.db.kanban import KanbanTicket

        with self._get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, resolved)
            if not t:
                return voice_then_reference(
                    "I could not find that ticket.",
                    f"Ticket #{resolved} not found.",
                )
            self._last_ticket_id = t.id
            ref_body = "\n".join(self._ticket_detail_parts(t))
            spoken_title = t.title

        preamble = (
            "[Ticket discussion mode — loaded via discuss_ticket]\n"
            "The user wants to explore this ticket in conversation (scope, risks, next steps).\n"
            "Briefly acknowledge the ticket, ask a few focused questions if helpful, and do not "
            "send_to_project, move_ticket, or attach files unless they explicitly ask.\n"
            "---\n"
        )
        return voice_then_reference(
            f"I loaded {spoken_title} for discussion.",
            preamble + ref_body,
        )

    def _action_update_ticket(self, ticket_id, title="", description="",
                               priority="", lane_name="",
                               linked_project_id=None, linked_workflow_id=None,
                               send_to_cli=False) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to update that.",
                "No ticket ID provided.",
            )
        from distr.core.db.kanban import KanbanTicket, KanbanLane
        with self._get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                return voice_then_reference(
                    "I could not find that ticket.",
                    f"Ticket #{ticket_id} not found.",
                )
            if title:
                t.title = title
            if description:
                t.description = description
            if priority:
                t.priority = priority
            if linked_project_id:
                t.linked_project_id = linked_project_id
            if send_to_cli:
                t.send_to_cli = True
                t.linked_workflow_id = None  # CLI and workflow are mutually exclusive
            elif linked_workflow_id:
                t.linked_workflow_id = linked_workflow_id
                t.send_to_cli = False
                t.priority = priority
            if lane_name:
                # Move to different lane
                board_id = t.lane.board_id if t.lane else None
                if board_id:
                    new_lane = s.query(KanbanLane).filter(
                        KanbanLane.board_id == board_id,
                        KanbanLane.name.ilike(f"%{lane_name}%")
                    ).first()
                    if new_lane:
                        t.lane_id = new_lane.id
            return voice_then_reference("I saved those ticket changes.", f"Updated ticket #{ticket_id}")

    def _action_attach_file(self, ticket_id, file_path) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to attach a file.",
                "No ticket ID provided.",
            )
        if not file_path or not os.path.isfile(file_path):
            return voice_then_reference(
                "That file path does not exist or is not readable.",
                f"File not found: {file_path}",
            )
        name = self._attach_file_to_ticket(ticket_id, file_path)
        if name:
            return voice_then_reference(f"I attached {name} to that ticket.", f"Attached '{name}' to ticket #{ticket_id}")
        return voice_then_reference("I could not attach that file.", "Failed to attach file.")

    def _action_add_todo(self, ticket_id, text) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to add a checklist item.",
                "No ticket ID provided.",
            )
        if not text:
            return voice_then_reference(
                "Say what you want on the checklist.",
                "No todo text provided.",
            )
        from distr.core.db.kanban import KanbanTicket, KanbanTicketTodo
        with self._get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                return voice_then_reference(
                    "I could not find that ticket.",
                    f"Ticket #{ticket_id} not found.",
                )
            max_pos = max([td.position for td in t.todos], default=-1) if t.todos else -1
            todo = KanbanTicketTodo(ticket_id=ticket_id, text=text, position=max_pos + 1)
            s.add(todo)
        return voice_then_reference(
            "I added that checklist item to the ticket.",
            f"Added todo to ticket #{ticket_id}",
        )

    def _action_add_link(self, ticket_id, title, url) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to save a link.",
                "No ticket ID provided.",
            )
        if not url:
            return voice_then_reference(
                "I need a URL to attach.",
                "No URL provided.",
            )
        from distr.core.db.kanban import KanbanTicket, KanbanTicketLink
        with self._get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                return voice_then_reference(
                    "I could not find that ticket.",
                    f"Ticket #{ticket_id} not found.",
                )
            link = KanbanTicketLink(ticket_id=ticket_id, title=title or url, url=url)
            s.add(link)
        return voice_then_reference(
            "I saved that link on the ticket.",
            f"Added link to ticket #{ticket_id}",
        )

    # ── Board CRUD ────────────────────────────────────────────────────────

    def _action_create_board(self, name: str) -> str:
        if not name or not name.strip():
            return voice_then_reference(
                "Say what you want the new board to be called.",
                "Board name is required.",
            )
        from distr.core.db.kanban import KanbanBoard, KanbanLane
        default_lanes = ["Backlog", "Current", "QA / Assess", "Done"]
        with self._get_session() as s:
            board = KanbanBoard(name=name.strip(), source="database")
            s.add(board)
            s.flush()
            for i, lane_name in enumerate(default_lanes):
                s.add(KanbanLane(board_id=board.id, name=lane_name, position=i))
            s.flush()
            board_id = board.id
        self._last_board_id = board_id
        ref = f"Created board '{name.strip()}' (ID {board_id}) with lanes: {', '.join(default_lanes)}"
        spoken = f"I created a board called {name.strip()} with the usual lanes."
        return voice_then_reference(spoken, ref)

    def _action_delete_board(self, board_id=None, board_name=None) -> str:
        board = self._find_board(board_id, board_name)
        if not board:
            return voice_then_reference(
                "I could not find that board.",
                "Board not found.",
            )
        from distr.core.db.kanban import KanbanBoard
        with self._get_session() as s:
            b = orm_get_by_id(s, KanbanBoard, board["id"])
            if not b:
                return voice_then_reference(
                    "I could not find that board.",
                    "Board not found.",
                )
            name = b.name
            s.delete(b)
        return voice_then_reference(f"I removed the board {name} and everything on it.", f"Deleted board '{name}' and all its tickets")

    # ── Ticket delete & move ──────────────────────────────────────────────

    def _action_delete_ticket(self, ticket_id) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to delete that.",
                "No ticket ID provided.",
            )
        from distr.core.db.kanban import KanbanTicket
        with self._get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                return voice_then_reference(
                    "I could not find that ticket.",
                    f"Ticket #{ticket_id} not found.",
                )
            title = t.title
            s.delete(t)
        return voice_then_reference(f"I deleted that ticket: {title}.", f"Deleted ticket #{ticket_id} ('{title}')")

    def _action_move_ticket(self, ticket_id, lane_name) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to move that.",
                "No ticket ID provided.",
            )
        if not lane_name:
            return voice_then_reference(
                "Say which lane to move it into.",
                "No lane name provided.",
            )
        from distr.core.db.kanban import KanbanTicket, KanbanLane
        with self._get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                return voice_then_reference(
                    "I could not find that ticket.",
                    f"Ticket #{ticket_id} not found.",
                )
            board_id = t.lane.board_id if t.lane else None
            if not board_id:
                return voice_then_reference(
                    "That ticket is not on a board I can read.",
                    "Cannot determine board for this ticket.",
                )
            new_lane = s.query(KanbanLane).filter(
                KanbanLane.board_id == board_id,
                KanbanLane.name.ilike(f"%{lane_name}%")
            ).first()
            if not new_lane:
                lanes = s.query(KanbanLane).filter_by(board_id=board_id).order_by(KanbanLane.position).all()
                available = ", ".join(l.name for l in lanes)
                return voice_then_reference(
                    "That lane name did not match any lane on this board.",
                    f"Lane '{lane_name}' not found. Available: {available}",
                )
            max_pos = max([tk.position for tk in new_lane.tickets], default=-1)
            t.lane_id = new_lane.id
            t.position = max_pos + 1
            moved_lane_name = new_lane.name
        return voice_then_reference(
            f"I moved that ticket into {moved_lane_name}.",
            f"Moved ticket #{ticket_id} to lane '{moved_lane_name}'",
        )

    # ── Sub-resource deletes ──────────────────────────────────────────────

    def _action_delete_file(self, ticket_id, filename) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to remove an attachment.",
                "No ticket ID provided.",
            )
        if not filename:
            return voice_then_reference(
                "Say which filename to remove.",
                "No filename provided.",
            )
        from distr.core.db.kanban import KanbanTicketFile
        with self._get_session() as s:
            f = s.query(KanbanTicketFile).filter_by(ticket_id=ticket_id).filter(
                KanbanTicketFile.filename.ilike(f"%{filename}%")
            ).first()
            if not f:
                return voice_then_reference(
                    "That attachment was not on the ticket.",
                    f"File '{filename}' not found on ticket #{ticket_id}.",
                )
            name = f.filename
            # Remove physical file
            try:
                if os.path.exists(f.file_path):
                    os.remove(f.file_path)
            except Exception:
                pass
            s.delete(f)
        return voice_then_reference(
            f"I removed {name} from the ticket.",
            f"Removed file '{name}' from ticket #{ticket_id}",
        )

    def _action_toggle_todo(self, ticket_id, todo_text) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number for that checklist change.",
                "No ticket ID provided.",
            )
        if not todo_text:
            return voice_then_reference(
                "Say which checklist line to toggle.",
                "No todo text provided.",
            )
        from distr.core.db.kanban import KanbanTicketTodo
        with self._get_session() as s:
            todo = s.query(KanbanTicketTodo).filter_by(ticket_id=ticket_id).filter(
                KanbanTicketTodo.text.ilike(f"%{todo_text}%")
            ).first()
            if not todo:
                return voice_then_reference(
                    "I could not find a checklist line that matched what you said.",
                    f"Todo matching '{todo_text}' not found on ticket #{ticket_id}.",
                )
            todo.done = not todo.done
            status = "done" if todo.done else "not done"
        return voice_then_reference(
            f"I flipped that checklist item to {status}.",
            f"Toggled todo to {status} on ticket #{ticket_id}",
        )

    def _action_delete_todo(self, ticket_id, todo_text) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to remove a checklist line.",
                "No ticket ID provided.",
            )
        if not todo_text:
            return voice_then_reference(
                "Say which checklist line to remove.",
                "No todo text provided.",
            )
        from distr.core.db.kanban import KanbanTicketTodo
        with self._get_session() as s:
            todo = s.query(KanbanTicketTodo).filter_by(ticket_id=ticket_id).filter(
                KanbanTicketTodo.text.ilike(f"%{todo_text}%")
            ).first()
            if not todo:
                return voice_then_reference(
                    "I could not find a checklist line that matched what you said.",
                    f"Todo matching '{todo_text}' not found on ticket #{ticket_id}.",
                )
            s.delete(todo)
        return voice_then_reference(
            "I removed that checklist line from the ticket.",
            f"Removed todo from ticket #{ticket_id}",
        )

    def _action_delete_link(self, ticket_id, url) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to remove a link.",
                "No ticket ID provided.",
            )
        if not url:
            return voice_then_reference(
                "Say which link to remove.",
                "No URL provided.",
            )
        from distr.core.db.kanban import KanbanTicketLink
        with self._get_session() as s:
            link = s.query(KanbanTicketLink).filter_by(ticket_id=ticket_id).filter(
                KanbanTicketLink.url.ilike(f"%{url}%")
            ).first()
            if not link:
                return voice_then_reference(
                    "I could not find a link on that ticket that matched.",
                    f"Link matching '{url}' not found on ticket #{ticket_id}.",
                )
            s.delete(link)
        return voice_then_reference(
            "I removed that link from the ticket.",
            f"Removed link from ticket #{ticket_id}",
        )

    # ── Send to project ───────────────────────────────────────────────────

    def _action_send_to_project(self, ticket_id) -> str:
        if not ticket_id:
            return voice_then_reference(
                "I need a ticket number to export that.",
                "No ticket ID provided.",
            )
        logger.info("send_to_project tool: begin ticket_id=%s", ticket_id)
        from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard as KB
        from distr.core.db.projects import Project

        with self._get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                return voice_then_reference(
                    "I could not find that ticket.",
                    f"Ticket #{ticket_id} not found.",
                )

            # Resolve project: ticket-level first, then board-level default
            project_id = t.linked_project_id
            if not project_id and t.lane:
                board = orm_get_by_id(s, KB, t.lane.board_id)
                if board:
                    project_id = board.default_project_id

            if not project_id:
                return voice_then_reference(
                    "Link a project to that ticket or its board before exporting.",
                    "No project linked to this ticket or its board.",
                )

            project = orm_get_by_id(s, Project, project_id)
            if not project:
                return voice_then_reference(
                    "The linked project record is missing.",
                    "Linked project not found.",
                )
            if not project.folder_location:
                return voice_then_reference(
                    f"Set a folder path on project {project.name} first.",
                    f"Project '{project.name}' has no folder location set.",
                )

            proj_root = os.path.abspath(os.path.expanduser(project.folder_location.strip()))
            if not os.path.isdir(proj_root):
                return voice_then_reference(
                    "That project's folder path does not exist on disk or is not a directory.",
                    f"Project folder missing or not a directory: {proj_root!r}",
                )

            tickets_folder = os.path.join(proj_root, ".tickets")
            try:
                os.makedirs(tickets_folder, exist_ok=True)
            except OSError as e:
                logger.exception("send_to_project tool: cannot mkdir %s", tickets_folder)
                return voice_then_reference(
                    "I could not create the .tickets folder under that project path.",
                    f"Cannot create .tickets: {e}",
                )

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            existing_export = _find_existing_export_for_ticket(tickets_folder, t.id)
            if existing_export:
                ticket_path = existing_export
                logger.info(
                    "send_to_project tool: updating existing export for ticket %s → %s",
                    t.id,
                    ticket_path,
                )
            else:
                ticket_path = os.path.join(tickets_folder, f"ticket_{timestamp}.md")

            preserved_id = _read_yaml_frontmatter_field(ticket_path, "id") if existing_export else None
            export_doc_id = preserved_id if preserved_id else f"ticket_{timestamp}"

            # Build markdown
            todos_md = ""
            if t.todos:
                todos_md = "\n## Checklist\n"
                for td in t.todos:
                    mark = "x" if td.done else " "
                    todos_md += f"- [{mark}] {(td.text or '').strip()}\n"

            links_md = ""
            if t.links:
                links_md = "\n## Links\n"
                for lk in t.links:
                    lt = (lk.title or "").strip() or "link"
                    lu = (lk.url or "").strip()
                    links_md += f"- [{lt}]({lu})\n"

            files_md = ""
            if t.files:
                files_md = "\n## Attached Files\n"
                for fl in t.files:
                    fn = (fl.filename or "").strip() or "file"
                    fp = (fl.file_path or "").strip()
                    files_md += f"- {fn} (`{fp}`)\n"

            try:
                desc_body = _plain_desc_for_ticket_export(t.description or "")
            except Exception as e:
                logger.warning("send_to_project tool: plain description failed: %s", e)
                desc_body = (t.description or "").strip() or "(no description)"

            content = (
                f"---\nid: {_yaml_scalar(export_doc_id)}\ntitle: {_yaml_scalar(t.title or '')}\n"
                f"project: {_yaml_scalar(project.name or '')}\n"
                f"created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"priority: {_yaml_scalar(t.priority or 'medium')}\nstatus: open\n"
                f"source: kanban_ticket_{t.id}\n---\n\n"
                f"## Description\n{desc_body}\n"
                f"{todos_md}{links_md}{files_md}\n"
                f"## Context\n- **Folder:** `{proj_root}`\n\n"
                f"---\n*Sent from Ticket Board via DecisionsAI*\n"
            )

            try:
                with open(ticket_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                logger.exception("send_to_project tool: failed to write %s", ticket_path)
                return voice_then_reference(
                    "Something went wrong writing the ticket file to the project folder.",
                    f"Failed to write ticket file: {e}",
                )

            project_name = project.name

        logger.info(
            "send_to_project tool: ok ticket_id=%s path=%s",
            ticket_id,
            ticket_path,
        )
        ref = f"Sent ticket #{ticket_id} to project '{project_name}' → {ticket_path}"
        spoken = f"I exported that ticket into your {project_name} project folder."
        return voice_then_reference(spoken, ref)

    # ── Activate board ────────────────────────────────────────────────────

    def _action_activate_board(self, board_id=None, board_name=None) -> str:
        """Set a board as the active/in-use board. Future ticket commands default to this board."""
        board = self._find_board(board_id, board_name)
        if not board:
            return voice_then_reference(
                "I could not find a board with that name.",
                f"Board '{board_name or board_id}' not found.",
            )

        from distr.core.db.kanban import KanbanBoard as KB
        with self._get_session() as s:
            # Deactivate all boards
            s.query(KB).filter(KB.in_use == True).update({"in_use": False})
            b = orm_get_by_id(s, KB, board["id"])
            if not b:
                return voice_then_reference(
                    "I could not find that board.",
                    f"Board '{board['name']}' not found.",
                )
            b.in_use = True
            s.commit()

        self._last_board_id = board["id"]
        return voice_then_reference(
            f"{board['name']} is now your default board for ticket commands.",
            f"Board '{board['name']}' is now your active board. All ticket commands will default to this board.",
        )

    # ── Send ticket to CLI ────────────────────────────────────────────────

    def _action_send_to_cli(self, ticket_id) -> str:
        """Send a ticket's instruction to pi coding agent for the linked project. Creates an audit trail."""
        if not ticket_id:
            # Try to find the most recent ticket from the in_use board
            try:
                from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard as KB
                with self._get_session() as s:
                    board = s.query(KB).filter(KB.in_use == True).first()
                    if board:
                        for lane in sorted(board.lanes, key=lambda l: l.position):
                            tickets = sorted(lane.tickets, key=lambda t: t.position)
                            if tickets:
                                ticket_id = tickets[0].id
                                break
            except Exception:
                pass
            if not ticket_id:
                return voice_then_reference(
                    "I need a ticket, and the active board has none to pick from.",
                    "No ticket ID provided and no tickets found on the active board.",
                )

        from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard as KB
        from distr.core.db.projects import Project
        from distr.core.pi_rpc import PiRpcSession

        pi_path = PiRpcSession.find_pi()
        if not pi_path:
            return voice_then_reference(
                "Pi coding agent is not installed on this system.",
                "Pi coding agent is not installed. Install it with: npm install -g @mariozechner/pi-coding-agent",
            )

        with self._get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                return voice_then_reference(
                    "I could not find that ticket.",
                    f"Ticket #{ticket_id} not found.",
                )

            title = t.title
            description = t.description or ""
            ticket_id_val = t.id

            # Resolve project
            project_id = t.linked_project_id
            if not project_id and t.lane:
                board = orm_get_by_id(s, KB, t.lane.board_id)
                if board:
                    project_id = board.default_project_id

            if not project_id:
                return voice_then_reference(
                    "Link a project to that ticket or its board before sending to Pi.",
                    "No project linked to this ticket or its board. Link a project first.",
                )

            project = orm_get_by_id(s, Project, project_id)
            if not project:
                return voice_then_reference(
                    "The linked project record is missing.",
                    "Linked project not found.",
                )
            if not project.folder_location:
                return voice_then_reference(
                    f"Set a folder path on project {project.name} first.",
                    f"Project '{project.name}' has no folder location set.",
                )

            folder = project.folder_location
            project_name = project.name

            from distr.core.kanban.ticket_cli_context import build_kanban_ticket_cli_instruction

            instruction = build_kanban_ticket_cli_instruction(
                s,
                ticket_id_val,
                project_name=project_name,
                project_folder=folder or "",
                project_id=project_id,
            )

        # Create audit trail
        audit_id = None
        step_id = None
        try:
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
            with self._get_session() as s:
                audit = AutoWorkflow(
                    name=f"[Project: {project_name}] Ticket #{ticket_id_val}: {title}",
                    status="in_progress",
                    workflow_type="pi_agent",
                )
                s.add(audit)
                s.flush()
                step = AutoWorkflowStep(
                    workflow_id=audit.id, position=0,
                    name=f"Ticket #{ticket_id_val}", instruction=instruction[:500],
                    status="running", tool_used="pi",
                )
                s.add(step)
                s.commit()
                audit_id = audit.id
                step_id = step.id
        except Exception as e:
            logger.debug("Could not create audit for send_to_cli: %s", e)

        if self.event_queue:
            try:
                self.event_queue.put(("workflow_updated", {}), block=False)
            except Exception:
                pass

        # Use unified CLI dispatch — ensures RPC session exists for real-time CLI feed
        from distr.core.agent.tools.integrations.unified_cli import dispatch_to_cli

        if project_id and folder:
            result = dispatch_to_cli(
                project_id=project_id,
                cwd=folder,
                instruction=instruction,
                project_name=project_name,
                ticket_id=ticket_id_val,
                append_system_prompt=(
                    f"You are working on project: {project_name}. "
                    "The prompt includes a [KANBAN TICKET CONTEXT] block — use checklist, links, and file paths."
                ),
            )
            if result["success"]:
                return voice_then_reference(
                    f"I sent that ticket to Pi for {project_name}. Check the CLI tab for progress.",
                    f"[Pi — {project_name}] Ticket #{ticket_id_val} sent to CLI. Check the CLI tab for progress.",
                )
            return voice_then_reference(
                "Pi did not accept that run; the error detail is below.",
                f"[Pi — {project_name}] Ticket #{ticket_id_val} failed: {result['message']}",
            )

    def _action_checkin_overview(self) -> str:
        """Return a concise status report of active board check-ins and workflow runs."""
        from distr.core.kanban.agent import _active_agents, _active_agents_lock
        from distr.core.workflow.service import get_active_runs
        with _active_agents_lock:
            agents = list(_active_agents.items())

        if not agents:
            runs = get_active_runs(limit=25)
            if not runs:
                return voice_then_reference(
                    "Nothing is actively running on boards or workflows right now.",
                    "No active board check-ins or workflow runs.",
                )
            lines = ["No in-memory board agents, but active workflow runs exist:"]
            for r in runs[:10]:
                lines.append(
                    f"- run #{r.get('id')} board='{r.get('board_name') or r.get('board_id')}' "
                    f"ticket='{r.get('ticket_title') or r.get('ticket_id')}' "
                    f"phase={r.get('phase') or 'unknown'} status={r.get('status')}"
                )
            ref = "\n".join(lines)
            spoken = "No board agents are live in memory, but workflow runs are still active; details are below."
            return voice_then_reference(spoken, ref)

        board_lines = []
        for board_id, agent in agents:
            s = agent.status
            board_lines.append(
                f"- board #{board_id}: state={s.state} ticket='{s.current_ticket_title or s.current_ticket_id or 'none'}' "
                f"phase={s.current_phase or 'unknown'} progress={s.processed_count}/{s.total_tickets}"
            )

        runs = get_active_runs(limit=25)
        run_lines = []
        for r in runs[:10]:
            run_lines.append(
                f"- run #{r.get('id')} board='{r.get('board_name') or r.get('board_id')}' "
                f"ticket='{r.get('ticket_title') or r.get('ticket_id')}' step='{r.get('current_step_name') or r.get('current_step_id')}' "
                f"phase={r.get('phase') or 'unknown'} elapsed={r.get('elapsed_seconds', 0)}s"
            )

        parts = ["Active board check-ins:", *board_lines]
        if run_lines:
            parts.extend(["", "Active workflow runs:", *run_lines])
        ref = "\n".join(parts)
        spoken = "Here is the check-in view for active boards and workflow runs."
        return voice_then_reference(spoken, ref)

    def _get_whatsapp_manager(self):
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            return getattr(app, "whatsapp_manager", None) if app else None
        except Exception:
            return None

    @staticmethod
    def _jid_from_phone(jid_phone: str) -> str:
        raw = (jid_phone or "").strip()
        if not raw:
            return ""
        if "@" in raw:
            return raw
        if "-" in raw:
            return f"{raw}@g.us"
        return f"{raw}@s.whatsapp.net"

    def _action_whatsapp_list_chats(self, limit: int = 50) -> str:
        from distr.core.db import WhatsAppMessage

        limit = max(1, min(int(limit or 50), 200))
        wm = self._get_whatsapp_manager()
        if wm:
            try:
                chats = (wm.get_chats(limit=limit, offset=0, search="") or {}).get("chats", [])
                if chats:
                    lines = []
                    for c in chats[:limit]:
                        jid = str(c.get("jid") or "")
                        name = c.get("name") or c.get("subject") or jid.split("@")[0] or "Unknown"
                        lines.append(f"- {name} ({jid})")
                    ref = "WhatsApp chats:\n" + "\n".join(lines)
                    spoken = "I listed WhatsApp chats; names and addresses are below."
                    return voice_then_reference(spoken, ref)
            except Exception as e:
                logger.debug("WhatsApp manager chat list failed, fallback to DB: %s", e)

        with self._get_session() as s:
            rows = (
                s.query(WhatsAppMessage)
                .order_by(WhatsAppMessage.whatsapp_timestamp.desc())
                .limit(2000)
                .all()
            )
        seen = {}
        for m in rows:
            phone = m.jid_phone or (m.jid or "").split("@")[0]
            if not phone or phone in seen:
                continue
            chat_name = m.sender_push_name or m.sender_phone or phone
            if (m.chat_type or "").lower() == "group":
                chat_name = phone
            seen[phone] = {"name": chat_name, "jid": m.jid or self._jid_from_phone(phone), "chat_type": m.chat_type or "private"}
            if len(seen) >= limit:
                break
        if not seen:
            return voice_then_reference(
                "No WhatsApp chats are stored yet.",
                "No WhatsApp chats found.",
            )
        lines = [f"- {v['name']} ({v['jid']}) [{v['chat_type']}]" for v in seen.values()]
        ref = "WhatsApp chats:\n" + "\n".join(lines)
        spoken = "Here are WhatsApp chats from recent stored messages."
        return voice_then_reference(spoken, ref)

    def _action_whatsapp_list_messages(self, jid_phone: str, limit: int = 50, unprocessed_only: bool = False) -> str:
        from distr.core.db import WhatsAppMessage

        phone = (jid_phone or "").strip()
        if not phone:
            return voice_then_reference(
                "I need a chat phone key or JID to read WhatsApp messages.",
                "Please provide jid_phone for whatsapp_list_messages.",
            )
        limit = max(1, min(int(limit or 50), 200))

        wm = self._get_whatsapp_manager()
        if wm:
            try:
                data = wm.get_stored_messages(
                    jid_phone=phone,
                    limit=limit,
                    offset=0,
                    unprocessed_only=bool(unprocessed_only),
                ) or {}
                msgs = data.get("messages") or []
                if not msgs:
                    return voice_then_reference(
                        "That chat has no stored messages yet.",
                        f"No WhatsApp messages found for '{phone}'.",
                    )
                lines = []
                for m in msgs[-limit:]:
                    who = "Me" if m.get("from_me") else (m.get("sender_push_name") or m.get("sender_phone") or "Unknown")
                    body = (m.get("text") or m.get("caption") or f"[{m.get('media_type') or 'message'}]").strip()
                    lines.append(f"- #{m.get('id')} {who}: {body[:180]}")
                ref = f"WhatsApp messages for '{phone}' ({len(msgs)}):\n" + "\n".join(lines)
                spoken = f"I pulled {len(msgs)} messages from that WhatsApp chat."
                return voice_then_reference(spoken, ref)
            except Exception as e:
                logger.debug("WhatsApp manager message list failed, fallback to DB: %s", e)

        with self._get_session() as s:
            query = s.query(WhatsAppMessage).filter(WhatsAppMessage.jid_phone == phone)
            if unprocessed_only:
                query = query.filter(WhatsAppMessage.processed == False)
            rows = query.order_by(WhatsAppMessage.whatsapp_timestamp.asc()).limit(limit).all()
        if not rows:
            return voice_then_reference(
                "That chat has no stored messages yet.",
                f"No WhatsApp messages found for '{phone}'.",
            )
        lines = []
        for m in rows:
            who = "Me" if m.from_me else (m.sender_push_name or m.sender_phone or "Unknown")
            body = (m.text or m.caption or f"[{m.media_type or 'message'}]").strip()
            lines.append(f"- #{m.id} {who}: {body[:180]}")
        ref = f"WhatsApp messages for '{phone}' ({len(rows)}):\n" + "\n".join(lines)
        spoken = f"I pulled {len(rows)} messages from that WhatsApp chat."
        return voice_then_reference(spoken, ref)

    def _action_whatsapp_send_message(self, jid: str, jid_phone: str, text: str) -> str:
        message_text = (text or "").strip()
        target_jid = (jid or "").strip() or self._jid_from_phone(jid_phone)
        if not target_jid:
            return voice_then_reference(
                "I need a chat address or phone number to send WhatsApp.",
                "Please provide jid or jid_phone for whatsapp_send_message.",
            )
        if not message_text:
            return voice_then_reference(
                "Say what message to send on WhatsApp.",
                "Please provide text for whatsapp_send_message.",
            )

        wm = self._get_whatsapp_manager()
        if not wm:
            return voice_then_reference(
                "WhatsApp is not connected in this session.",
                "WhatsApp manager is not available.",
            )
        try:
            import requests
            payload = {"jid": target_jid, "text": message_text, "caption": "", "audio": None}
            payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            headers = wm._relay_auth_headers(payload_str)
            resp = requests.post(f"{wm.api_base}/send", json=payload, headers=headers, timeout=10)
            data = {}
            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text[:300]}
            if resp.status_code == 200 and data.get("success", True):
                return voice_then_reference("WhatsApp message sent.", f"Sent WhatsApp message to {target_jid}.")
            return voice_then_reference(
                "WhatsApp did not accept that send.",
                f"Failed to send WhatsApp message to {target_jid}: {data.get('error') or data}",
            )
        except Exception as e:
            logger.error("WhatsApp send via tool failed: %s", e, exc_info=True)
            return voice_then_reference(
                "WhatsApp send failed.",
                f"Failed to send WhatsApp message: {e}",
            )

    def _action_whatsapp_snapshot_to_ticket(
        self,
        board_id: Optional[int] = None,
        board_name: str = "",
        jid_phone: str = "",
        message_ids: Optional[List[int]] = None,
        title: str = "",
    ) -> str:
        from distr.core.db import WhatsAppMessage
        from distr.core.db.kanban import KanbanLane, KanbanTicket, KanbanTicketFile

        message_ids = message_ids or []
        board = self._find_board(board_id, board_name)
        if not board:
            return voice_then_reference(
                "I need a board name or ID for that WhatsApp snapshot.",
                "Board not found for whatsapp_snapshot_to_ticket. Provide board_id or board_name.",
            )
        lane = self._find_lane(board["id"], "")
        if not lane:
            return voice_then_reference(
                "That board has no lanes yet.",
                f"No lanes found in board '{board['name']}'.",
            )

        with self._get_session() as s:
            query = s.query(WhatsAppMessage)
            if message_ids:
                query = query.filter(WhatsAppMessage.id.in_(message_ids))
            else:
                if not jid_phone:
                    return voice_then_reference(
                        "Pass message IDs or a chat phone key for the snapshot.",
                        "Provide message_ids or jid_phone for whatsapp_snapshot_to_ticket.",
                    )
                query = query.filter(WhatsAppMessage.jid_phone == jid_phone)
            msgs = query.order_by(WhatsAppMessage.whatsapp_timestamp.asc()).all()
            if not msgs:
                return voice_then_reference(
                    "There were no messages to snapshot.",
                    "No WhatsApp messages found for snapshot.",
                )

            lane_obj = orm_get_by_id(s, KanbanLane, lane["id"])
            if not lane_obj:
                return voice_then_reference(
                    "The board lane for that snapshot is missing.",
                    "Lane not found for snapshot ticket.",
                )

            base_phone = msgs[0].jid_phone or jid_phone or "unknown"
            ticket_title = (title or "").strip() or f"[WA Snapshot] {base_phone} ({len(msgs)} messages)"
            lines = [f"WhatsApp snapshot from {base_phone}", ""]
            for m in msgs:
                who = "Me" if m.from_me else (m.sender_push_name or m.sender_phone or "Unknown")
                body = (m.text or m.caption or f"[{m.media_type or 'message'}]").strip()
                lines.append(f"- #{m.id} {who}: {body[:400]}")
            description = "\n".join(lines)

            max_pos = max([t.position for t in lane_obj.tickets], default=-1)
            ticket = KanbanTicket(
                lane_id=lane_obj.id,
                title=ticket_title[:250],
                description=description,
                priority="medium",
                position=max_pos + 1,
                whatsapp_message_id=msgs[-1].id,
                whatsapp_message_wa_id=msgs[-1].message_id,
                source_chat_id=self._source_chat_id_for_new_ticket(),
            )
            s.add(ticket)
            s.flush()

            snapshot_group = f"{ticket.id}_{lane_obj.id}"
            for m in msgs:
                m.processed = True
                m.snapshot_group = snapshot_group
                wa_disk = resolve_whatsapp_media_disk_path(m.media_local_path or "")
                if wa_disk and os.path.exists(wa_disk):
                    safe_name = os.path.basename(wa_disk)
                    s.add(KanbanTicketFile(
                        ticket_id=ticket.id,
                        filename=safe_name,
                        file_path=wa_disk,
                        description=f"WhatsApp {m.media_type or 'media'}: {safe_name}",
                    ))

            created_id = ticket.id
            created_title = ticket.title

        ref = f"Created ticket #{created_id} on board '{board['name']}' from {len(msgs)} WhatsApp messages: {created_title}"
        spoken = f"I turned those WhatsApp messages into a ticket on {board['name']}."
        return voice_then_reference(spoken, ref)
