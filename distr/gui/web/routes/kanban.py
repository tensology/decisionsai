"""
API routes for Kanban board management.
"""
from fastapi import APIRouter, HTTPException, File, UploadFile, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import json
import logging
import os
import threading

from distr.core.paths import DB_DIR
from distr.core.db import get_session
from distr.core.db.kanban import (
    KanbanBoard, KanbanLane, KanbanTicket,
    KanbanTicketFile, KanbanTicketLink, KanbanTicketTodo,
)
from distr.core.kanban.agent import _active_agents, KanbanAgentCheckIn
from distr.core.settings import load_settings_from_db, save_settings_to_db

logger = logging.getLogger(__name__)

KANBAN_UPLOADS_DIR = os.path.join(DB_DIR, "kanban_uploads")
DEFAULT_LANES = ["Backlog", "Current", "QA / Assess", "Done"]


class BoardCreate(BaseModel):
    name: str
    description: Optional[str] = ""

class BoardUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_workflow_id: Optional[int] = None
    default_project_id: Optional[int] = None
    default_snippet_id: Optional[int] = None
    default_action_id: Optional[int] = None
    send_to_cli: Optional[bool] = None
    color: Optional[str] = None
    position: Optional[int] = None
    agent_enabled: Optional[bool] = None

class TicketCreate(BaseModel):
    lane_id: int
    title: str
    description: Optional[str] = ""
    priority: Optional[str] = "medium"

class TicketUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    lane_id: Optional[int] = None
    position: Optional[int] = None
    linked_workflow_id: Optional[int] = None
    linked_project_id: Optional[int] = None
    linked_snippet_id: Optional[int] = None
    linked_action_id: Optional[int] = None
    send_to_cli: Optional[bool] = None

class TicketMove(BaseModel):
    lane_id: int
    position: int

class LinkCreate(BaseModel):
    title: str
    url: str

class TodoCreate(BaseModel):
    text: str

class TodoUpdate(BaseModel):
    text: Optional[str] = None
    done: Optional[bool] = None

class CopyToBoard(BaseModel):
    board_id: int
    title: str
    description: Optional[str] = ""
    priority: Optional[str] = "medium"


ALLOWED_FREQUENCIES = {"5min", "10min", "15min", "30min", "hourly", "daily", "weekly", "fortnightly", "monthly"}


class KanbanSettingsUpdate(BaseModel):
    kanban_agent_enabled: Optional[bool] = None
    kanban_agent_frequency: Optional[str] = None
    kanban_agent_time: Optional[str] = None
    kanban_agent_hours: Optional[List[int]] = None
    kanban_agent_days: Optional[List[int]] = None
    kanban_agent_monthly_day: Optional[int] = None
    kanban_agent_source_lane: Optional[str] = None
    kanban_agent_done_lane: Optional[str] = None
    kanban_agent_orchestrator_provider: Optional[str] = None
    kanban_agent_orchestrator_model: Optional[str] = None
    kanban_agent_coder_provider: Optional[str] = None
    kanban_agent_coder_model: Optional[str] = None
    kanban_agent_sub_provider: Optional[str] = None
    kanban_agent_sub_model: Optional[str] = None
    kanban_cli_tool: Optional[str] = None
    kanban_cli_auth: Optional[str] = None


def create_routes():
    router = APIRouter()

    # ── Global Kanban Settings ──

    @router.get("/kanban/settings")
    async def get_kanban_settings():
        """Return all kanban-prefixed global settings."""
        settings = load_settings_from_db()
        kanban_settings = {k: v for k, v in settings.items() if k.startswith("kanban_")}
        # Parse JSON-encoded list fields for the response
        for list_key in ("kanban_agent_hours", "kanban_agent_days"):
            val = kanban_settings.get(list_key)
            if isinstance(val, str):
                try:
                    kanban_settings[list_key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    kanban_settings[list_key] = []
        return JSONResponse(kanban_settings)

    @router.put("/kanban/settings")
    async def update_kanban_settings(payload: KanbanSettingsUpdate):
        """Update global kanban settings with validation."""
        data = payload.model_dump(exclude_none=True)

        # Determine effective frequency: use provided value or fall back to current
        effective_frequency = data.get("kanban_agent_frequency")
        if effective_frequency is None:
            current = load_settings_from_db()
            effective_frequency = current.get("kanban_agent_frequency", "daily")

        # Validate frequency
        if "kanban_agent_frequency" in data:
            if data["kanban_agent_frequency"] not in ALLOWED_FREQUENCIES:
                raise HTTPException(422, f"Invalid frequency: must be one of {sorted(ALLOWED_FREQUENCIES)}")

        # Validate hours
        if "kanban_agent_hours" in data:
            hours = data["kanban_agent_hours"]
            if not all(isinstance(h, int) and 0 <= h <= 23 for h in hours):
                raise HTTPException(422, "Invalid kanban_agent_hours: all values must be integers in [0, 23]")
            # Deduplicate
            data["kanban_agent_hours"] = sorted(set(hours))

        # Validate days
        if "kanban_agent_days" in data:
            days = data["kanban_agent_days"]
            if not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
                raise HTTPException(422, "Invalid kanban_agent_days: all values must be integers in [0, 6]")

        # Validate monthly_day
        if "kanban_agent_monthly_day" in data:
            md = data["kanban_agent_monthly_day"]
            if not (isinstance(md, int) and 1 <= md <= 28):
                raise HTTPException(422, "Invalid kanban_agent_monthly_day: must be an integer in [1, 28]")

        # Load current settings, apply updates, save
        settings = load_settings_from_db()
        for key, value in data.items():
            # Convert list fields to JSON strings for storage
            if key in ("kanban_agent_hours", "kanban_agent_days"):
                settings[key] = json.dumps(value)
            else:
                settings[key] = value
        save_settings_to_db(settings)
        return JSONResponse({"success": True})

    # ── Agent check-in ──

    @router.post("/kanban/agent/checkin")
    async def manual_agent_checkin():
        """Manually trigger an agent check-in on all boards with agent_enabled=true."""
        import threading
        from distr.core.db.kanban import KanbanBoard
        from distr.core.kanban.agent import KanbanAgentCheckIn

        fired = 0
        with get_session() as s:
            boards = s.query(KanbanBoard).filter(KanbanBoard.agent_enabled == True).all()
            board_ids = [b.id for b in boards]

        for bid in board_ids:
            agent = KanbanAgentCheckIn(bid)
            threading.Thread(target=agent.run, daemon=True).start()
            fired += 1

        if fired == 0:
            return JSONResponse({"message": "No boards have agent check-in enabled."})
        return JSONResponse({"message": f"Agent check-in started for {fired} board(s)."})

    @router.post("/kanban/boards/{board_id}/use")
    async def set_board_in_use(board_id: int):
        """Set this board as the active/in-use board. Only one board can be in_use at a time.
        If the board has a linked project, returns a prompt to activate it."""
        with get_session() as s:
            # Deactivate all boards
            s.query(KanbanBoard).filter(KanbanBoard.in_use == True).update({"in_use": False})
            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            board.in_use = True
            s.flush()

            # Check if board has a linked project
            linked_project = None
            if board.default_project_id:
                from distr.core.db.projects import Project
                proj = s.query(Project).get(board.default_project_id)
                if proj and not proj.in_use:
                    linked_project = {"id": proj.id, "name": proj.name}

            return JSONResponse({
                "success": True,
                "linked_project": linked_project,
            })

    # ── Boards ──

    @router.get("/kanban/boards")
    async def list_boards(include_archived: bool = False):
        with get_session() as s:
            query = s.query(KanbanBoard)
            if not include_archived:
                query = query.filter((KanbanBoard.archived == False) | (KanbanBoard.archived == None))
            boards = query.order_by(KanbanBoard.position, KanbanBoard.name).all()
            result = []
            for b in boards:
                result.append({
                    "id": b.id, "name": b.name, "description": b.description or "",
                    "source": b.source, "external_board_id": b.external_board_id,
                    "external_url": b.external_url, "color": b.color or "",
                    "position": b.position or 0,
                    "archived": getattr(b, 'archived', False) or False,
                    "agent_enabled": getattr(b, 'agent_enabled', False) or False,
                    "in_use": getattr(b, 'in_use', False) or False,
                })
            return JSONResponse(result)

    @router.post("/kanban/boards")
    async def create_board(payload: BoardCreate):
        with get_session() as s:
            board = KanbanBoard(name=payload.name, description=payload.description or "", source="database")
            s.add(board)
            s.flush()
            for i, lane_name in enumerate(DEFAULT_LANES):
                s.add(KanbanLane(board_id=board.id, name=lane_name, position=i))
            s.flush()
            return JSONResponse({"success": True, "id": board.id})

    @router.put("/kanban/boards/{board_id}")
    async def update_board(board_id: int, payload: BoardUpdate):
        with get_session() as s:
            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            if payload.name is not None:
                board.name = payload.name
            if payload.description is not None:
                board.description = payload.description
            if payload.default_workflow_id is not None:
                board.default_workflow_id = payload.default_workflow_id if payload.default_workflow_id else None
            if payload.default_project_id is not None:
                board.default_project_id = payload.default_project_id if payload.default_project_id else None
            if payload.default_snippet_id is not None:
                board.default_snippet_id = payload.default_snippet_id if payload.default_snippet_id else None
            if payload.default_action_id is not None:
                board.default_action_id = payload.default_action_id if payload.default_action_id else None
            if payload.send_to_cli is not None:
                board.send_to_cli = payload.send_to_cli
            if payload.color is not None:
                board.color = payload.color if payload.color else None
            if payload.position is not None:
                board.position = payload.position
            if payload.agent_enabled is not None:
                board.agent_enabled = payload.agent_enabled
            return JSONResponse({"success": True})

    @router.delete("/kanban/boards/{board_id}")
    async def delete_board(board_id: int):
        with get_session() as s:
            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            s.delete(board)
            return JSONResponse({"success": True})

    @router.post("/kanban/boards/{board_id}/archive")
    async def archive_board(board_id: int):
        with get_session() as s:
            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            board.archived = True
            return JSONResponse({"success": True})

    @router.post("/kanban/boards/{board_id}/unarchive")
    async def unarchive_board(board_id: int):
        with get_session() as s:
            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            board.archived = False
            return JSONResponse({"success": True})

    @router.post("/kanban/boards/reorder")
    async def reorder_boards(payload: dict):
        """Reorder boards. Expects {"order": [id1, id2, ...]}"""
        order = payload.get("order", [])
        if not order:
            return JSONResponse({"success": True})
        with get_session() as s:
            for pos, board_id in enumerate(order):
                board = s.query(KanbanBoard).get(board_id)
                if board:
                    board.position = pos
            return JSONResponse({"success": True})

    @router.get("/kanban/boards/{board_id}")
    async def get_board(board_id: int):
        with get_session() as s:
            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            # Get WhatsApp links for this board
            from distr.core.db import WhatsAppPhoneLink
            whatsapp_links = s.query(WhatsAppPhoneLink).filter_by(board_id=board_id).all()
            lanes = []
            for lane in board.lanes:
                tickets = []
                for t in lane.tickets:
                    tickets.append({
                        "id": t.id, "title": t.title, "description": t.description or "",
                        "priority": t.priority or "medium", "position": t.position,
                        "external_source": t.external_source, "external_id": t.external_id,
                        "external_url": t.external_url,
                        "linked_workflow_id": t.linked_workflow_id,
                        "linked_project_id": t.linked_project_id,
                        "linked_snippet_id": t.linked_snippet_id,
                        "linked_action_id": t.linked_action_id,
                        "files": [{"id": f.id, "filename": f.filename, "description": f.description or ""} for f in t.files],
                        "links": [{"id": l.id, "title": l.title, "url": l.url} for l in t.links],
                        "todos": [{"id": td.id, "text": td.text, "done": td.done, "position": td.position} for td in t.todos],
                        "whatsapp_message_id": t.whatsapp_message_id,
                        "whatsapp_message_wa_id": t.whatsapp_message_wa_id,
                    })
                lanes.append({"id": lane.id, "name": lane.name, "position": lane.position, "tickets": tickets})
            return JSONResponse({
                "id": board.id, "name": board.name, "description": board.description or "",
                "source": board.source, "external_board_id": board.external_board_id,
                "external_url": board.external_url, "lanes": lanes,
                "default_workflow_id": board.default_workflow_id,
                "default_project_id": board.default_project_id,
                "default_snippet_id": board.default_snippet_id,
                "default_action_id": board.default_action_id,
                "send_to_cli": getattr(board, 'send_to_cli', False) or False,
                "color": board.color or "",
                "agent_enabled": getattr(board, 'agent_enabled', False) or False,
                "in_use": getattr(board, 'in_use', False) or False,
                "whatsapp_links": [{"id": l.id, "phone_number": l.phone_number, "contact_name": l.contact_name, "auto_snapshot": l.auto_snapshot or False} for l in whatsapp_links],
            })

    # ── Tickets ──

    @router.post("/kanban/tickets")
    async def create_ticket(payload: TicketCreate):
        with get_session() as s:
            lane = s.query(KanbanLane).get(payload.lane_id)
            if not lane:
                raise HTTPException(404, "Lane not found")
            # Get board defaults for new tickets
            board = s.query(KanbanBoard).get(lane.board_id)
            max_pos = max([t.position for t in lane.tickets], default=-1)
            ticket = KanbanTicket(
                lane_id=payload.lane_id, title=payload.title,
                description=payload.description or "", priority=payload.priority or "medium",
                position=max_pos + 1,
                linked_workflow_id=board.default_workflow_id if board else None,
                linked_project_id=board.default_project_id if board else None,
                linked_snippet_id=board.default_snippet_id if board else None,
                linked_action_id=board.default_action_id if board else None,
                send_to_cli=board.send_to_cli if board else False,
            )
            s.add(ticket)
            s.flush()
            return JSONResponse({"success": True, "id": ticket.id})

    @router.get("/kanban/tickets/{ticket_id}")
    async def get_ticket(ticket_id: int):
        with get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            return JSONResponse({
                "id": t.id, "lane_id": t.lane_id, "title": t.title,
                "description": t.description or "", "priority": t.priority or "medium",
                "position": t.position,
                "external_source": t.external_source, "external_id": t.external_id,
                "external_url": t.external_url,
                "linked_workflow_id": t.linked_workflow_id,
                "linked_project_id": t.linked_project_id,
                "linked_snippet_id": t.linked_snippet_id,
                "linked_action_id": t.linked_action_id,
                "send_to_cli": t.send_to_cli or False,
                "whatsapp_message_id": t.whatsapp_message_id,
                "whatsapp_message_wa_id": t.whatsapp_message_wa_id,
                "files": [{"id": f.id, "filename": f.filename, "description": f.description or ""} for f in t.files],
                "links": [{"id": l.id, "title": l.title, "url": l.url} for l in t.links],
                "todos": [{"id": td.id, "text": td.text, "done": td.done, "position": td.position} for td in t.todos],
            })

    @router.put("/kanban/tickets/{ticket_id}")
    async def update_ticket(ticket_id: int, payload: TicketUpdate):
        with get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            if payload.title is not None:
                t.title = payload.title
            if payload.description is not None:
                t.description = payload.description
            if payload.priority is not None:
                t.priority = payload.priority
            if payload.linked_workflow_id is not None:
                t.linked_workflow_id = payload.linked_workflow_id
            if payload.linked_project_id is not None:
                t.linked_project_id = payload.linked_project_id
            if payload.linked_snippet_id is not None:
                t.linked_snippet_id = payload.linked_snippet_id
            if payload.linked_action_id is not None:
                t.linked_action_id = payload.linked_action_id
            if payload.send_to_cli is not None:
                t.send_to_cli = payload.send_to_cli
                if payload.send_to_cli:
                    t.linked_workflow_id = None  # CLI and workflow are mutually exclusive
            if payload.lane_id is not None:
                t.lane_id = payload.lane_id
            if payload.position is not None:
                t.position = payload.position
            return JSONResponse({"success": True})

    @router.put("/kanban/tickets/{ticket_id}/move")
    async def move_ticket(ticket_id: int, payload: TicketMove):
        with get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            lane = s.query(KanbanLane).get(payload.lane_id)
            if not lane:
                raise HTTPException(404, "Lane not found")
            t.lane_id = payload.lane_id
            t.position = payload.position
            # Reorder siblings
            siblings = s.query(KanbanTicket).filter(
                KanbanTicket.lane_id == payload.lane_id,
                KanbanTicket.id != ticket_id
            ).order_by(KanbanTicket.position).all()
            for i, sib in enumerate(siblings):
                new_pos = i if i < payload.position else i + 1
                sib.position = new_pos
            return JSONResponse({"success": True})

    @router.delete("/kanban/tickets/{ticket_id}")
    async def delete_ticket(ticket_id: int):
        with get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            s.delete(t)
            return JSONResponse({"success": True})

    # ── Ticket Files ──

    @router.post("/kanban/tickets/{ticket_id}/files")
    async def upload_ticket_file(ticket_id: int, file: UploadFile = File(...)):
        with get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            upload_dir = os.path.join(KANBAN_UPLOADS_DIR, str(ticket_id))
            os.makedirs(upload_dir, exist_ok=True)
            safe_name = os.path.basename(file.filename or "file")
            dest = os.path.join(upload_dir, safe_name)
            content = await file.read()
            with open(dest, "wb") as f:
                f.write(content)
            rec = KanbanTicketFile(ticket_id=ticket_id, filename=safe_name, file_path=dest)
            s.add(rec)
            s.flush()
            return JSONResponse({"success": True, "id": rec.id, "filename": safe_name})

    @router.delete("/kanban/tickets/{ticket_id}/files/{file_id}")
    async def delete_ticket_file(ticket_id: int, file_id: int):
        with get_session() as s:
            f = s.query(KanbanTicketFile).filter_by(id=file_id, ticket_id=ticket_id).first()
            if not f:
                raise HTTPException(404, "File not found")
            try:
                if os.path.exists(f.file_path):
                    os.remove(f.file_path)
            except Exception:
                pass
            s.delete(f)
            return JSONResponse({"success": True})

    # ── Ticket Links ──

    @router.post("/kanban/tickets/{ticket_id}/links")
    async def add_ticket_link(ticket_id: int, payload: LinkCreate):
        with get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            link = KanbanTicketLink(ticket_id=ticket_id, title=payload.title, url=payload.url)
            s.add(link)
            s.flush()
            return JSONResponse({"success": True, "id": link.id})

    @router.delete("/kanban/tickets/{ticket_id}/links/{link_id}")
    async def delete_ticket_link(ticket_id: int, link_id: int):
        with get_session() as s:
            link = s.query(KanbanTicketLink).filter_by(id=link_id, ticket_id=ticket_id).first()
            if not link:
                raise HTTPException(404, "Link not found")
            s.delete(link)
            return JSONResponse({"success": True})

    # ── Ticket Todos ──

    @router.post("/kanban/tickets/{ticket_id}/todos")
    async def add_ticket_todo(ticket_id: int, payload: TodoCreate):
        with get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            max_pos = max([td.position for td in t.todos], default=-1)
            todo = KanbanTicketTodo(ticket_id=ticket_id, text=payload.text, position=max_pos + 1)
            s.add(todo)
            s.flush()
            return JSONResponse({"success": True, "id": todo.id})

    @router.put("/kanban/tickets/{ticket_id}/todos/{todo_id}")
    async def update_ticket_todo(ticket_id: int, todo_id: int, payload: TodoUpdate):
        with get_session() as s:
            todo = s.query(KanbanTicketTodo).filter_by(id=todo_id, ticket_id=ticket_id).first()
            if not todo:
                raise HTTPException(404, "Todo not found")
            if payload.text is not None:
                todo.text = payload.text
            if payload.done is not None:
                todo.done = payload.done
            return JSONResponse({"success": True})

    @router.delete("/kanban/tickets/{ticket_id}/todos/{todo_id}")
    async def delete_ticket_todo(ticket_id: int, todo_id: int):
        with get_session() as s:
            todo = s.query(KanbanTicketTodo).filter_by(id=todo_id, ticket_id=ticket_id).first()
            if not todo:
                raise HTTPException(404, "Todo not found")
            s.delete(todo)
            return JSONResponse({"success": True})

    # ── Copy external ticket to local board ──

    @router.post("/kanban/tickets/copy-to-board")
    async def copy_ticket_to_board(payload: CopyToBoard):
        with get_session() as s:
            board = s.query(KanbanBoard).filter_by(id=payload.board_id, source="database").first()
            if not board:
                raise HTTPException(404, "Database board not found")
            first_lane = s.query(KanbanLane).filter_by(board_id=board.id).order_by(KanbanLane.position).first()
            if not first_lane:
                raise HTTPException(400, "Board has no lanes")
            max_pos = max([t.position for t in first_lane.tickets], default=-1)
            ticket = KanbanTicket(
                lane_id=first_lane.id, title=payload.title,
                description=payload.description or "", priority=payload.priority or "medium",
                position=max_pos + 1,
            )
            s.add(ticket)
            s.flush()
            return JSONResponse({"success": True, "id": ticket.id})

    # ── Linkable entities (for linking tickets to workflows/projects/etc.) ──

    @router.get("/kanban/linkable")
    async def get_linkable_entities():
        """Return lists of workflows, projects, snippets, actions for linking."""
        with get_session() as s:
            from distr.core.db import Workflow, Action, Snippet
            from distr.core.db.projects import Project
            from distr.core.db.workflow import AutoWorkflow
            workflows = [{"id": w.id, "title": w.title or f"Workflow #{w.id}"} for w in s.query(Workflow).all()]
            step_runner_workflows = [{"id": w.id, "title": w.name or f"Workflow #{w.id}"} for w in s.query(AutoWorkflow).all()]
            projects = [{"id": p.id, "name": p.name} for p in s.query(Project).all()]
            snippets = [{"id": sn.id, "title": sn.title or f"Snippet #{sn.id}"} for sn in s.query(Snippet).all()]
            actions = [{"id": a.id, "title": a.title or f"Action #{a.id}"} for a in s.query(Action).all()]
            return JSONResponse({"workflows": workflows + step_runner_workflows, "projects": projects, "snippets": snippets, "actions": actions})

    # ── External boards (Trello / Jira) ──

    @router.get("/kanban/external-boards")
    async def get_external_boards():
        """Fetch Trello and Jira boards from connected accounts."""
        trello_boards = []
        jira_boards = []
        try:
            from distr.core.settings import load_settings_from_db
            import json as _json
            settings = load_settings_from_db()
            raw = settings.get("connected_accounts") or "[]"
            if isinstance(raw, str):
                try:
                    accounts = _json.loads(raw)
                except Exception:
                    accounts = []
            else:
                accounts = raw if isinstance(raw, list) else []
            if not accounts:
                logger.info("External boards: no connected accounts found")
                return JSONResponse({"trello": [], "jira": []})
            logger.info("External boards: found %d connected accounts", len(accounts))
            for acct in accounts:
                provider = acct.get("provider", "").lower()
                logger.info("External boards: account provider='%s' has_key=%s has_token=%s has_email=%s",
                            provider, bool(acct.get("api_key")), bool(acct.get("api_token")), bool(acct.get("email")))
                if provider == "trello" and acct.get("api_key") and acct.get("api_token") and acct.get("is_valid", True):
                    try:
                        import requests
                        resp = requests.get(
                            "https://api.trello.com/1/members/me/boards",
                            params={"key": acct["api_key"], "token": acct["api_token"], "fields": "name,url,closed"},
                            timeout=10,
                        )
                        logger.info("Trello API response: %d", resp.status_code)
                        if resp.status_code == 200:
                            for b in resp.json():
                                if not b.get("closed", False):
                                    trello_boards.append({"id": b["id"], "name": b["name"], "url": b.get("url", "")})
                    except Exception as e:
                        logger.warning("Trello board fetch failed: %s", e)
                elif provider == "jira" and acct.get("email") and acct.get("api_token") and acct.get("is_valid", True):
                    domain = acct.get("domain") or ""
                    if not domain:
                        server_url = (acct.get("server_url") or "").strip().rstrip("/")
                        if server_url:
                            domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
                    if not domain:
                        logger.warning("Jira account has no domain or server_url, skipping")
                        continue
                    try:
                        import requests
                        from requests.auth import HTTPBasicAuth
                        base_url = f"https://{domain}"
                        logger.info("Fetching Jira boards from %s", base_url)
                        resp = requests.get(
                            f"{base_url}/rest/agile/1.0/board",
                            auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                            headers={"Accept": "application/json"},
                            timeout=10,
                        )
                        logger.info("Jira API response: %d", resp.status_code)
                        if resp.status_code == 200:
                            for b in resp.json().get("values", []):
                                jira_boards.append({
                                    "id": str(b["id"]), "name": b["name"],
                                    "url": f"https://{domain}/jira/software/projects/{b.get('location', {}).get('projectKey', '')}/boards/{b['id']}",
                                })
                    except Exception as e:
                        logger.warning("Jira board fetch failed: %s", e)
        except Exception as e:
            logger.warning("External board fetch error: %s", e)
        logger.info("External boards result: %d trello, %d jira", len(trello_boards), len(jira_boards))
        return JSONResponse({"trello": trello_boards, "jira": jira_boards})

    @router.get("/kanban/external-boards/{provider}/{board_id}")
    async def get_external_board_detail(provider: str, board_id: str):
        """Fetch lanes and tickets from an external Trello or Jira board (read-only view)."""
        lanes = []
        board_name = ""
        board_url = ""
        try:
            from distr.core.settings import load_settings_from_db
            import json as _json
            settings = load_settings_from_db()
            raw = settings.get("connected_accounts") or "[]"
            if isinstance(raw, str):
                try:
                    accounts = _json.loads(raw)
                except Exception:
                    accounts = []
            else:
                accounts = raw if isinstance(raw, list) else []
            if provider == "trello":
                for acct in accounts:
                    if acct.get("provider", "").lower() == "trello" and acct.get("api_key") and acct.get("api_token"):
                        import requests
                        br = requests.get(f"https://api.trello.com/1/boards/{board_id}",
                                          params={"key": acct["api_key"], "token": acct["api_token"], "fields": "name,url"}, timeout=10)
                        if br.status_code == 200:
                            bd = br.json()
                            board_name = bd.get("name", "")
                            board_url = bd.get("url", "")
                        lr = requests.get(f"https://api.trello.com/1/boards/{board_id}/lists",
                                          params={"key": acct["api_key"], "token": acct["api_token"], "cards": "open", "card_fields": "name,desc,url"}, timeout=10)
                        if lr.status_code == 200:
                            for lst in lr.json():
                                cards = [{"id": c["id"], "title": c["name"], "description": c.get("desc", ""), "url": c.get("url", "")} for c in lst.get("cards", [])]
                                lanes.append({"id": lst["id"], "name": lst["name"], "tickets": cards})
                        break
            elif provider == "jira":
                for acct in accounts:
                    if acct.get("provider", "").lower() == "jira" and acct.get("email") and acct.get("api_token"):
                        import requests
                        from requests.auth import HTTPBasicAuth
                        domain = acct.get("domain") or ""
                        if not domain:
                            server_url = (acct.get("server_url") or "").strip().rstrip("/")
                            if server_url:
                                domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
                        if not domain:
                            continue
                        auth = HTTPBasicAuth(acct["email"], acct["api_token"])
                        base_url = f"https://{domain}" if not domain.startswith("http") else domain
                        cr = requests.get(f"{base_url}/rest/agile/1.0/board/{board_id}/configuration",
                                          auth=auth, headers={"Accept": "application/json"}, timeout=10)
                        if cr.status_code == 200:
                            cfg = cr.json()
                            board_name = cfg.get("name", "")
                            board_url = f"https://{domain}/jira/software/projects/{cfg.get('location', {}).get('projectKey', '')}/boards/{board_id}"
                            for col in cfg.get("columnConfig", {}).get("columns", []):
                                lanes.append({"id": col["name"], "name": col["name"], "tickets": []})
                        ir = requests.get(f"{base_url}/rest/agile/1.0/board/{board_id}/issue",
                                          auth=auth, headers={"Accept": "application/json"},
                                          params={"maxResults": 100}, timeout=10)
                        if ir.status_code == 200:
                            for issue in ir.json().get("issues", []):
                                fields = issue.get("fields", {})
                                status_name = fields.get("status", {}).get("name", "")
                                card = {
                                    "id": issue["key"], "title": fields.get("summary", ""),
                                    "description": fields.get("description", "") or "",
                                    "url": f"https://{domain}/browse/{issue['key']}",
                                }
                                for lane in lanes:
                                    if lane["name"].lower() == status_name.lower():
                                        lane["tickets"].append(card)
                                        break
                        break
        except Exception as e:
            logger.warning("External board detail fetch error: %s", e)
        return JSONResponse({"name": board_name, "url": board_url, "lanes": lanes})

    # ── Send ticket to project (.tickets folder) ──

    @router.post("/kanban/tickets/{ticket_id}/send-to-project")
    async def send_ticket_to_project(ticket_id: int):
        """Create a .tickets/ticket_*.md file in the linked project's folder from a Kanban ticket."""
        with get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")

            # Resolve project: ticket-level first, then board-level default
            project_id = t.linked_project_id
            if not project_id:
                lane = s.query(KanbanLane).get(t.lane_id)
                if lane:
                    board = s.query(KanbanBoard).get(lane.board_id)
                    if board:
                        project_id = board.default_project_id

            if not project_id:
                raise HTTPException(400, "No project linked to this ticket or its board")

            from distr.core.db.projects import Project
            project = s.query(Project).get(project_id)
            if not project:
                raise HTTPException(404, "Linked project not found")
            if not project.folder_location:
                raise HTTPException(400, f"Project '{project.name}' has no folder location set")

            # Build the markdown ticket file
            tickets_folder = os.path.join(project.folder_location, ".tickets")
            os.makedirs(tickets_folder, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ticket_filename = f"ticket_{timestamp}.md"
            ticket_path = os.path.join(tickets_folder, ticket_filename)

            # Gather sub-items
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

            content = f"""---
id: ticket_{timestamp}
title: {t.title}
project: {project.name}
created: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
priority: {t.priority or "medium"}
status: open
source: kanban_ticket_{t.id}
---

## Description
{t.description or "(no description)"}
{todos_md}{links_md}{files_md}
## Context
- **Project:** {project.name} (ID: {project.id})
- **Folder:** {project.folder_location}
- **Kanban Ticket ID:** {t.id}

---
*Sent from Kanban board via DecisionsAI*
"""

            try:
                with open(ticket_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                raise HTTPException(500, f"Failed to write ticket file: {e}")

            return JSONResponse({
                "success": True,
                "file_path": ticket_path,
                "project_name": project.name,
            })

    # ── Send ticket to CLI ──

    @router.post("/kanban/tickets/{ticket_id}/send-to-cli")
    async def send_ticket_to_cli(ticket_id: int):
        """Send a ticket's instruction to Kiro CLI for the linked project."""
        import shutil
        import subprocess as sp

        kiro_path = shutil.which("kiro-cli")
        if not kiro_path:
            raise HTTPException(400, "Kiro CLI is not installed")

        with get_session() as s:
            t = s.query(KanbanTicket).get(ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")

            title = t.title
            description = t.description or ""
            tid = t.id

            project_id = t.linked_project_id
            if not project_id:
                lane = s.query(KanbanLane).get(t.lane_id)
                if lane:
                    board = s.query(KanbanBoard).get(lane.board_id)
                    if board:
                        project_id = board.default_project_id

            if not project_id:
                raise HTTPException(400, "No project linked to this ticket or its board")

            from distr.core.db.projects import Project
            project = s.query(Project).get(project_id)
            if not project or not project.folder_location:
                raise HTTPException(400, "Project has no folder location set")

            folder = project.folder_location
            project_name = project.name

        instruction = f"{title}\n\n{description}".strip() if description else title

        # Create audit trail using AutoWorkflow models
        from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
        audit_id = step_id = None
        try:
            with get_session() as s:
                audit = AutoWorkflow(
                    name=f"[Project: {project_name}] Ticket #{tid}: {title}",
                    status="in_progress", workflow_type="kiro_cli",
                )
                s.add(audit)
                s.flush()
                step = AutoWorkflowStep(
                    workflow_id=audit.id, position=0,
                    name=f"Ticket #{tid}", instruction=instruction[:500],
                    status="running", tool_used="kiro-cli",
                )
                s.add(step)
                s.commit()
                audit_id, step_id = audit.id, step.id
        except Exception:
            pass

        # Run CLI in background thread so the API returns immediately
        def _run_cli():
            try:
                result = sp.run(
                    [kiro_path, "chat", "--no-interactive", "--trust-all-tools", instruction],
                    capture_output=True, text=True, timeout=600, cwd=folder,
                )
                output = (result.stdout + result.stderr).strip()[:3000]
                status = "completed" if result.returncode == 0 else "failed"
            except sp.TimeoutExpired:
                output, status = "Kiro CLI timed out after 10 minutes", "failed"
            except Exception as e:
                output, status = f"Kiro CLI error: {e}", "failed"

            if audit_id and step_id:
                pass  # Legacy StepRunner audit trail removed (task 6.3)

        threading.Thread(target=_run_cli, daemon=True).start()

        return JSONResponse({
            "success": True,
            "message": f"Ticket #{tid} sent to CLI for project '{project_name}'. Check the audit log for progress.",
        })

    # ── Agent run / cancel / restart ──

    @router.post("/kanban/boards/{board_id}/run-agent")
    async def run_agent(board_id: int):
        with get_session() as s:
            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            if not board.agent_enabled:
                raise HTTPException(400, "Agent not enabled on this board")
        agent = KanbanAgentCheckIn(board_id)
        threading.Thread(target=agent.run, daemon=True).start()
        return JSONResponse({"success": True})

    @router.post("/kanban/boards/{board_id}/cancel-agent")
    async def cancel_agent(board_id: int):
        agent = _active_agents.get(board_id)
        if not agent:
            raise HTTPException(404, "No active agent for this board")
        agent.cancel()
        return JSONResponse({"success": True})

    @router.post("/kanban/boards/{board_id}/restart-agent")
    async def restart_agent(board_id: int):
        agent = _active_agents.get(board_id)
        if agent:
            agent.restart()
        else:
            agent = KanbanAgentCheckIn(board_id)
            threading.Thread(target=agent.run, daemon=True).start()
        return JSONResponse({"success": True})

    @router.get("/kanban/boards/{board_id}/agent-status")
    async def agent_status(board_id: int):
        agent = _active_agents.get(board_id)
        if not agent:
            return JSONResponse({"state": "idle"})
        s = agent.status
        return JSONResponse({
            "state": s.state,
            "current_ticket_id": s.current_ticket_id,
            "current_ticket_title": s.current_ticket_title,
            "total_tickets": s.total_tickets,
            "processed_count": s.processed_count,
            "current_run_id": s.current_run_id,
        })

    # ── WhatsApp ↔ Board Integration ──

    @router.get("/kanban/boards/{board_id}/whatsapp-links")
    async def get_whatsapp_links(board_id: int):
        """Get WhatsApp phone numbers linked to this board."""
        from distr.core.db import WhatsAppPhoneLink
        with get_session() as s:
            links = s.query(WhatsAppPhoneLink).filter_by(board_id=board_id).all()
            return JSONResponse([{
                "id": l.id,
                "board_id": l.board_id,
                "phone_jid": l.phone_jid,
                "phone_number": l.phone_number or "",
                "contact_name": l.contact_name or "",
                "auto_snapshot": l.auto_snapshot or False,
            } for l in links])

    @router.post("/kanban/boards/{board_id}/whatsapp-links")
    async def add_whatsapp_link(board_id: int, payload: dict):
        """Link a WhatsApp phone number to this board."""
        from distr.core.db import WhatsAppPhoneLink
        phone_jid = payload.get("phone_jid", "")
        if not phone_jid:
            raise HTTPException(400, "phone_jid is required")
        with get_session() as s:
            # Prevent duplicate links
            existing = s.query(WhatsAppPhoneLink).filter_by(board_id=board_id, phone_jid=phone_jid).first()
            if existing:
                return JSONResponse({"success": True, "id": existing.id, "message": "Already linked"})
            link = WhatsAppPhoneLink(
                board_id=board_id,
                phone_jid=phone_jid,
                phone_number=payload.get("phone_number", phone_jid.split("@")[0].split(":")[0]),
                contact_name=payload.get("contact_name", ""),
                auto_snapshot=payload.get("auto_snapshot", False),
            )
            s.add(link)
            s.flush()
            return JSONResponse({"success": True, "id": link.id})

    @router.delete("/kanban/boards/{board_id}/whatsapp-links/{link_id}")
    async def delete_whatsapp_link(board_id: int, link_id: int):
        """Unlink a WhatsApp phone number from this board."""
        from distr.core.db import WhatsAppPhoneLink
        with get_session() as s:
            link = s.query(WhatsAppPhoneLink).filter_by(id=link_id, board_id=board_id).first()
            if not link:
                raise HTTPException(404, "Link not found")
            s.delete(link)
            return JSONResponse({"success": True})

    @router.patch("/kanban/boards/{board_id}/whatsapp-links/{link_id}")
    async def update_whatsapp_link(board_id: int, link_id: int, payload: dict):
        """Update a WhatsApp link (e.g. toggle auto_snapshot)."""
        from distr.core.db import WhatsAppPhoneLink
        with get_session() as s:
            link = s.query(WhatsAppPhoneLink).filter_by(id=link_id, board_id=board_id).first()
            if not link:
                raise HTTPException(404, "Link not found")
            if "auto_snapshot" in payload:
                link.auto_snapshot = payload["auto_snapshot"]
            if "contact_name" in payload:
                link.contact_name = payload["contact_name"]
            return JSONResponse({"success": True})

    @router.get("/kanban/whatsapp/messages")
    async def get_whatsapp_messages(jid_phone: str = "", limit: int = 50, offset: int = 0, unprocessed_only: bool = False):
        """Get WhatsApp messages stored in the local database."""
        try:
            from PyQt6.QtWidgets import QApplication
            _app = QApplication.instance()
            whatsapp_manager = getattr(_app, 'whatsapp_manager', None) if _app else None
            if not whatsapp_manager:
                return JSONResponse({"messages": [], "total": 0, "error": "WhatsApp not connected"})
            result = whatsapp_manager.get_stored_messages(
                jid_phone=jid_phone or None,
                limit=limit,
                offset=offset,
                unprocessed_only=unprocessed_only,
            )
            return JSONResponse(result)
        except Exception as e:
            logger.error(f"WhatsApp message query error: {e}")
            return JSONResponse({"messages": [], "total": 0, "error": str(e)})

    @router.post("/kanban/whatsapp/messages/{message_id}/processed")
    async def mark_whatsapp_message_processed(message_id: int):
        """Mark a WhatsApp message as processed."""
        from distr.core.db import WhatsAppMessage
        with get_session() as s:
            msg = s.query(WhatsAppMessage).get(message_id)
            if not msg:
                raise HTTPException(404, "Message not found")
            msg.processed = True
            msg.processed_date = datetime.utcnow()
            return JSONResponse({"success": True})

    @router.get("/kanban/whatsapp/media")
    async def get_whatsapp_media(path: str = ""):
        """Serve a WhatsApp media file for display in the UI."""
        import os as _os
        from fastapi.responses import FileResponse
        if not path:
            raise HTTPException(400, "path parameter required")
        # Security: only allow files under ~/Downloads/DecisionsAI/
        home = _os.path.expanduser("~")
        allowed_dir = _os.path.join(home, "Downloads", "DecisionsAI")
        realpath = _os.path.realpath(path)
        if not realpath.startswith(_os.path.realpath(allowed_dir)):
            raise HTTPException(403, "Access denied")
        if not _os.path.exists(realpath):
            raise HTTPException(404, "File not found")
        # Determine media type from extension
        ext_media = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
            ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".opus": "audio/opus",
            ".mp4": "video/mp4", ".3gp": "video/3gpp",
            ".pdf": "application/pdf",
        }
        ext = _os.path.splitext(realpath)[1].lower()
        media_type = ext_media.get(ext, "application/octet-stream")
        return FileResponse(realpath, media_type=media_type)

    @router.get("/kanban/whatsapp/chats")
    async def get_whatsapp_chats(limit: int = 100, offset: int = 0, search: str = ""):
        """Get the WhatsApp chat list from the Baileys service."""
        try:
            from PyQt6.QtWidgets import QApplication
            _app = QApplication.instance()
            whatsapp_manager = getattr(_app, 'whatsapp_manager', None) if _app else None
            if not whatsapp_manager:
                return JSONResponse({"chats": [], "total": 0, "error": "WhatsApp not connected"})
            result = whatsapp_manager.get_chats(limit=limit, offset=offset, search=search)
            return JSONResponse(result)
        except Exception as e:
            logger.error(f"WhatsApp chats query error: {e}")
            return JSONResponse({"chats": [], "total": 0, "error": str(e)})

    @router.post("/kanban/tickets/from-whatsapp/{message_id}")
    async def create_ticket_from_whatsapp(message_id: int, payload: dict):
        """Create a Kanban ticket from a WhatsApp message."""
        from distr.core.db import WhatsAppMessage, WhatsAppPhoneLink
        with get_session() as s:
            msg = s.query(WhatsAppMessage).get(message_id)
            if not msg:
                raise HTTPException(404, "WhatsApp message not found")

            board_id = payload.get("board_id")
            if not board_id:
                raise HTTPException(400, "board_id is required")

            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")

            # Find source lane (use board default or first lane)
            source_lane_name = board.agent_source_lane or ""
            lane = None
            if source_lane_name:
                lane = s.query(KanbanLane).filter_by(board_id=board_id, name=source_lane_name).first()
            if not lane:
                lane = s.query(KanbanLane).filter_by(board_id=board_id).order_by(KanbanLane.position).first()
            if not lane:
                raise HTTPException(400, "Board has no lanes")

            # Build ticket title from message
            sender = msg.sender_push_name or msg.sender_phone or msg.jid_phone or "Unknown"
            title = f"[WA] {sender}: {msg.text[:80]}" if msg.text else f"[WA] {sender}: {msg.media_type or 'message'}"
            if msg.caption:
                title = f"[WA] {sender}: {msg.caption[:80]}"

            # Build description
            desc_parts = [f"WhatsApp message from {sender}"]
            if msg.sender_phone:
                desc_parts.append(f"Phone: {msg.sender_phone}")
            if msg.text:
                desc_parts.append(f"\n{msg.text}")
            if msg.caption:
                desc_parts.append(f"Caption: {msg.caption}")
            if msg.media_type:
                desc_parts.append(f"Media: {msg.media_type}")
                if msg.media_filename:
                    desc_parts.append(f"File: {msg.media_filename}")
                if msg.media_local_path:
                    desc_parts.append(f"Path: {msg.media_local_path}")
            description = "\n".join(desc_parts)

            # Check if ticket already exists for this message
            existing = s.query(KanbanTicket).filter_by(whatsapp_message_id=message_id).first()
            if existing:
                return JSONResponse({"success": True, "id": existing.id, "message": "Ticket already exists"})

            max_pos = max([t.position for t in lane.tickets], default=-1)
            ticket = KanbanTicket(
                lane_id=lane.id,
                title=title,
                description=description,
                priority="medium",
                position=max_pos + 1,
                whatsapp_message_id=message_id,
                whatsapp_message_wa_id=msg.message_id,
            )
            s.add(ticket)

            # Mark message as processed
            msg.processed = True
            msg.processed_date = datetime.utcnow()

            # If message has media, add as ticket file
            if msg.media_local_path and os.path.exists(msg.media_local_path):
                safe_name = os.path.basename(msg.media_local_path)
                ticket_file = KanbanTicketFile(
                    ticket_id=ticket.id if ticket.id else 0,  # Will be set after flush
                    filename=safe_name,
                    file_path=msg.media_local_path,
                    description=f"WhatsApp {msg.media_type}: {safe_name}" if msg.media_type else safe_name,
                )
                # Need to flush ticket first to get ID
                s.flush()
                ticket_file.ticket_id = ticket.id
                s.add(ticket_file)

            s.flush()
            return JSONResponse({"success": True, "id": ticket.id})

    return router
