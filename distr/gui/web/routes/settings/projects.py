"""
Projects routes — /projects/*, /browse-folder
"""
from fastapi import HTTPException, File, UploadFile, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import Optional
import json
import os
import subprocess
import sys
import re

from ._shared import logger, ProjectUpdate, ContextItemCreate, ContextItemUpdate, PROJECT_UPLOADS_DIR


def register_routes(router, templates):

    @router.get("/projects")
    async def get_projects_list():
        """Get list of projects for the Projects page"""
        logger.info("GET /api/projects called")
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            with get_session() as session:
                projects = session.query(Project).order_by(Project.modified_date.desc()).all()
                return JSONResponse([
                    {
                        "id": p.id,
                        "name": p.name or "",
                        "description": p.description or "",
                        "folder_location": p.folder_location or "",
                        "in_use": bool(p.in_use),
                        "provider": p.provider or "",
                        "board_name": p.board_name or "",
                    }
                    for p in projects
                ])
        except Exception as e:
            logger.error(f"Failed to load projects: {e}", exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/projects/board-providers")
    async def get_board_providers():
        """Return board providers (Trello/Jira) that have at least one valid connected account."""
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            raw = settings.get("connected_accounts") or "[]"
            if isinstance(raw, str):
                try:
                    connected = json.loads(raw)
                except Exception:
                    connected = []
            else:
                connected = raw if isinstance(raw, list) else []
            trello_has = any(
                isinstance(a, dict) and a.get("provider") == "trello" and a.get("is_valid") and a.get("api_key") and a.get("api_token")
                for a in connected
            )
            jira_has = any(
                isinstance(a, dict) and a.get("provider") == "jira" and a.get("is_valid") and a.get("server_url") and a.get("email") and a.get("api_token")
                for a in connected
            )
            providers = [{"id": "trello", "name": "Trello"}] if trello_has else []
            if jira_has:
                providers.append({"id": "jira", "name": "Jira"})
            return JSONResponse({"providers": providers})
        except Exception as e:
            logger.error(f"Failed to get board providers: {e}", exc_info=True)
            return JSONResponse({"providers": []})

    @router.get("/projects/boards")
    async def get_boards(provider: Optional[str] = None):
        """Return boards for the given provider (trello or jira). Requires valid connected account(s)."""
        if provider not in ("trello", "jira"):
            return JSONResponse({"boards": []})
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            raw = settings.get("connected_accounts") or "[]"
            if isinstance(raw, str):
                try:
                    connected = json.loads(raw)
                except Exception:
                    connected = []
            else:
                connected = raw if isinstance(raw, list) else []
            if provider == "trello":
                from distr.core.integrations.trello_api import TrelloAPI
                accounts = [a for a in connected if isinstance(a, dict) and a.get("provider") == "trello" and a.get("is_valid") and a.get("api_key") and a.get("api_token")]
                all_boards = []
                for acc in accounts:
                    try:
                        api = TrelloAPI(acc.get("api_key", ""), acc.get("api_token", ""))
                        if api.test_connection():
                            for b in (api.get_boards() or []):
                                if not b.get("closed", False):
                                    all_boards.append({"id": b.get("id"), "name": b.get("name") or "Unnamed Board"})
                    except Exception as e:
                        logger.warning(f"Trello boards fetch: {e}")
                return JSONResponse({"boards": all_boards})
            else:
                import requests
                from base64 import b64encode
                accounts = [a for a in connected if isinstance(a, dict) and a.get("provider") == "jira" and a.get("is_valid") and a.get("server_url") and a.get("email") and a.get("api_token")]
                all_boards = []
                for acc in accounts:
                    try:
                        url = (acc.get("server_url") or "").strip()
                        if not url.startswith("http"):
                            url = "https://" + url
                        url = url.rstrip("/")
                        auth = b64encode(f"{acc.get('email')}:{acc.get('api_token')}".encode("ascii")).decode("ascii")
                        headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
                        r = requests.get(f"{url}/rest/api/3/myself", headers=headers, timeout=10)
                        if r.status_code != 200:
                            continue
                        br = requests.get(f"{url}/rest/agile/1.0/board", headers=headers, params={"maxResults": 1000, "type": "scrum,kanban"}, timeout=30)
                        if br.status_code == 200 and "values" in br.json():
                            for b in br.json()["values"]:
                                all_boards.append({"id": str(b.get("id")), "name": b.get("name") or "Unnamed Board"})
                    except Exception as e:
                        logger.warning(f"Jira boards fetch: {e}")
                return JSONResponse({"boards": all_boards})
        except Exception as e:
            logger.error(f"Failed to get boards: {e}", exc_info=True)
            return JSONResponse({"boards": []})

    @router.get("/projects/{project_id}")
    async def get_project_detail(project_id: int):
        """Get full project details including context items and files (matches desktop Projects UI)."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project, ProjectContextItem, ProjectFile
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                context_items = session.query(ProjectContextItem).filter(ProjectContextItem.project_id == project_id).order_by(ProjectContextItem.modified_date.desc()).all()
                files = session.query(ProjectFile).filter(ProjectFile.project_id == project_id).order_by(ProjectFile.modified_date.desc()).all()
                return JSONResponse({
                    "id": project.id,
                    "name": project.name or "",
                    "description": project.description or "",
                    "folder_location": project.folder_location or "",
                    "in_use": bool(project.in_use),
                    "provider": project.provider or "",
                    "board_id": project.board_id or "",
                    "board_name": project.board_name or "",
                    "additional_trigger_words": project.additional_trigger_words or "[]",
                    "startup_instructions": project.startup_instructions or "",
                    "context_items": [{"id": c.id, "title": c.title or "", "content": c.content or ""} for c in context_items],
                    "files": [{"id": f.id, "filename": f.filename or "", "description": f.description or "", "file_path": f.file_path or ""} for f in files],
                })
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to load project detail: {e}", exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/projects/{project_id}/context-items")
    async def create_context_item(project_id: int, payload: ContextItemCreate):
        """Add a context item to a project (matches desktop Add Context Item)."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project, ProjectContextItem
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                title = (payload.title or "").strip()
                content = (payload.content or "").strip()
                if not title:
                    raise HTTPException(status_code=400, detail="Title is required")
                if not content:
                    raise HTTPException(status_code=400, detail="Content is required")
                item = ProjectContextItem(project_id=project_id, title=title, content=content)
                session.add(item)
                session.commit()
                session.refresh(item)
                return JSONResponse({"id": item.id, "success": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to create context item: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/projects/{project_id}/context-items/{item_id}")
    async def update_context_item(project_id: int, item_id: int, payload: ContextItemUpdate):
        """Update a project context item (matches desktop Edit Context Item)."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import ProjectContextItem
            with get_session() as session:
                item = session.query(ProjectContextItem).filter(
                    ProjectContextItem.id == item_id,
                    ProjectContextItem.project_id == project_id
                ).first()
                if not item:
                    raise HTTPException(status_code=404, detail="Context item not found")
                if payload.title is not None:
                    item.title = (payload.title or "").strip()
                if payload.content is not None:
                    item.content = (payload.content or "").strip()
                session.commit()
                return JSONResponse({"success": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update context item: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/projects/{project_id}/context-items/{item_id}")
    async def delete_context_item(project_id: int, item_id: int):
        """Remove a project context item (matches desktop Remove Context Item)."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import ProjectContextItem
            with get_session() as session:
                item = session.query(ProjectContextItem).filter(
                    ProjectContextItem.id == item_id,
                    ProjectContextItem.project_id == project_id
                ).first()
                if not item:
                    raise HTTPException(status_code=404, detail="Context item not found")
                session.delete(item)
                session.commit()
                return JSONResponse({"success": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to delete context item: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    def _safe_filename(name: str) -> str:
        """Sanitize filename: strip path, allow alphanumeric, dash, underscore, dot."""
        base = os.path.basename(name) if name else "file"
        base = re.sub(r"[^\w\-.]", "_", base)
        return base or "file"

    @router.post("/projects/{project_id}/files")
    async def upload_project_file(project_id: int, file: UploadFile = File(...), description: Optional[str] = Form(None)):
        """Upload a file for a project; file is stored under PROJECT_UPLOADS_DIR and a ProjectFile record is created."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project, ProjectFile
            if not file.filename:
                raise HTTPException(status_code=400, detail="No file selected")
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
            project_dir = os.path.join(PROJECT_UPLOADS_DIR, str(project_id))
            os.makedirs(project_dir, exist_ok=True)
            base_name = _safe_filename(file.filename)
            stem, ext = os.path.splitext(base_name)
            dest_name = base_name
            dest_path = os.path.join(project_dir, dest_name)
            n = 0
            while os.path.exists(dest_path):
                n += 1
                dest_name = f"{stem}_{n}{ext}"
                dest_path = os.path.join(project_dir, dest_name)
            contents = await file.read()
            with open(dest_path, "wb") as f:
                f.write(contents)
            with get_session() as session:
                pf = ProjectFile(project_id=project_id, filename=dest_name, description=(description or "").strip() or None, file_path=os.path.abspath(dest_path))
                session.add(pf)
                session.commit()
                session.refresh(pf)
                out_id = pf.id
                out_filename = pf.filename
            return JSONResponse({"id": out_id, "filename": out_filename, "success": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to upload project file: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/projects/{project_id}/files/{file_id}/open-folder")
    async def open_project_file_folder(project_id: int, file_id: int):
        """Open the file's folder in the system file manager (Finder on macOS)."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import ProjectFile
            with get_session() as session:
                pf = session.query(ProjectFile).filter(ProjectFile.id == file_id, ProjectFile.project_id == project_id).first()
                if not pf:
                    raise HTTPException(status_code=404, detail="File not found")
                path = (pf.file_path or "").strip()
            if not path or not os.path.isfile(path):
                raise HTTPException(status_code=404, detail="File not found on disk")
            folder = os.path.dirname(path)
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", path], check=False, timeout=5)
            elif sys.platform == "win32":
                subprocess.run(["explorer", "/select," + path], check=False, timeout=5)
            else:
                subprocess.run(["xdg-open", folder], check=False, timeout=5)
            return JSONResponse({"success": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Open file folder failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/projects/{project_id}/files/{file_id}")
    async def delete_project_file(project_id: int, file_id: int):
        """Remove a project file and delete the file from disk if under PROJECT_UPLOADS_DIR."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import ProjectFile
            with get_session() as session:
                pf = session.query(ProjectFile).filter(ProjectFile.id == file_id, ProjectFile.project_id == project_id).first()
                if not pf:
                    raise HTTPException(status_code=404, detail="File not found")
                path = (pf.file_path or "").strip()
                session.delete(pf)
                session.commit()
            if path and os.path.isabs(path) and path.startswith(os.path.abspath(PROJECT_UPLOADS_DIR)) and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError as e:
                    logger.warning(f"Could not remove uploaded file {path}: {e}")
            return JSONResponse({"success": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to delete project file: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/browse-folder")
    async def browse_folder(initial_dir: Optional[str] = None):
        """Open native folder picker and return selected path (matches desktop Browse folder).
        On macOS uses osascript so no Python dock icon or focus steal; on Windows/Linux uses tkinter."""
        try:
            initial = (initial_dir or "").strip() or os.path.expanduser("~")
            if not os.path.isdir(initial):
                initial = os.path.expanduser("~")
            path = ""
            if sys.platform == "darwin":
                # macOS: osascript shows native folder picker without spawning Python (no dock icon)
                esc = initial.replace("\\", "\\\\").replace('"', '\\"')
                script = (
                    f'return POSIX path of (choose folder with prompt "Select project folder" '
                    f'default location (POSIX file "{esc}"))'
                )
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                path = (result.stdout or "").strip()
                if result.returncode != 0 and "User canceled" not in (result.stderr or ""):
                    logger.warning(f"osascript browse-folder: {result.stderr}")
            else:
                # Windows/Linux: tkinter (Python subprocess may show in taskbar/dock)
                env = os.environ.copy()
                env["BROWSE_INITIAL_DIR"] = initial
                code = (
                    "import os\n"
                    "import tkinter\n"
                    "from tkinter import filedialog\n"
                    "root = tkinter.Tk()\n"
                    "root.withdraw()\n"
                    "root.attributes('-topmost', True)\n"
                    "path = filedialog.askdirectory(initialdir=os.environ.get('BROWSE_INITIAL_DIR', ''), title='Select project folder')\n"
                    "print(path or '')\n"
                )
                result = subprocess.run(
                    [sys.executable, "-c", code],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                path = (result.stdout or "").strip()
            if path:
                return JSONResponse({"path": path})
            return JSONResponse({"error": "No folder selected"})
        except subprocess.TimeoutExpired:
            return JSONResponse({"error": "Folder selection timed out"})
        except Exception as e:
            logger.error(f"Browse folder failed: {e}", exc_info=True)
            return JSONResponse({"error": str(e)})

    @router.put("/projects/{project_id}")
    async def update_project(project_id: int, payload: ProjectUpdate):
        """Update a project by id"""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                if payload.name is not None:
                    project.name = payload.name
                if payload.description is not None:
                    project.description = payload.description
                if payload.folder_location is not None:
                    project.folder_location = payload.folder_location
                if payload.additional_trigger_words is not None:
                    project.additional_trigger_words = payload.additional_trigger_words
                if payload.startup_instructions is not None:
                    project.startup_instructions = payload.startup_instructions
                if payload.provider is not None:
                    project.provider = payload.provider
                if payload.board_id is not None:
                    project.board_id = payload.board_id
                if payload.board_name is not None:
                    project.board_name = payload.board_name
                session.commit()
                return JSONResponse({"success": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update project: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/projects/{project_id}/use")
    async def set_project_in_use(project_id: int):
        """Set this project as the one in use (only one can be in use).
        Also sets the linked kanban board as in_use if one exists."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.db.kanban import KanbanBoard
            with get_session() as session:
                session.query(Project).filter(Project.in_use == True).update({"in_use": False})
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                project.in_use = True

                # Also activate the linked board if the project has a board_id
                linked_board_name = None
                if project.board_id:
                    # Find the kanban board linked to this project
                    board = session.query(KanbanBoard).filter(
                        KanbanBoard.default_project_id == project_id
                    ).first()
                    if board:
                        session.query(KanbanBoard).filter(KanbanBoard.in_use == True).update({"in_use": False})
                        board.in_use = True
                        linked_board_name = board.name

                session.commit()
                return JSONResponse({"success": True, "linked_board": linked_board_name})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to set project in use: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/projects")
    async def create_project(request: Request):
        """Create a new project with optional name and folder."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            name = (body.get("name") or "").strip() or "New Project"
            folder = (body.get("folder_location") or "").strip()
            with get_session() as session:
                project = Project(name=name, description="", folder_location=folder, additional_trigger_words="[]")
                session.add(project)
                session.commit()
                session.refresh(project)
                return JSONResponse({"id": project.id, "success": True})
        except Exception as e:
            logger.error(f"Failed to create project: {e}", exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/projects/{project_id}")
    async def delete_project(project_id: int):
        """Delete a project by id"""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                session.delete(project)
                session.commit()
                return JSONResponse({"success": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to delete project: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/projects/{project_id}/kanban-board")
    async def get_project_kanban_board(project_id: int):
        """Return the kanban board linked to this project, or null if none exists."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.db.kanban import KanbanBoard
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                # Check for a board with default_project_id pointing to this project
                board = session.query(KanbanBoard).filter(
                    KanbanBoard.default_project_id == project_id
                ).first()
                if board:
                    return JSONResponse({"board": {"id": board.id, "name": board.name}})
                # Also check for a board whose name matches the project name
                board = session.query(KanbanBoard).filter(
                    KanbanBoard.name == project.name
                ).first()
                if board:
                    return JSONResponse({"board": {"id": board.id, "name": board.name}})
                return JSONResponse({"board": None})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get kanban board for project: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/projects/{project_id}/kanban-board")
    async def create_project_kanban_board(project_id: int):
        """Create a kanban board for this project, named after the project, and link it."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.db.kanban import KanbanBoard, KanbanLane
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                # Check if one already exists
                existing = session.query(KanbanBoard).filter(
                    KanbanBoard.default_project_id == project_id
                ).first()
                if not existing:
                    existing = session.query(KanbanBoard).filter(
                        KanbanBoard.name == project.name
                    ).first()
                if existing:
                    # Link it if not already linked
                    if not existing.default_project_id:
                        existing.default_project_id = project_id
                    return JSONResponse({"board": {"id": existing.id, "name": existing.name}, "created": False})
                board = KanbanBoard(name=project.name, description=f"Board for project: {project.name}", source="database", default_project_id=project_id)
                session.add(board)
                session.flush()
                for i, lane_name in enumerate(["Backlog", "Current", "QA / Assess", "Done"]):
                    session.add(KanbanLane(board_id=board.id, name=lane_name, position=i))
                session.flush()
                return JSONResponse({"board": {"id": board.id, "name": board.name}, "created": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to create kanban board for project: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    # ── CLI: send instruction & audit trail ──

    @router.post("/projects/{project_id}/cli")
    async def send_cli_instruction(project_id: int, request: Request):
        """Send an instruction to pi coding agent in the context of a project."""
        try:
            body = await request.json()
            instruction = (body.get("instruction") or "").strip()
            if not instruction:
                return JSONResponse({"success": False, "error": "instruction required"}, status_code=400)

            # Check pi coding agent is available
            from distr.core.pi_rpc import PiRpcSession, get_or_create_rpc_session
            pi_path = PiRpcSession.find_pi()
            if not pi_path:
                return JSONResponse({"success": False, "error": "Pi coding agent not installed. Run: npm install -g @mariozechner/pi-coding-agent"}, status_code=400)

            from distr.core.db import get_session
            from distr.core.db.projects import Project
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                folder = project.folder_location or ""
                project_name = project.name or ""

            if not folder:
                return JSONResponse({"success": False, "error": "Project has no folder set"}, status_code=400)

            # Log to current chat
            chat_id = None
            try:
                from distr.core.settings import load_settings_from_db
                settings = load_settings_from_db()
                chat_id = settings.get("agent_current_chat_id") or settings.get("last_chat_id")
                if chat_id:
                    from distr.core.chat import ChatService
                    ChatService.add_message(int(chat_id), "user", f"[Pi: {project_name}] {instruction}")
            except Exception as e:
                logger.debug(f"Could not log CLI instruction to chat: {e}")

            # Create audit workflow to track this execution
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
            with get_session() as session:
                audit = AutoWorkflow(
                    name=f"[Project: {project_name}] {instruction}",
                    status="in_progress",
                    chat_id=int(chat_id) if chat_id else None,
                    workflow_type="pi_agent",
                )
                session.add(audit)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=audit.id,
                    position=0,
                    name="Pi Agent",
                    instruction=instruction,
                    status="running",
                    tool_used="pi",
                )
                session.add(step)
                session.commit()
                audit_id = audit.id
                step_id = step.id

            # Try RPC session first, fall back to pi -p (print mode)
            try:
                rpc = await get_or_create_rpc_session(project_id, folder)
                success = rpc.send_prompt(instruction)
                if success:
                    if chat_id:
                        try:
                            from distr.core.chat import ChatService
                            ChatService.add_message(int(chat_id), "assistant", f"[Pi: {project_name}] Instruction sent. Check the terminal tab for progress.")
                        except Exception:
                            pass
                    return JSONResponse({"success": True, "session_id": audit_id, "engine": "pi_rpc"})
            except Exception as e:
                logger.warning(f"RPC session failed, falling back to pi -p: {e}")

            # Fallback: run pi in print mode (one-shot)
            import subprocess
            import threading
            def _run_pi():
                try:
                    result = subprocess.run(
                        [pi_path, "-p", "--append-system-prompt", f"You are working on project: {project_name}", instruction],
                        capture_output=True, text=True, timeout=300,
                        cwd=folder,
                    )
                    output = (result.stdout + result.stderr).strip()[:3000]
                    status = "completed" if result.returncode == 0 else "failed"

                    # Log result to chat
                    if chat_id:
                        try:
                            from distr.core.chat import ChatService
                            ChatService.add_message(int(chat_id), "assistant", f"[Pi: {project_name}] {output[:1500]}")
                        except Exception:
                            pass
                except subprocess.TimeoutExpired:
                    pass
                except Exception as e:
                    logger.error(f"Pi execution failed: {e}", exc_info=True)

            threading.Thread(target=_run_pi, daemon=True).start()
            return JSONResponse({"success": True, "session_id": audit_id, "engine": "pi_cli"})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"CLI instruction failed: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.get("/projects/{project_id}/cli/audit")
    async def get_cli_audit(project_id: int):
        """Get audit trail of pi agent actions for a project."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                project_name = project.name or ""

                # Find pi_agent workflows for this project
                prefix = f"[Project: {project_name}]"
                workflows = (
                    session.query(AutoWorkflow)
                    .filter(
                        AutoWorkflow.name.like(f"{prefix}%"),
                        AutoWorkflow.workflow_type == "pi_agent",
                    )
                    .order_by(AutoWorkflow.created_date.desc())
                    .limit(50)
                    .all()
                )

                result = []
                for w in workflows:
                    steps = (
                        session.query(AutoWorkflowStep)
                        .filter(AutoWorkflowStep.workflow_id == w.id)
                        .order_by(AutoWorkflowStep.position)
                        .all()
                    )
                    result.append({
                        "id": w.id,
                        "instruction": (w.name or "").replace(prefix, "").strip(),
                        "status": w.status,
                        "created": w.created_date.isoformat() if w.created_date else None,
                        "steps": [
                            {
                                "id": st.id,
                                "title": st.name,
                                "status": st.status,
                                "result": (st.result or "")[:300],
                                "tool": st.tool_used or "",
                            }
                            for st in steps
                        ],
                    })
                return JSONResponse({"sessions": result})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"CLI audit failed: {e}", exc_info=True)
            return JSONResponse({"sessions": []})


    # ── Pi coding agent management ──

    @router.get("/pi/status")
    async def get_pi_status():
        """Check if pi coding agent is installed and get version."""
        from distr.core.pi_rpc import PiRpcSession
        pi_path = PiRpcSession.find_pi()
        if not pi_path:
            return JSONResponse({"installed": False, "version": None, "path": None, "running": False})
        try:
            import subprocess
            version = subprocess.run([pi_path, "--version"], capture_output=True, text=True, timeout=5)
            ver_str = version.stdout.strip().split("\n")[0] if version.returncode == 0 else None
        except Exception:
            ver_str = None
        # Check if any RPC sessions are alive
        from distr.core.pi_rpc import _rpc_sessions
        running = any(s.is_alive for s in _rpc_sessions.values())
        return JSONResponse({
            "installed": True,
            "version": ver_str,
            "path": pi_path,
            "running": running,
        })

    @router.post("/pi/login")
    async def pi_login():
        """Trigger pi login (opens browser for auth)."""
        from distr.core.pi_rpc import PiRpcSession
        pi_path = PiRpcSession.find_pi()
        if not pi_path:
            return JSONResponse({"success": False, "error": "Pi is not installed. Run: npm install -g @mariozechner/pi-coding-agent"}, status_code=400)
        try:
            import subprocess
            subprocess.Popen([pi_path, "/login"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return JSONResponse({"success": True, "message": "Login started — check your browser"})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/pi/logout")
    async def pi_logout():
        """Logout from pi."""
        from distr.core.pi_rpc import PiRpcSession
        pi_path = PiRpcSession.find_pi()
        if not pi_path:
            return JSONResponse({"success": False, "error": "Pi is not installed"}, status_code=400)
        try:
            import subprocess
            result = subprocess.run([pi_path, "/logout"], capture_output=True, text=True, timeout=10)
            return JSONResponse({"success": result.returncode == 0, "output": result.stdout.strip()})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)


    # ── SSE: real-time audit log updates ──

    @router.get("/projects/{project_id}/cli/stream")
    async def stream_cli_audit(project_id: int):
        """SSE endpoint — pushes an event whenever the project's pi agent audit changes."""
        import asyncio
        from fastapi.responses import StreamingResponse

        async def event_generator():
            last_version = 0
            while True:
                try:
                    from distr.core.db import get_session
                    from distr.core.db.projects import Project
                    from distr.core.db.workflow import AutoWorkflow
                    with get_session() as session:
                        project = session.query(Project).filter(Project.id == project_id).first()
                        if not project:
                            yield "data: {\"error\": \"project not found\"}\n\n"
                            return
                        prefix = f"[Project: {project.name or ''}]"
                        # Get latest workflow modified time as version
                        latest = (
                            session.query(AutoWorkflow)
                            .filter(
                                AutoWorkflow.name.like(f"{prefix}%"),
                                AutoWorkflow.workflow_type == "pi_agent",
                            )
                            .order_by(AutoWorkflow.modified_date.desc())
                            .first()
                        )
                        version = int(latest.modified_date.timestamp() * 1000) if latest and latest.modified_date else 0
                        if version != last_version:
                            last_version = version
                            yield f"data: {{\"version\": {version}, \"refresh\": true}}\n\n"
                except Exception:
                    pass
                await asyncio.sleep(1.5)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Terminal: WebSocket (pi RPC mode) + buffer + overview ───────

    @router.websocket("/projects/{project_id}/terminal/ws")
    async def terminal_websocket(websocket: WebSocket, project_id: int):
        """WebSocket for real-time pi RPC transcript. Connects to a pi --mode rpc session."""
        import asyncio
        from distr.core.pi_rpc import get_or_create_rpc_session, get_rpc_session, kill_rpc_session, PiRpcSession
        from distr.gui.web.security import websocket_has_valid_internal_token, is_allowed_local_origin

        # Auth check
        origin = websocket.headers.get("origin")
        if origin and not is_allowed_local_origin(origin):
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        if not websocket_has_valid_internal_token(websocket):
            await websocket.close(code=1008, reason="Unauthorized")
            return

        await websocket.accept()

        # Get project folder
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    await websocket.send_json({"type": "error", "message": "Project not found"})
                    await websocket.close(code=1008, reason="Project not found")
                    return
                cwd = project.folder_location or os.path.expanduser("~")
        except Exception as e:
            logger.error(f"Terminal: failed to load project: {e}")
            await websocket.send_json({"type": "error", "message": "Failed to load project"})
            await websocket.close(code=1011, reason="Internal error")
            return

        # Ensure the directory exists
        if not os.path.isdir(cwd):
            cwd = os.path.expanduser("~")

        # Create or get the pi RPC session
        try:
            rpc = await get_or_create_rpc_session(project_id, cwd)
        except Exception as e:
            logger.error(f"Terminal: failed to create pi RPC session: {e}")
            await websocket.send_json({"type": "error", "message": f"Failed to start pi: {e}"})
            await websocket.close(code=1011, reason="Terminal error")
            return

        # Queue for RPC events to be sent to this WebSocket
        event_queue = asyncio.Queue()

        def _on_event(event_dict):
            try:
                event_queue.put_nowait(event_dict)
            except Exception:
                pass

        rpc.add_event_callback(_on_event)

        # Send initial connection message + existing transcript
        buffer_messages = rpc.get_messages()
        await websocket.send_json({"type": "connected", "project_id": project_id, "buffer": buffer_messages})

        async def _forward_events():
            """Forward RPC events to WebSocket client."""
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=30)
                    await websocket.send_json(event)
                except asyncio.TimeoutError:
                    # Send keepalive
                    try:
                        await websocket.send_json({"type": "ping"})
                    except Exception:
                        break
                except Exception:
                    break

        # Start event forwarding task
        forward_task = asyncio.create_task(_forward_events())

        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")

                if msg_type == "prompt":
                    # User sent a prompt to pi via the terminal input
                    instruction = msg.get("message", "")
                    if instruction:
                        rpc.send_prompt(instruction)
                elif msg_type == "steer":
                    # User is steering/redirecting pi
                    instruction = msg.get("message", "")
                    if instruction:
                        rpc.steer(instruction)
                elif msg_type == "abort":
                    # User wants to abort current operation
                    rpc.abort()
                elif msg_type == "restart":
                    # Kill and restart pi RPC session
                    await kill_rpc_session(project_id)
                    try:
                        rpc = await get_or_create_rpc_session(project_id, cwd)
                        rpc.add_event_callback(_on_event)
                        await websocket.send_json({"type": "connected", "project_id": project_id, "buffer": rpc.get_messages()})
                    except Exception as e:
                        await websocket.send_json({"type": "error", "message": f"Failed to restart: {e}"})
                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"Terminal WebSocket error: {e}")
        finally:
            forward_task.cancel()
            rpc.remove_event_callback(_on_event)

    @router.get("/projects/{project_id}/terminal/buffer")
    async def get_terminal_buffer(project_id: int, lines: int = 100):
        """Get the terminal buffer content from the pi RPC session."""
        from distr.core.pi_rpc import get_rpc_session

        rpc = get_rpc_session(project_id)
        if not rpc:
            return JSONResponse({"buffer": "", "alive": False, "project_id": project_id})

        buffer_text = rpc.get_buffer(lines)
        return JSONResponse({
            "buffer": buffer_text,
            "alive": rpc.is_alive,
            "project_id": project_id,
        })

    @router.post("/projects/{project_id}/terminal/restart")
    async def restart_terminal(project_id: int):
        """Kill and recreate the pi RPC session for a project."""
        from distr.core.pi_rpc import kill_rpc_session, get_or_create_rpc_session
        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project

        try:
            with db_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                cwd = project.folder_location or os.path.expanduser("~")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        if not os.path.isdir(cwd):
            cwd = os.path.expanduser("~")

        await kill_rpc_session(project_id)
        try:
            rpc = await get_or_create_rpc_session(project_id, cwd)
            return JSONResponse({"success": True, "alive": rpc.is_alive})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/projects/{project_id}/terminal/overview")
    async def terminal_overview(project_id: int):
        """Get pi RPC session transcript, produce a natural spoken summary, and speak it aloud."""
        import asyncio
        from distr.core.pi_rpc import get_rpc_session
        from distr.core.settings import load_settings_from_db
        from distr.core.llm_factory import create_stream
        from distr.core.signals import signal_manager

        rpc = get_rpc_session(project_id)
        if not rpc:
            return JSONResponse({"error": "No pi session for this project"}, status_code=404)

        # Get structured transcript
        messages = rpc.get_messages()
        if not messages:
            return JSONResponse({"summary": "The terminal is empty — nothing has been output yet.", "empty": True})

        # Extract only user commands and assistant responses (skip thinking, tool calls, tool results)
        user_msgs = []
        assistant_msgs = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "user":
                content = (msg.get("content", "") or "").strip()
                if content:
                    user_msgs.append(content)
            elif role == "assistant":
                content = (msg.get("content", "") or "").strip()
                if content:
                    assistant_msgs.append(content)

        if not user_msgs and not assistant_msgs:
            return JSONResponse({"summary": "The terminal has no commands yet.", "empty": True})

        # Build a focused transcript for LLM summarization
        # Last 5 commands and responses, truncated for the LLM
        transcript_parts = []
        for cmd in user_msgs[-5:]:
            truncated = cmd[:200] + "..." if len(cmd) > 200 else cmd
            transcript_parts.append(f"[cmd] {truncated}")
        for resp in assistant_msgs[-5:]:
            truncated = resp[:400] + "..." if len(resp) > 400 else resp
            transcript_parts.append(f"[resp] {truncated}")

        buffer = "\n".join(transcript_parts)
        if len(buffer) > 4000:
            buffer = buffer[-4000:]

        # Get LLM settings
        settings = load_settings_from_db()
        provider = (settings.get("agent_provider") or settings.get("default_provider") or "ollama").strip()
        model = (settings.get("agent_model_name") or settings.get("default_model_name") or "").strip()
        if not provider:
            provider = "ollama"
        if not model:
            model = "llama3.2" if provider == "ollama" else "gpt-4o-mini"

        # LLM prompt: produce natural spoken language for TTS
        system_prompt = (
            "You produce short TTS-friendly summaries of terminal activity. "
            "Rules:\n"
            "1. Speak naturally, as if talking to a colleague.\n"
            "2. Never say file paths, directory trees, or raw command output.\n"
            "3. Describe what happened in plain English — e.g. 'listed the project files', 'checked the config', 'read the main source file'.\n"
            "4. Quantify when useful — 'about 30 files', 'a few hundred lines'.\n"
            "5. 1-2 sentences max.\n"
            "6. If there were errors, mention the outcome not the error details.\n"
            "Examples:\n"
            "- 'Listed the project directory — about 30 files including the main source and config.'\n"
            "- 'Read the config file — it has database and API settings.'\n"
            "- 'Found an error so listed the directory instead — about 15 files.'\n"
        )
        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Terminal transcript:\n" + buffer},
        ]

        # Run LLM in thread pool so it doesn't block uvicorn
        def _summarize():
            try:
                summary_parts = []
                for token in create_stream(provider, model, llm_messages, settings):
                    summary_parts.append(token)
                return "".join(summary_parts).strip()
            except Exception as e:
                logger.error(f"Terminal overview LLM call failed: {e}", exc_info=True)
                return f"Error: {str(e)[:200]}"

        try:
            loop = asyncio.get_running_loop()
            summary = await loop.run_in_executor(None, _summarize)
        except Exception as e:
            logger.error(f"Terminal overview executor failed: {e}", exc_info=True)
            summary = f"Error: {str(e)[:200]}"

        # Speak the summary aloud
        try:
            logger.info(f"Terminal overview: speaking {len(summary)} chars")
            signal_manager.speak_text_directly.emit(summary)
        except Exception as e:
            logger.warning(f"Failed to speak terminal overview: {e}", exc_info=True)

        return JSONResponse({"summary": summary, "empty": False, "buffer_lines": len(messages)})

        # For longer sessions, build a focused transcript for LLM summarization
        # Only include user commands and final assistant responses
        transcript_parts = []
        for i, cmd in enumerate(user_msgs):
            transcript_parts.append(f"[cmd {i+1}] {cmd}")
        for i, resp in enumerate(assistant_msgs):
            # Truncate each response to keep the LLM input manageable
            truncated = resp[:600] + "..." if len(resp) > 600 else resp
            transcript_parts.append(f"[resp {i+1}] {truncated}")

        buffer = "\n".join(transcript_parts)
        if len(buffer) > 8000:
            buffer = buffer[-8000:]

        # Get LLM settings
        settings = load_settings_from_db()
        provider = (settings.get("agent_provider") or settings.get("default_provider") or "ollama").strip()
        model = (settings.get("agent_model_name") or settings.get("default_model_name") or "").strip()
        if not provider:
            provider = "ollama"
        if not model:
            model = "llama3.2" if provider == "ollama" else "gpt-4o-mini"

        # Call LLM to summarize — focus on commands and results only
        system_prompt = (
            "You summarize terminal sessions. You receive a transcript containing user commands [cmd N] "
            "and agent responses [resp N]. Give a 1-2 sentence spoken summary of what commands ran "
            "and what the key results were. Speak naturally. Examples:\n"
            "- 'ls showed 12 files in the project directory including package.json and src.'\n"
            "- 'read failed on that path because it's a directory, so ls was used instead to list the contents.'\n"
        )
        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Terminal transcript:\n\n" + buffer},
        ]

        # Run the LLM call in a thread pool so it doesn't block the uvicorn event loop
        def _summarize():
            try:
                summary_parts = []
                for token in create_stream(provider, model, llm_messages, settings):
                    summary_parts.append(token)
                return "".join(summary_parts).strip()
            except Exception as e:
                logger.error(f"Terminal overview LLM call failed: {e}", exc_info=True)
                return f"Error summarizing: {str(e)[:200]}"

        try:
            loop = asyncio.get_running_loop()
            summary = await loop.run_in_executor(None, _summarize)
        except Exception as e:
            logger.error(f"Terminal overview executor failed: {e}", exc_info=True)
            summary = f"Error: {str(e)[:200]}"

        # Speak the summary aloud
        try:
            logger.info(f"Terminal overview (LLM): speaking {len(summary)} chars")
            signal_manager.speak_text_directly.emit(summary)
        except Exception as e:
            logger.warning(f"Failed to speak terminal overview: {e}", exc_info=True)

        return JSONResponse({"summary": summary, "empty": False, "buffer_lines": len(messages)})
