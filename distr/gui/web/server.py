"""
Unified FastAPI server for GUI components (Board, Settings, Chat)

This server runs once when the app starts and serves all web UIs.
"""
import sys
import threading
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse
from pathlib import Path
import logging
import uvicorn
from typing import Optional
import time
import requests
from distr.gui.web.security import (
    ALLOWED_LOCAL_ORIGINS,
    INTERNAL_AUTH_HEADER,
    get_internal_api_token,
    require_internal_token_request,
    is_allowed_local_origin,
    rate_limiter,
)

logger = logging.getLogger(__name__)

# Server configuration
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Singleton instance
_unified_server: Optional['UnifiedGuiServer'] = None


def get_unified_server() -> Optional['UnifiedGuiServer']:
    """Get the global unified server instance"""
    return _unified_server


def _mount_static(app: FastAPI, url_path: str, directory: Path, name: str):
    """Mount a static directory if it exists (no-op otherwise)."""
    if directory.exists():
        app.mount(url_path, StaticFiles(directory=str(directory)), name=name)


def create_app() -> FastAPI:
    """
    Create and configure the unified FastAPI app
    
    Returns:
        Configured FastAPI app with both flow and board routes
    """
    app = FastAPI(title="Decisions AI GUI Server", redirect_slashes=False)
    app.state.internal_api_token = get_internal_api_token()
    
    # Add CORS middleware to allow requests from QWebEngineView
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_LOCAL_ORIGINS,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", INTERNAL_AUTH_HEADER],
    )
    
    # All web assets live under distr/gui/web/
    web_dir = Path(__file__).parent
    project_root = web_dir.parent.parent.parent
    assets_dir = project_root / "assets"
    templates_dir = web_dir / "templates"
    static_dir = web_dir / "static"

    # Mount project assets (favicon, icons, etc.) at /assets
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # --- Static file mounts ---
    # Shared (base CSS, shared JS — used by all pages)
    _mount_static(app, "/static/shared/css", static_dir / "shared" / "css", "shared_css")
    _mount_static(app, "/static/shared/js", static_dir / "shared" / "js", "shared_js")
    _mount_static(app, "/static/vendor", static_dir / "vendor", "vendor")
    # Catch-all for the entire static directory (covers /static/vendor/* etc.)
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir), html=False), name="static_root")

    # Per-page static files
    _mount_static(app, "/board/js", static_dir / "board", "board_js")
    _mount_static(app, "/board/css", static_dir / "board", "board_css")
    _mount_static(app, "/settings/static/css", static_dir / "settings" / "css", "settings_css")
    _mount_static(app, "/settings/static/js", static_dir / "settings" / "js", "settings_js")
    _mount_static(app, "/chat/static/css", static_dir / "chat" / "css", "chat_css")
    _mount_static(app, "/chat/static/js", static_dir / "chat" / "js", "chat_js")
    _mount_static(app, "/docs/static/css", static_dir / "docs" / "css", "docs_css")
    _mount_static(app, "/docs/static/js", static_dir / "docs" / "js", "docs_js")
    _mount_static(app, "/kanban/static/js", static_dir / "kanban" / "js", "kanban_js")
    _mount_static(app, "/actions/static/js", static_dir / "actions" / "js", "actions_js")
    _mount_static(app, "/workflows/static/js", static_dir / "workflows" / "js", "workflows_js")
    _mount_static(app, "/skills/static/js", static_dir / "skills" / "js", "skills_js")
    _mount_static(app, "/projects/static/js", static_dir / "projects" / "js", "projects_js")
    _mount_static(app, "/oauth/static/css", static_dir / "oauth" / "css", "oauth_css")
    _mount_static(app, "/oauth/static/js", static_dir / "oauth" / "js", "oauth_js")
    _mount_static(app, "/static/img", static_dir / "img", "static_img")

    # Legacy sub-dir paths (kept for backward compat)
    board_templates_dir = templates_dir / "board"
    chat_templates_dir = templates_dir / "chat"
    settings_templates_dir = templates_dir / "settings"

    # Add middleware to disable caching for static files in development
    class NoCacheMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            path = request.url.path
            if any(path.startswith(p) for p in [
                "/board/", "/settings/static/", "/chat/static/", "/docs/static/",
                "/kanban/static/", "/actions/static/", "/workflows/static/",
                "/skills/static/", "/projects/static/", "/oauth/static/",
                "/static/shared/",
            ]):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response
    
    app.add_middleware(NoCacheMiddleware)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        path = request.url.path
        method = request.method.upper()
        if method == "OPTIONS":
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "font-src 'self' data:; "
                "img-src 'self' data:; "
                "media-src 'self' blob:; "
                "connect-src 'self' ws: wss:; "
                "frame-ancestors 'none';"
            )
            return response
        sensitive_settings_reads = {
            "/api/thirdparty",
            "/api/advanced/accounts",
        }
        try:
            if path.startswith("/api/"):
                if method != "GET" or path in sensitive_settings_reads:
                    require_internal_token_request(request)
            if path.startswith("/api/internal/"):
                require_internal_token_request(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            "img-src 'self' data:; "
            "media-src 'self' blob:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none';"
        )
        return response

    def _template_context(request: Request, base_path: str) -> dict:
        # Starlette 1.0+: request is passed as first arg to TemplateResponse, not in context
        return {
            "base_path": base_path,
            "internal_api_token": app.state.internal_api_token,
        }
    
    try:
        from distr.gui.web.routes.board import create_routes as create_board_routes
        board_router = create_board_routes(board_templates_dir, base_path="/board")
        app.include_router(board_router, prefix="/board", tags=["board"])
        logger.info("Board routes mounted at /board")
    except Exception as e:
        logger.error("Failed to load Board routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.settings import create_routes as create_settings_routes
        settings_router = create_settings_routes(settings_templates_dir, base_path="/settings")
        app.include_router(settings_router, prefix="/api", tags=["settings"])
        logger.info("Settings API routes mounted at /api")
    except Exception as e:
        logger.error("Failed to load Settings routes: %s", e, exc_info=True)

    # --- Page templates ---
    # Root templates dir contains base.html + components/ (shared layout).
    # Each page has its own subdirectory: settings/, chat/, kanban/, etc.
    # Jinja2 resolves {% extends "base.html" %} from the root.
    page_templates = Jinja2Templates(directory=str(templates_dir))

    # Settings page (tabbed: general, audio, thirdparty, llms, advanced, logs)
    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page_no_slash(request: Request):
        return page_templates.TemplateResponse(request, "settings/settings.html", _template_context(request, "/settings"))

    @app.get("/settings/", response_class=HTMLResponse)
    async def settings_page_with_slash(request: Request):
        return page_templates.TemplateResponse(request, "settings/settings.html", _template_context(request, "/settings"))

    # Chat page
    @app.get("/chat", response_class=HTMLResponse)
    async def chat_page_no_slash(request: Request):
        return page_templates.TemplateResponse(request, "chat/chat.html", _template_context(request, "/chat"))

    @app.get("/chat/", response_class=HTMLResponse)
    async def chat_page_with_slash(request: Request):
        return page_templates.TemplateResponse(request, "chat/chat.html", _template_context(request, "/chat"))

    try:
        from distr.gui.web.routes.chat import create_routes as create_chat_routes
        chat_router = create_chat_routes(chat_templates_dir, base_path="/chat")
        app.include_router(chat_router, prefix="/api", tags=["chat"])
        logger.info("Chat API routes mounted at /api")
    except Exception as e:
        logger.error("Failed to load Chat routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.docs import create_routes as create_docs_routes
        docs_router = create_docs_routes()
        app.include_router(docs_router, prefix="/docs/api", tags=["docs"])
        logger.info("Docs API routes mounted at /docs/api")
    except Exception as e:
        logger.error("Failed to load Docs routes: %s", e, exc_info=True)

    # Standalone pages — each has its own template directory
    @app.get("/actions/", response_class=HTMLResponse)
    async def actions_page(request: Request):
        return page_templates.TemplateResponse(request, "actions/actions.html", _template_context(request, "/actions"))

    @app.get("/actions", response_class=HTMLResponse)
    async def actions_redirect():
        return RedirectResponse(url="/actions/", status_code=302)

    @app.get("/skills/", response_class=HTMLResponse)
    async def skills_page(request: Request):
        return page_templates.TemplateResponse(request, "skills/skills.html", _template_context(request, "/skills"))

    @app.get("/skills", response_class=HTMLResponse)
    async def skills_redirect():
        return RedirectResponse(url="/skills/", status_code=302)

    # Projects API — list projects for push dropdown
    @app.get("/api/projects")
    async def get_projects():
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            session = get_session()
            projects = session.query(Project).all()
            return [{"name": p.name, "path": p.folder} for p in projects if p.folder]
        except Exception:
            return []

    # Skills API — serve the skills registry
    @app.get("/api/skills")
    async def get_skills_registry():
        import json as _json
        skills_root = project_root / "skills"
        registry_file = skills_root / "skills_registry.json"
        if registry_file.exists():
            return _json.loads(registry_file.read_text())
        return []

    @app.get("/api/skills/{skill_id}")
    async def get_skill_detail(skill_id: str):
        skills_root = project_root / "skills"
        skill_dir = skills_root / skill_id
        if not skill_dir.exists() or not skill_dir.is_dir():
            raise HTTPException(status_code=404, detail="Skill not found")
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            skill_file = skill_dir / "skill.md"
        if not skill_file.exists():
            raise HTTPException(status_code=404, detail="Skill SKILL.md not found")
        content = skill_file.read_text()
        return {"id": skill_id, "content": content}

    @app.post("/api/skills/{skill_id}/push")
    async def push_skill_to_project(skill_id: str, request: Request):
        """Push a skill to a project's CLI command directory."""
        import json as _json
        import shutil
        body = await request.json()
        project_path = body.get("project_path", ".")
        target = body.get("target", "pi")
        instructions = body.get("instructions", "")

        # Supported CLI targets
        cli_targets = {
            "pi": ".pi/skills",
            "claude": ".claude/commands",
            "cursor": ".cursor/commands",
            "gemini": ".gemini/commands",
            "codex": ".codex/commands",
        }
        target_dir_name = cli_targets.get(target.lower())
        if not target_dir_name:
            raise HTTPException(status_code=400, detail=f"Unsupported target '{target}'. Supported: {', '.join(cli_targets.keys())}")

        skills_root = project_root / "skills"
        skill_dir = skills_root / skill_id
        if not skill_dir.exists() or not skill_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            skill_file = skill_dir / "skill.md"
        if not skill_file.exists():
            raise HTTPException(status_code=404, detail="SKILL.md not found")

        # Read skill name from frontmatter
        skill_name = skill_id
        try:
            fm_content = skill_file.read_text()
            if fm_content.startswith("---"):
                end = fm_content.find("---", 3)
                if end > 0:
                    for line in fm_content[3:end].split("\n"):
                        if line.strip().startswith("name:"):
                            skill_name = line.split(":", 1)[1].strip().strip('"').strip("'")
                            break
        except Exception:
            pass

        # Resolve project path
        from pathlib import Path as P
        project = P(project_path).resolve()
        if not project.exists():
            project.mkdir(parents=True, exist_ok=True)

        target_dir = project / target_dir_name
        target_dir.mkdir(parents=True, exist_ok=True)

        # For pi: directory/SKILL.md (Agent Skills spec). For others: flat .md file
        if target.lower() == "pi":
            dest_skill_dir = target_dir / skill_id
            dest_skill_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_skill_dir / "SKILL.md"
            shutil.copy2(skill_file, dest_file)
            # Copy supporting files into the skill directory
            pushed_files = [str(dest_file)]
            for subdir_name in ["scripts", "references", "reference"]:
                subdir = skill_dir / subdir_name
                if subdir.exists() and subdir.is_dir():
                    dest_subdir = dest_skill_dir / subdir_name
                    if dest_subdir.exists():
                        shutil.rmtree(dest_subdir)
                    shutil.copytree(subdir, dest_subdir)
                    pushed_files.append(str(dest_subdir))
        else:
            dest_file = target_dir / f"{skill_id}.md"
            shutil.copy2(skill_file, dest_file)
            pushed_files = [str(dest_file)]
            for subdir_name in ["scripts", "references", "reference"]:
                subdir = skill_dir / subdir_name
                if subdir.exists() and subdir.is_dir():
                    dest_subdir = target_dir / skill_id / subdir_name
                    if dest_subdir.exists():
                        shutil.rmtree(dest_subdir)
                    shutil.copytree(subdir, dest_subdir)
                    pushed_files.append(str(dest_subdir))

        return {
            "success": True,
            "skill_id": skill_id,
            "skill_name": skill_name,
            "target": target,
            "destination": str(dest_file),
            "files": pushed_files,
            "message": f"Pushed '{skill_name}' to {target}! Run: /skill:{skill_id}" + (f" with: {instructions}" if instructions else "")
        }

    @app.get("/projects/", response_class=HTMLResponse)
    async def projects_page(request: Request):
        return page_templates.TemplateResponse(request, "projects/projects.html", _template_context(request, "/projects"))

    @app.get("/projects", response_class=HTMLResponse)
    async def projects_redirect():
        return RedirectResponse(url="/projects/", status_code=302)

    @app.get("/workflows/", response_class=HTMLResponse)
    async def workflows_page(request: Request):
        return page_templates.TemplateResponse(request, "workflows/workflows.html", _template_context(request, "/workflows"))

    @app.get("/workflows", response_class=HTMLResponse)
    async def workflows_redirect():
        return RedirectResponse(url="/workflows/", status_code=302)

    # Ticket Board
    @app.get("/kanban/", response_class=HTMLResponse)
    async def kanban_page(request: Request):
        return page_templates.TemplateResponse(request, "kanban/kanban.html", _template_context(request, "/kanban"))

    @app.get("/kanban", response_class=HTMLResponse)
    async def kanban_redirect():
        return RedirectResponse(url="/kanban/", status_code=302)

    try:
        from distr.gui.web.routes.kanban import create_routes as create_kanban_routes
        kanban_router = create_kanban_routes()
        app.include_router(kanban_router, prefix="/api", tags=["kanban"])
        logger.info("Ticket Board API routes mounted at /api")
    except Exception as e:
        logger.error("Failed to load Ticket Board routes: %s", e, exc_info=True)

    # Legacy: redirect old step-runner URLs to workflows
    @app.get("/step-runner/", response_class=HTMLResponse)
    async def step_runner_redirect_to_workflows():
        return RedirectResponse(url="/workflows/", status_code=302)

    @app.get("/step-runner", response_class=HTMLResponse)
    async def step_runner_redirect():
        return RedirectResponse(url="/workflows/", status_code=302)

    @app.get("/docs/", response_class=HTMLResponse)
    async def docs_page(request: Request):
        return page_templates.TemplateResponse(request, "docs/docs.html", _template_context(request, "/docs"))

    @app.get("/docs", response_class=HTMLResponse)
    async def docs_redirect():
        return RedirectResponse(url="/docs/", status_code=302)

    @app.get("/")
    async def root_redirect():
        return RedirectResponse(url="/settings", status_code=302)

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {"status": "ok", "services": ["flow", "board", "settings", "chat"]}
    
    # Run one-time ticket board settings migration on startup
    @app.on_event("startup")
    async def _run_kanban_migration():
        try:
            from distr.core.kanban.migration import migrate_board_agent_settings_to_global
            migrate_board_agent_settings_to_global()
        except Exception as e:
            logger.warning("Ticket Board settings migration could not run: %s", e)

    # Check model recommendations staleness on startup
    @app.on_event("startup")
    async def _check_model_recommendations():
        try:
            from distr.core.services.model_recommendations import (
                is_stale, refresh_recommendations, _refresh_running
            )
            if is_stale() and not _refresh_running:
                import threading
                threading.Thread(target=refresh_recommendations, daemon=True).start()
                logger.info("Model recommendations stale — background refresh started")
        except Exception as e:
            logger.warning("Could not check model recommendations: %s", e)

    return app


class UnifiedGuiServer:
    """
    Manages the unified FastAPI server lifecycle in a separate thread.
    Started once when the app loads, stopped when the app shuts down.
    """
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.app: Optional[FastAPI] = None
        self.server: Optional[uvicorn.Server] = None
        self.server_thread: Optional[threading.Thread] = None
        self.is_running = False
    
    def _run_server_thread(self):
        """Run the server in a thread"""
        try:
            logger.info("Creating unified GUI app...")
            self.app = create_app()
            logger.info("App created, starting uvicorn on %s:%s", self.host, self.port)

            config = uvicorn.Config(
                self.app,
                host=self.host,
                port=self.port,
                log_level="warning",
                loop="asyncio",
            )
            self.server = uvicorn.Server(config)
            self.server.run()
        except Exception as e:
            logger.error("Unified GUI server thread error: %s", e, exc_info=True)
        finally:
            self.is_running = False
    
    def start(self):
        """Start the server in a separate thread"""
        global _unified_server
        
        if self.is_running and self.server_thread and self.server_thread.is_alive():
            logger.warning("Unified GUI server is already running")
            return
        
        # Clean up any existing thread first
        if self.server_thread and self.server_thread.is_alive():
            logger.warning("Stopping existing server before starting new one")
            self.stop()
            time.sleep(0.5)
        
        try:
            # Check if port is already in use
            import socket
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                test_socket.bind((self.host, self.port))
                test_socket.close()
            except OSError:
                # Port is in use, try next ports
                logger.warning(f"Port {self.port} is already in use, trying next port")
                self.port += 1
                for _ in range(5):
                    try:
                        test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        test_socket.bind((self.host, self.port))
                        test_socket.close()
                        logger.info(f"Using port {self.port} instead")
                        break
                    except OSError:
                        self.port += 1
                else:
                    logger.error("Could not find an available port")
                    self.is_running = False
                    return
            
            # Start server in a separate thread
            self.server_thread = threading.Thread(
                target=self._run_server_thread,
                daemon=True
            )
            self.server_thread.start()
            self.is_running = True
            _unified_server = self
            
            # Wait a moment for the server to initialize
            time.sleep(0.5)
            
            logger.info("Unified GUI server started on %s:%s", self.host, self.port)
            
        except Exception as e:
            logger.error(f"Failed to start Unified GUI server: {e}")
            self.is_running = False
    
    def stop(self):
        """Stop the server thread"""
        global _unified_server
        
        if not self.is_running:
            return
        
        try:
            # Shut down uvicorn server gracefully
            if self.server:
                self.server.should_exit = True
            
            self.is_running = False
            
            # Wait for thread to finish (with timeout)
            if self.server_thread and self.server_thread.is_alive():
                self.server_thread.join(timeout=2.0)
                if self.server_thread.is_alive():
                    logger.warning("Unified GUI server thread didn't stop gracefully")
            
            _unified_server = None
            logger.info("Unified GUI server stopped")
        except Exception as e:
            logger.error(f"Error stopping Unified GUI server: {e}")
            self.is_running = False
    
    def get_url(self) -> str:
        """Get the base URL of the server"""
        return f"http://{self.host}:{self.port}"
    
    def get_flow_url(self) -> str:
        """Get the Flow Logic base URL"""
        return f"{self.get_url()}/flow"
    
    def get_board_url(self) -> str:
        """Get the Board base URL"""
        return f"{self.get_url()}/board"

    def get_settings_url(self) -> str:
        """Get the Settings base URL"""
        return f"{self.get_url()}/settings"

    def get_chat_url(self) -> str:
        """Get the Chat base URL"""
        return f"{self.get_url()}/chat"

    def is_ready(self) -> bool:
        """Check if the server is running and ready by attempting to connect"""
        if not self.is_running or self.server_thread is None:
            return False
        
        # Check if thread is still alive
        if not self.server_thread.is_alive():
            self.is_running = False
            return False
        
        # Verify with HTTP request
        try:
            url = f"http://{self.host}:{self.port}/health"
            response = requests.get(url, timeout=0.5)
            return response.status_code == 200
        except Exception:
            return False
