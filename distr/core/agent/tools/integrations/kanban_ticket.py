"""
Kanban Board Ticket Tool — create, list, and manage tickets on Kanban boards.

Replaces the old CreateCursorTicketTool. Works with the database-backed
KanbanBoard / KanbanLane / KanbanTicket models and supports attaching files
(images, documents, etc.) that were received in the conversation thread
(e.g. from Telegram).
"""
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class KanbanTicketInput(BaseModel):
    """Input schema for KanbanTicketTool."""
    text: str = Field(default="", description="Free-form instruction text (the tool parses board/lane/title from it)")
    action: str = Field(default="create_ticket", description="Action: list_boards, create_board, create_ticket, list_tickets, list_trello_tickets, list_jira_tickets, get_ticket, update_ticket, move_ticket, delete_ticket, attach_file, add_todo, toggle_todo, add_link, send_to_project, send_to_cli, activate_board")
    board_name: str = Field(default="", description="Board name (fuzzy matched)")
    board_id: int = Field(default=0, description="Board ID (exact)")
    lane_name: str = Field(default="", description="Lane name (fuzzy matched, defaults to Backlog)")
    title: str = Field(default="", description="Ticket title")
    description: str = Field(default="", description="Ticket description")
    priority: str = Field(default="medium", description="Priority: low, medium, high, critical")
    ticket_id: int = Field(default=0, description="Ticket ID for get/update/move/delete actions")
    file_path: str = Field(default="", description="File path for attach_file action")
    url: str = Field(default="", description="URL for add_link action")
    todo_text: str = Field(default="", description="Text for add_todo/toggle_todo action")
    linked_project_id: int = Field(default=0, description="Link ticket to a project by ID")
    linked_workflow_id: int = Field(default=0, description="Link ticket to a workflow by ID")
    send_to_cli: bool = Field(default=False, description="If True, send ticket directly to project CLI instead of running a workflow")


class KanbanTicketTool(BaseTool):
    """Create and manage tickets on Kanban boards.

    ACTIONS (pass as the 'action' parameter):
      list_boards        — list all boards
      create_board       — create a new board (requires board_name)
      delete_board       — delete a board (requires board_id or board_name)
      list_lanes         — list lanes for a board (requires board_id or board_name)
      create_ticket      — create a ticket (requires board_name or board_id, plus title)
      list_tickets       — list tickets in a board or lane
      get_ticket         — get ticket details (requires ticket_id)
      update_ticket      — update a ticket (requires ticket_id)
      move_ticket        — move ticket to a different lane (requires ticket_id, lane_name)
      delete_ticket      — delete a ticket (requires ticket_id)
      attach_file        — attach a local file to a ticket (requires ticket_id, file_path)
      delete_file        — remove an attached file (requires ticket_id, file_path as filename)
      add_todo           — add a checklist item (requires ticket_id, text)
      toggle_todo        — toggle a checklist item done/undone (requires ticket_id, todo_text)
      delete_todo        — remove a checklist item (requires ticket_id, todo_text)
      add_link           — add a URL link (requires ticket_id, title, url)
      delete_link        — remove a link (requires ticket_id, url)
      send_to_project    — send ticket to linked project's .tickets folder (requires ticket_id)

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
      ticket_id    — ticket ID for get/update/attach/todo/link actions
      file_path    — local file path for attach_file action
      url          — URL for add_link action
      todo_text    — text for add_todo action

    CONVERSATION CONTEXT:
      When creating a ticket, the tool automatically gathers the recent conversation
      thread (including references to files/images received from Telegram) and uses
      it to build a rich ticket description. If images or documents were mentioned
      or received in the thread, they are attached to the ticket automatically.
    """

    name: str = "create_ticket"
    args_schema: type[BaseModel] = KanbanTicketInput
    description: str = (
        "Full CRUD for Kanban boards and tickets. "
        "Use action='create_ticket' with board_name and title to create a ticket. "
        "Use action='list_boards' to see available boards (local, Trello, and Jira). "
        "Use action='list_trello_tickets' or action='list_jira_tickets' with board_name to read external board tickets. "
        "Use action='create_board' with board_name to create a new board. "
        "Use action='activate_board' with board_name to set a board as the active/default board. "
        "Use action='delete_ticket' with ticket_id to delete a ticket. "
        "Use action='move_ticket' with ticket_id and lane_name to move a ticket. "
        "Use action='attach_file' with ticket_id and file_path to attach files. "
        "Use action='send_to_project' with ticket_id to send ticket to the linked project folder. "
        "Use action='send_to_cli' with ticket_id to send ticket to Kiro CLI for execution. "
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
            "create a kanban ticket", "kanban ticket",
            "add to board", "add to the board",
            "list boards", "show boards", "my boards",
            "list tickets", "show tickets",
        ]

    # ── DB helpers ────────────────────────────────────────────────────────

    def _get_session(self):
        from distr.core.db import get_session
        return get_session()

    def _all_boards(self) -> List[Dict]:
        from distr.core.db.kanban import KanbanBoard
        with self._get_session() as s:
            boards = s.query(KanbanBoard).order_by(KanbanBoard.name).all()
            return [{"id": b.id, "name": b.name, "description": b.description or "",
                     "default_project_id": b.default_project_id} for b in boards]

    def _find_board(self, board_id: Optional[int] = None, board_name: Optional[str] = None) -> Optional[Dict]:
        """Find a board by ID or fuzzy name match. Falls back to the in_use board if nothing specified."""
        from distr.core.db.kanban import KanbanBoard
        with self._get_session() as s:
            if board_id:
                b = s.query(KanbanBoard).get(board_id)
                if b:
                    return {"id": b.id, "name": b.name, "description": b.description or "",
                            "default_project_id": b.default_project_id}
                return None
            if board_name:
                name_lower = board_name.strip().lower()
                # Strip spaces/punctuation for loose comparison
                name_stripped = re.sub(r'[^a-z0-9]', '', name_lower)
                boards = s.query(KanbanBoard).all()
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
                # No board specified — use the in_use board
                b = s.query(KanbanBoard).filter(KanbanBoard.in_use == True).first()
                if b:
                    return {"id": b.id, "name": b.name, "description": b.description or "",
                            "default_project_id": b.default_project_id}
                # Fall back to single board if only one exists
                boards = s.query(KanbanBoard).all()
                if len(boards) == 1:
                    b = boards[0]
                    return {"id": b.id, "name": b.name, "description": b.description or "",
                            "default_project_id": b.default_project_id}
        return None

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
                        return {"title": title, "description": desc}
            except Exception as e:
                logger.warning("LLM summarisation failed, using fallback: %s", e)

        # Fallback
        lines = [l.strip() for l in raw_text.strip().split("\n") if l.strip()]
        title = lines[0][:80] if lines else "New Ticket"
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
        **kwargs,
    ) -> str:
        try:
            action = (action or "create_ticket").strip().lower().replace(" ", "_")
            logger.info("KanbanTicketTool: action=%s board_name=%s board_id=%s title=%s",
                        action, board_name, board_id, title[:50] if title else "")

            if action == "list_boards":
                return self._action_list_boards()
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
                    return f"Please specify a board. Use action='list_boards' to see available {provider} boards."
                tickets = self._fetch_external_tickets(provider, ext_board_id)
                if not tickets:
                    return f"No tickets found on {provider} board {ext_board_id}."
                lines = [f"#{t['id']} {t['title']}" + (f" [{t.get('status', '')}]" if t.get('status') else "") for t in tickets[:30]]
                return f"{provider.title()} board tickets ({len(tickets)}):\n" + "\n".join(lines)
            elif action == "get_ticket":
                return self._action_get_ticket(ticket_id or self._last_ticket_id)
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
            else:
                return (
                    f"Unknown action '{action}'. Valid actions: list_boards, create_board, delete_board, "
                    "activate_board, list_lanes, create_ticket, list_tickets, get_ticket, update_ticket, "
                    "move_ticket, delete_ticket, attach_file, delete_file, add_todo, toggle_todo, "
                    "delete_todo, add_link, delete_link, send_to_project, send_to_cli"
                )

        except Exception as e:
            logger.error("KanbanTicketTool error: %s", e, exc_info=True)
            return f"Error: {e}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)

    # ── Action implementations ────────────────────────────────────────────

    def _action_list_boards(self) -> str:
        boards = self._all_boards()
        lines = []
        for b in boards:
            lines.append(f"Board '{b['name']}' (ID {b['id']}, local)")

        # Also fetch external boards
        try:
            ext = self._fetch_external_boards()
            for b in ext.get("trello", []):
                lines.append(f"Board '{b['name']}' (Trello, ID {b['id']})")
            for b in ext.get("jira", []):
                lines.append(f"Board '{b['name']}' (Jira, ID {b['id']})")
        except Exception as e:
            logger.debug("Could not fetch external boards: %s", e)

        if not lines:
            return "No Kanban boards found. You can create one in the Board UI."
        return "Available boards:\n" + "\n".join(lines)

    def _fetch_external_boards(self) -> Dict:
        """Fetch Trello and Jira boards from connected accounts."""
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

        result = {"trello": [], "jira": []}
        for acct in accounts:
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
                    domain = acct.get("domain") or ""
                    if not domain:
                        server_url = (acct.get("server_url") or "").strip().rstrip("/")
                        if server_url:
                            domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
                    if domain:
                        resp = requests.get(
                            f"https://{domain}/rest/agile/1.0/board",
                            auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                            headers={"Accept": "application/json"}, timeout=10,
                        )
                        if resp.status_code == 200:
                            for b in resp.json().get("values", []):
                                result["jira"].append({"id": str(b["id"]), "name": b["name"]})
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
            return "Board not found. Use action='list_boards' to see available boards."
        lanes = self._get_lanes(board["id"])
        names = [l["name"] for l in lanes]
        return f"Lanes in '{board['name']}': {', '.join(names)}"

    def _action_create_ticket(self, text="", board_name="", board_id=None,
                               lane_name="", title="", description="",
                               priority="medium",
                               linked_project_id=None, linked_workflow_id=None,
                               send_to_cli=False) -> str:
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
                    return f"There are multiple boards. Please specify which one: {board_list}"
                else:
                    return "No boards found. Create one first with action='create_board'."

        # Resolve lane
        lane = self._find_lane(board["id"], lane_name)
        if not lane:
            return f"No lanes found in board '{board['name']}'."

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

        # Create the ticket in DB
        from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard as KB
        with self._get_session() as s:
            lane_obj = s.query(KanbanLane).get(lane["id"])
            max_pos = max([t.position for t in lane_obj.tickets], default=-1) if lane_obj else -1

            # Check if board has a default project
            board_obj = s.query(KB).get(board["id"])
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
        logger.info("KanbanTicketTool: %s", result)
        return result

    def _create_bulk_tickets(self, board, lane, tickets_data, conv_files,
                              linked_project_id, linked_workflow_id):
        """Create multiple tickets from a list of {title, description, priority} dicts."""
        from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard as KB

        created = []
        with self._get_session() as s:
            lane_obj = s.query(KanbanLane).get(lane["id"])
            max_pos = max([t.position for t in lane_obj.tickets], default=-1) if lane_obj else -1
            board_obj = s.query(KB).get(board["id"])
            effective_project_id = linked_project_id or (board_obj.default_project_id if board_obj else None)
            effective_workflow_id = linked_workflow_id or None
            effective_send_to_cli = board_obj.send_to_cli if board_obj else False
            if effective_send_to_cli:
                effective_workflow_id = None

            for item in tickets_data:
                if not isinstance(item, dict):
                    continue
                t_title = (item.get("title") or "Untitled")[:200]
                t_desc = (item.get("description") or "")[:2000]
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
        return f"Created {len(created)} ticket(s) in board '{board['name']}', lane '{lane['name']}':\n" + "\n".join(titles)

    def _action_list_tickets(self, board_id=None, board_name=None, lane_name=None) -> str:
        board = self._find_board(board_id, board_name)
        if not board:
            return "Board not found."
        from distr.core.db.kanban import KanbanTicket, KanbanLane
        with self._get_session() as s:
            query = s.query(KanbanTicket).join(KanbanLane).filter(KanbanLane.board_id == board["id"])
            if lane_name:
                query = query.filter(KanbanLane.name.ilike(f"%{lane_name}%"))
            tickets = query.order_by(KanbanLane.position, KanbanTicket.position).all()
            if not tickets:
                return f"No tickets in board '{board['name']}'."
            lines = []
            for t in tickets:
                lane_n = t.lane.name if t.lane else "?"
                files_count = len(t.files) if t.files else 0
                extra = f" ({files_count} files)" if files_count else ""
                lines.append(f"[{lane_n}] #{t.id} {t.title} ({t.priority}){extra}")
            return f"Tickets in '{board['name']}':\n" + "\n".join(lines)

    def _action_get_ticket(self, ticket_id) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        from distr.core.db.kanban import KanbanTicket
        with self._get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                return f"Ticket #{ticket_id} not found."
            files = [f.filename for f in t.files] if t.files else []
            todos = [f"{'[x]' if td.done else '[ ]'} {td.text}" for td in t.todos] if t.todos else []
            links = [f"{l.title}: {l.url}" for l in t.links] if t.links else []
            parts = [
                f"Ticket #{t.id}: {t.title}",
                f"Lane: {t.lane.name if t.lane else '?'}",
                f"Priority: {t.priority}",
                f"Description: {t.description or '(none)'}",
                f"Send to CLI: {'Yes' if t.send_to_cli else 'No'}",
            ]
            if files:
                parts.append(f"Files: {', '.join(files)}")
            if todos:
                parts.append(f"Todos: {'; '.join(todos)}")
            if links:
                parts.append(f"Links: {'; '.join(links)}")
            return "\n".join(parts)

    def _action_update_ticket(self, ticket_id, title="", description="",
                               priority="", lane_name="",
                               linked_project_id=None, linked_workflow_id=None,
                               send_to_cli=False) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        from distr.core.db.kanban import KanbanTicket, KanbanLane
        with self._get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                return f"Ticket #{ticket_id} not found."
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
            return f"Updated ticket #{ticket_id}"

    def _action_attach_file(self, ticket_id, file_path) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        if not file_path or not os.path.isfile(file_path):
            return f"File not found: {file_path}"
        name = self._attach_file_to_ticket(ticket_id, file_path)
        if name:
            return f"Attached '{name}' to ticket #{ticket_id}"
        return "Failed to attach file."

    def _action_add_todo(self, ticket_id, text) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        if not text:
            return "No todo text provided."
        from distr.core.db.kanban import KanbanTicket, KanbanTicketTodo
        with self._get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                return f"Ticket #{ticket_id} not found."
            max_pos = max([td.position for td in t.todos], default=-1) if t.todos else -1
            todo = KanbanTicketTodo(ticket_id=ticket_id, text=text, position=max_pos + 1)
            s.add(todo)
        return f"Added todo to ticket #{ticket_id}"

    def _action_add_link(self, ticket_id, title, url) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        if not url:
            return "No URL provided."
        from distr.core.db.kanban import KanbanTicket, KanbanTicketLink
        with self._get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                return f"Ticket #{ticket_id} not found."
            link = KanbanTicketLink(ticket_id=ticket_id, title=title or url, url=url)
            s.add(link)
        return f"Added link to ticket #{ticket_id}"

    # ── Board CRUD ────────────────────────────────────────────────────────

    def _action_create_board(self, name: str) -> str:
        if not name or not name.strip():
            return "Board name is required."
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
        return f"Created board '{name.strip()}' (ID {board_id}) with lanes: {', '.join(default_lanes)}"

    def _action_delete_board(self, board_id=None, board_name=None) -> str:
        board = self._find_board(board_id, board_name)
        if not board:
            return "Board not found."
        from distr.core.db.kanban import KanbanBoard
        with self._get_session() as s:
            b = s.query(KanbanBoard).get(board["id"])
            if not b:
                return "Board not found."
            name = b.name
            s.delete(b)
        return f"Deleted board '{name}' and all its tickets"

    # ── Ticket delete & move ──────────────────────────────────────────────

    def _action_delete_ticket(self, ticket_id) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        from distr.core.db.kanban import KanbanTicket
        with self._get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                return f"Ticket #{ticket_id} not found."
            title = t.title
            s.delete(t)
        return f"Deleted ticket #{ticket_id} ('{title}')"

    def _action_move_ticket(self, ticket_id, lane_name) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        if not lane_name:
            return "No lane name provided."
        from distr.core.db.kanban import KanbanTicket, KanbanLane
        with self._get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                return f"Ticket #{ticket_id} not found."
            board_id = t.lane.board_id if t.lane else None
            if not board_id:
                return "Cannot determine board for this ticket."
            new_lane = s.query(KanbanLane).filter(
                KanbanLane.board_id == board_id,
                KanbanLane.name.ilike(f"%{lane_name}%")
            ).first()
            if not new_lane:
                lanes = s.query(KanbanLane).filter_by(board_id=board_id).order_by(KanbanLane.position).all()
                available = ", ".join(l.name for l in lanes)
                return f"Lane '{lane_name}' not found. Available: {available}"
            max_pos = max([tk.position for tk in new_lane.tickets], default=-1)
            t.lane_id = new_lane.id
            t.position = max_pos + 1
            moved_lane_name = new_lane.name
        return f"Moved ticket #{ticket_id} to lane '{moved_lane_name}'"

    # ── Sub-resource deletes ──────────────────────────────────────────────

    def _action_delete_file(self, ticket_id, filename) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        if not filename:
            return "No filename provided."
        from distr.core.db.kanban import KanbanTicketFile
        with self._get_session() as s:
            f = s.query(KanbanTicketFile).filter_by(ticket_id=ticket_id).filter(
                KanbanTicketFile.filename.ilike(f"%{filename}%")
            ).first()
            if not f:
                return f"File '{filename}' not found on ticket #{ticket_id}."
            name = f.filename
            # Remove physical file
            try:
                if os.path.exists(f.file_path):
                    os.remove(f.file_path)
            except Exception:
                pass
            s.delete(f)
        return f"Removed file '{name}' from ticket #{ticket_id}"

    def _action_toggle_todo(self, ticket_id, todo_text) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        if not todo_text:
            return "No todo text provided."
        from distr.core.db.kanban import KanbanTicketTodo
        with self._get_session() as s:
            todo = s.query(KanbanTicketTodo).filter_by(ticket_id=ticket_id).filter(
                KanbanTicketTodo.text.ilike(f"%{todo_text}%")
            ).first()
            if not todo:
                return f"Todo matching '{todo_text}' not found on ticket #{ticket_id}."
            todo.done = not todo.done
            status = "done" if todo.done else "not done"
        return f"Toggled todo to {status} on ticket #{ticket_id}"

    def _action_delete_todo(self, ticket_id, todo_text) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        if not todo_text:
            return "No todo text provided."
        from distr.core.db.kanban import KanbanTicketTodo
        with self._get_session() as s:
            todo = s.query(KanbanTicketTodo).filter_by(ticket_id=ticket_id).filter(
                KanbanTicketTodo.text.ilike(f"%{todo_text}%")
            ).first()
            if not todo:
                return f"Todo matching '{todo_text}' not found on ticket #{ticket_id}."
            s.delete(todo)
        return f"Removed todo from ticket #{ticket_id}"

    def _action_delete_link(self, ticket_id, url) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        if not url:
            return "No URL provided."
        from distr.core.db.kanban import KanbanTicketLink
        with self._get_session() as s:
            link = s.query(KanbanTicketLink).filter_by(ticket_id=ticket_id).filter(
                KanbanTicketLink.url.ilike(f"%{url}%")
            ).first()
            if not link:
                return f"Link matching '{url}' not found on ticket #{ticket_id}."
            s.delete(link)
        return f"Removed link from ticket #{ticket_id}"

    # ── Send to project ───────────────────────────────────────────────────

    def _action_send_to_project(self, ticket_id) -> str:
        if not ticket_id:
            return "No ticket ID provided."
        from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard as KB
        from distr.core.db.projects import Project

        with self._get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                return f"Ticket #{ticket_id} not found."

            # Resolve project: ticket-level first, then board-level default
            project_id = t.linked_project_id
            if not project_id and t.lane:
                board = s.query(KB).get(t.lane.board_id)
                if board:
                    project_id = board.default_project_id

            if not project_id:
                return "No project linked to this ticket or its board."

            project = s.query(Project).get(project_id)
            if not project:
                return "Linked project not found."
            if not project.folder_location:
                return f"Project '{project.name}' has no folder location set."

            tickets_folder = os.path.join(project.folder_location, ".tickets")
            os.makedirs(tickets_folder, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ticket_path = os.path.join(tickets_folder, f"ticket_{timestamp}.md")

            # Build markdown
            todos_md = ""
            if t.todos:
                todos_md = "\n## Checklist\n"
                for td in t.todos:
                    mark = "x" if td.done else " "
                    todos_md += f"- [{mark}] {td.text}\n"

            links_md = ""
            if t.links:
                links_md = "\n## Links\n"
                for lk in t.links:
                    links_md += f"- [{lk.title}]({lk.url})\n"

            files_md = ""
            if t.files:
                files_md = "\n## Attached Files\n"
                for fl in t.files:
                    files_md += f"- {fl.filename} (`{fl.file_path}`)\n"

            content = (
                f"---\nid: ticket_{timestamp}\ntitle: {t.title}\n"
                f"project: {project.name}\ncreated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"priority: {t.priority or 'medium'}\nstatus: open\n"
                f"source: kanban_ticket_{t.id}\n---\n\n"
                f"## Description\n{t.description or '(no description)'}\n"
                f"{todos_md}{links_md}{files_md}\n"
                f"---\n*Sent from Kanban board via DecisionsAI*\n"
            )

            with open(ticket_path, "w", encoding="utf-8") as f:
                f.write(content)

            project_name = project.name

        return f"Sent ticket #{ticket_id} to project '{project_name}' → {ticket_path}"

    # ── Activate board ────────────────────────────────────────────────────

    def _action_activate_board(self, board_id=None, board_name=None) -> str:
        """Set a board as the active/in-use board. Future ticket commands default to this board."""
        board = self._find_board(board_id, board_name)
        if not board:
            return f"Board '{board_name or board_id}' not found."

        from distr.core.db.kanban import KanbanBoard as KB
        with self._get_session() as s:
            # Deactivate all boards
            s.query(KB).filter(KB.in_use == True).update({"in_use": False})
            b = s.query(KB).get(board["id"])
            if not b:
                return f"Board '{board['name']}' not found."
            b.in_use = True
            s.commit()

        self._last_board_id = board["id"]
        return f"Board '{board['name']}' is now your active board. All ticket commands will default to this board."

    # ── Send ticket to CLI ────────────────────────────────────────────────

    def _action_send_to_cli(self, ticket_id) -> str:
        """Send a ticket's instruction to Kiro CLI for the linked project. Creates an audit trail."""
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
                return "No ticket ID provided and no tickets found on the active board."

        import shutil
        import subprocess

        from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard as KB
        from distr.core.db.projects import Project

        kiro_path = shutil.which("kiro-cli")
        if not kiro_path:
            return "Kiro CLI is not installed. Install it with: curl -fsSL https://cli.kiro.dev/install | bash"

        with self._get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                return f"Ticket #{ticket_id} not found."

            title = t.title
            description = t.description or ""
            ticket_id_val = t.id

            # Resolve project
            project_id = t.linked_project_id
            if not project_id and t.lane:
                board = s.query(KB).get(t.lane.board_id)
                if board:
                    project_id = board.default_project_id

            if not project_id:
                return "No project linked to this ticket or its board. Link a project first."

            project = s.query(Project).get(project_id)
            if not project:
                return "Linked project not found."
            if not project.folder_location:
                return f"Project '{project.name}' has no folder location set."

            folder = project.folder_location
            project_name = project.name

        instruction = f"{title}\n\n{description}".strip() if description else title

        # Create audit trail
        audit_id = None
        step_id = None
        try:
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
            with self._get_session() as s:
                audit = AutoWorkflow(
                    name=f"[Project: {project_name}] Ticket #{ticket_id_val}: {title}",
                    status="in_progress",
                    workflow_type="kiro_cli",
                )
                s.add(audit)
                s.flush()
                step = AutoWorkflowStep(
                    workflow_id=audit.id, position=0,
                    name=f"Ticket #{ticket_id_val}", instruction=instruction[:500],
                    status="running", tool_used="kiro-cli",
                )
                s.add(step)
                s.commit()
                audit_id = audit.id
                step_id = step.id
        except Exception as e:
            logger.debug("Could not create audit for send_to_cli: %s", e)

        if self.event_queue:
            try:
                self.event_queue.put(("step_runner_updated", {}), block=False)
            except Exception:
                pass

        # Execute Kiro CLI
        try:
            result = subprocess.run(
                [kiro_path, "chat", "--no-interactive", "--trust-all-tools", instruction],
                capture_output=True, text=True, timeout=600,
                cwd=folder,
            )
            output = (result.stdout + result.stderr).strip()[:3000]
            status = "completed" if result.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            output = "Kiro CLI timed out after 10 minutes"
            status = "failed"
        except Exception as e:
            output = f"Kiro CLI error: {e}"
            status = "failed"

        # Update audit trail (legacy StepRunner — removed in task 6.3)
        if audit_id and step_id:
            pass

        if self.event_queue:
            try:
                self.event_queue.put(("step_runner_updated", {}), block=False)
            except Exception:
                pass

        if not output:
            return f"Kiro CLI completed for ticket #{ticket_id_val} (exit code: {result.returncode})"

        preview = output[:500] + "..." if len(output) > 500 else output
        return f"[Kiro CLI — {project_name}] Ticket #{ticket_id_val}:\n{preview}"
