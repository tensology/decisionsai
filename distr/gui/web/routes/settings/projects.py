"""
Projects routes — /projects/*, /browse-folder
"""
from fastapi import HTTPException, File, UploadFile, Form
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
        """Set this project as the one in use (only one can be in use)."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            with get_session() as session:
                session.query(Project).filter(Project.in_use == True).update({"in_use": False})
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                project.in_use = True
                session.commit()
                return JSONResponse({"success": True})
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
