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
    color: Optional[str] = None

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


ALLOWED_FREQUENCIES = {"hourly", "daily", "weekly", "fortnightly", "monthly"}


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

    # ── Boards ──

    @router.get("/kanban/boards")
    async def list_boards():
        with get_session() as s:
            boards = s.query(KanbanBoard).order_by(KanbanBoard.name).all()
            result = []
            for b in boards:
                result.append({
                    "id": b.id, "name": b.name, "description": b.description or "",
                    "source": b.source, "external_board_id": b.external_board_id,
                    "external_url": b.external_url, "color": b.color or "",
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
            if payload.color is not None:
                board.color = payload.color if payload.color else None
            return JSONResponse({"success": True})

    @router.delete("/kanban/boards/{board_id}")
    async def delete_board(board_id: int):
        with get_session() as s:
            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            s.delete(board)
            return JSONResponse({"success": True})

    @router.get("/kanban/boards/{board_id}")
    async def get_board(board_id: int):
        with get_session() as s:
            board = s.query(KanbanBoard).get(board_id)
            if not board:
                raise HTTPException(404, "Board not found")
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
                "color": board.color or "",
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
            return JSONResponse({"workflows": workflows, "step_runner_workflows": step_runner_workflows, "projects": projects, "snippets": snippets, "actions": actions})

    # ── External boards (Trello / Jira) ──

    @router.get("/kanban/external-boards")
    async def get_external_boards():
        """Fetch Trello and Jira boards from connected accounts."""
        trello_boards = []
        jira_boards = []
        try:
            from distr.core.db import get_session as gs, Settings
            import json as _json
            with gs() as s:
                settings = s.query(Settings).first()
                if settings and settings.connected_accounts:
                    accounts = _json.loads(settings.connected_accounts or "[]")
                    for acct in accounts:
                        provider = acct.get("provider", "").lower()
                        if provider == "trello" and acct.get("api_key") and acct.get("api_token"):
                            try:
                                import requests
                                resp = requests.get(
                                    "https://api.trello.com/1/members/me/boards",
                                    params={"key": acct["api_key"], "token": acct["api_token"], "fields": "name,url"},
                                    timeout=10,
                                )
                                if resp.status_code == 200:
                                    for b in resp.json():
                                        trello_boards.append({"id": b["id"], "name": b["name"], "url": b.get("url", "")})
                            except Exception as e:
                                logger.warning("Trello board fetch failed: %s", e)
                        elif provider == "jira" and acct.get("domain") and acct.get("email") and acct.get("api_token"):
                            try:
                                import requests
                                from requests.auth import HTTPBasicAuth
                                resp = requests.get(
                                    f"https://{acct['domain']}/rest/agile/1.0/board",
                                    auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                                    headers={"Accept": "application/json"},
                                    timeout=10,
                                )
                                if resp.status_code == 200:
                                    for b in resp.json().get("values", []):
                                        jira_boards.append({
                                            "id": str(b["id"]), "name": b["name"],
                                            "url": f"https://{acct['domain']}/jira/software/projects/{b.get('location', {}).get('projectKey', '')}/boards/{b['id']}",
                                        })
                            except Exception as e:
                                logger.warning("Jira board fetch failed: %s", e)
        except Exception as e:
            logger.warning("External board fetch error: %s", e)
        return JSONResponse({"trello": trello_boards, "jira": jira_boards})

    @router.get("/kanban/external-boards/{provider}/{board_id}")
    async def get_external_board_detail(provider: str, board_id: str):
        """Fetch lanes and tickets from an external Trello or Jira board (read-only view)."""
        lanes = []
        board_name = ""
        board_url = ""
        try:
            from distr.core.db import get_session as gs, Settings
            import json as _json
            with gs() as s:
                settings = s.query(Settings).first()
                accounts = _json.loads(settings.connected_accounts or "[]") if settings else []
                if provider == "trello":
                    for acct in accounts:
                        if acct.get("provider", "").lower() == "trello" and acct.get("api_key") and acct.get("api_token"):
                            import requests
                            # Get board info
                            br = requests.get(f"https://api.trello.com/1/boards/{board_id}",
                                              params={"key": acct["api_key"], "token": acct["api_token"], "fields": "name,url"}, timeout=10)
                            if br.status_code == 200:
                                bd = br.json()
                                board_name = bd.get("name", "")
                                board_url = bd.get("url", "")
                            # Get lists
                            lr = requests.get(f"https://api.trello.com/1/boards/{board_id}/lists",
                                              params={"key": acct["api_key"], "token": acct["api_token"], "cards": "open", "card_fields": "name,desc,url"}, timeout=10)
                            if lr.status_code == 200:
                                for lst in lr.json():
                                    cards = [{"id": c["id"], "title": c["name"], "description": c.get("desc", ""), "url": c.get("url", "")} for c in lst.get("cards", [])]
                                    lanes.append({"id": lst["id"], "name": lst["name"], "tickets": cards})
                            break
                elif provider == "jira":
                    for acct in accounts:
                        if acct.get("provider", "").lower() == "jira" and acct.get("domain") and acct.get("email") and acct.get("api_token"):
                            import requests
                            from requests.auth import HTTPBasicAuth
                            auth = HTTPBasicAuth(acct["email"], acct["api_token"])
                            domain = acct["domain"]
                            # Get board config for columns
                            cr = requests.get(f"https://{domain}/rest/agile/1.0/board/{board_id}/configuration",
                                              auth=auth, headers={"Accept": "application/json"}, timeout=10)
                            if cr.status_code == 200:
                                cfg = cr.json()
                                board_name = cfg.get("name", "")
                                board_url = f"https://{domain}/jira/software/projects/{cfg.get('location', {}).get('projectKey', '')}/boards/{board_id}"
                                for col in cfg.get("columnConfig", {}).get("columns", []):
                                    lanes.append({"id": col["name"], "name": col["name"], "tickets": []})
                            # Get issues on board
                            ir = requests.get(f"https://{domain}/rest/agile/1.0/board/{board_id}/issue",
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

    return router
