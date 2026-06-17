"""
Unified FastAPI server for GUI components (Board, Settings, Chat)

This server runs once when the app starts and serves all web UIs.
"""
import threading
import re
import os
import shutil
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
)

logger = logging.getLogger(__name__)

# Server configuration
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Singleton instance
_unified_server: Optional['UnifiedGuiServer'] = None
_APP_VERSION_CACHE: str | None = None


def get_unified_server() -> Optional['UnifiedGuiServer']:
    """Get the global unified server instance"""
    return _unified_server


def _app_version_label(project_root: Path) -> str:
    """Short version label for the web chrome."""
    global _APP_VERSION_CACHE
    if _APP_VERSION_CACHE:
        return _APP_VERSION_CACHE
    try:
        version = ""
        changelog = project_root / "CHANGELOG.md"
        if changelog.exists():
            match = re.search(r"^## \[([0-9][^\]]*)\]", changelog.read_text(encoding="utf-8"), re.M)
            version = match.group(1).strip() if match else ""
        _APP_VERSION_CACHE = version or "dev"
    except Exception:
        _APP_VERSION_CACHE = "dev"
    return _APP_VERSION_CACHE


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
    os.environ.setdefault("DECISIONSAI_INTERNAL_API_TOKEN", app.state.internal_api_token)
    
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
    _mount_static(app, "/irc/static/css", static_dir / "irc" / "css", "irc_css")
    _mount_static(app, "/irc/static/js", static_dir / "irc" / "js", "irc_js")
    _mount_static(app, "/tickets/static/js", static_dir / "kanban" / "js", "tickets_js")
    _mount_static(app, "/kanban/static/js", static_dir / "kanban" / "js", "kanban_js_legacy")
    _mount_static(app, "/automations/static/js", static_dir / "automations" / "js", "automations_js")
    _mount_static(app, "/actions/static/js", static_dir / "actions" / "js", "actions_js")
    _mount_static(app, "/snippets/static/js", static_dir / "snippets" / "js", "snippets_js")
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
                "/tickets/static/", "/kanban/static/", "/automations/static/", "/actions/static/", "/workflows/static/",
                "/skills/static/", "/projects/static/", "/oauth/static/",
                "/static/shared/",
            ]):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response
    
    app.add_middleware(NoCacheMiddleware)

    @app.middleware("http")
    async def legacy_kanban_api_alias(request: Request, call_next):
        """Deprecated: rewrite /api/kanban/* to /api/tickets/* for older clients.

        Remote app and desktop UI use /api/tickets/* directly. This shim remains
        for legacy integrations until they are updated.
        """
        path = request.url.path
        if path.startswith("/api/kanban"):
            scope = request.scope
            scope["path"] = "/api/tickets" + path[len("/api/kanban"):]
            raw_path = scope.get("raw_path")
            if isinstance(raw_path, (bytes, bytearray)):
                scope["raw_path"] = scope["path"].encode("latin-1")
        return await call_next(request)

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
        unauthenticated_local_posts = {
            "/api/harness/events",
        }
        sensitive_settings_reads = {
            "/api/thirdparty",
            "/api/advanced/accounts",
        }
        try:
            if path.startswith("/api/"):
                if path not in unauthenticated_local_posts and (method != "GET" or path in sensitive_settings_reads):
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
            "app_version": _app_version_label(project_root),
        }

    @app.post("/api/harness/events")
    async def receive_harness_event(request: Request):
        try:
            raw = await request.json()
            data = raw if isinstance(raw, dict) else {}
        except Exception:
            data = {}
        try:
            from distr.core.harness_events import HarnessEventPayload, record_harness_event_silently

            allowed = set(HarnessEventPayload.__dataclass_fields__.keys())
            payload = HarnessEventPayload(**{key: value for key, value in data.items() if key in allowed})
            return JSONResponse(record_harness_event_silently(payload))
        except Exception:
            return JSONResponse({"success": True, "silent": True})
    
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

    @app.get("/diagram", response_class=HTMLResponse)
    async def diagram_page_no_slash(request: Request):
        return page_templates.TemplateResponse(request, "diagram/diagram.html", _template_context(request, "/diagram"))

    @app.get("/diagram/", response_class=HTMLResponse)
    async def diagram_page_with_slash(request: Request):
        return page_templates.TemplateResponse(request, "diagram/diagram.html", _template_context(request, "/diagram"))

    @app.get("/downloads", response_class=HTMLResponse)
    async def downloads_page_no_slash(request: Request):
        return page_templates.TemplateResponse(request, "downloads/downloads.html", _template_context(request, "/downloads"))

    @app.get("/downloads/", response_class=HTMLResponse)
    async def downloads_page_with_slash(request: Request):
        return page_templates.TemplateResponse(request, "downloads/downloads.html", _template_context(request, "/downloads"))

    @app.get("/irc", response_class=HTMLResponse)
    async def irc_page_no_slash(request: Request):
        return page_templates.TemplateResponse(request, "irc/irc.html", _template_context(request, "/irc"))

    @app.get("/irc/", response_class=HTMLResponse)
    async def irc_page_with_slash(request: Request):
        return page_templates.TemplateResponse(request, "irc/irc.html", _template_context(request, "/irc"))

    try:
        from distr.gui.web.routes.integrations_hooks import router as integrations_hooks_router

        app.include_router(integrations_hooks_router)
        logger.info("Integration webhooks mounted at POST /hooks/slack/events")
    except Exception as e:
        logger.error("Failed to load integration webhook routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.chat import create_routes as create_chat_routes
        chat_router = create_chat_routes(chat_templates_dir, base_path="/chat")
        app.include_router(chat_router, prefix="/api", tags=["chat"])
        logger.info("Chat API routes mounted at /api")
    except Exception as e:
        logger.error("Failed to load Chat routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.diagrams import create_routes as create_diagram_routes
        diagram_router = create_diagram_routes(templates_dir, base_path="/diagram")
        app.include_router(diagram_router, prefix="/api", tags=["diagrams"])
        logger.info("Diagram API routes mounted at /api/diagrams")
    except Exception as e:
        logger.error("Failed to load Diagram routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.downloads import create_routes as create_download_routes
        download_router = create_download_routes(templates_dir, base_path="/downloads")
        app.include_router(download_router, prefix="/api", tags=["downloads"])
        logger.info("Download API routes mounted at /api/downloads")
    except Exception as e:
        logger.error("Failed to load Download routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.irc import create_routes as create_irc_routes
        irc_router = create_irc_routes()
        app.include_router(irc_router, prefix="/api", tags=["irc"])
        logger.info("IRC chat proxy routes mounted at /api/irc")
    except Exception as e:
        logger.error("Failed to load IRC chat routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.ide_bridge import create_routes as create_ide_bridge_routes
        ide_bridge_router = create_ide_bridge_routes()
        app.include_router(ide_bridge_router, prefix="/api", tags=["ide_bridge"])
        logger.info("IDE bridge routes mounted at /api/ide")
    except Exception as e:
        logger.error("Failed to load IDE bridge routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.orchestrator_memory import create_routes as create_orchestrator_memory_routes
        orchestrator_memory_router = create_orchestrator_memory_routes()
        app.include_router(orchestrator_memory_router, prefix="/api", tags=["orchestrator"])
        logger.info("Orchestrator memory routes mounted at /api/orchestrator")
    except Exception as e:
        logger.error("Failed to load Orchestrator memory routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.docs import create_routes as create_docs_routes
        docs_router = create_docs_routes()
        app.include_router(docs_router, prefix="/docs/api", tags=["docs"])
        logger.info("Docs API routes mounted at /docs/api")
    except Exception as e:
        logger.error("Failed to load Docs routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.events_stream import router as events_stream_router

        app.include_router(events_stream_router)
        logger.info("Event SSE mounted at GET /api/events/stream")
    except Exception as e:
        logger.error("Failed to load events stream routes: %s", e, exc_info=True)

    @app.get("/api/sidecar/health")
    async def api_sidecar_health():
        """Whether the local sidecar responds on ``GET /health`` (TASK 14 / R23)."""
        try:
            from distr.core.agent.tools.input.sidecar_http import sidecar_base_url, sidecar_health

            base = sidecar_base_url()
            body = sidecar_health(timeout=2.0)
            if body is None:
                return JSONResponse(
                    {"ok": False, "base_url": base, "sidecar_ok": False},
                    status_code=503,
                )
            out: dict = {"ok": True, "base_url": base, "sidecar_ok": True}
            for k, v in body.items():
                if k != "ok":
                    out[k] = v
            return JSONResponse(out)
        except Exception as e:
            logger.warning("api_sidecar_health failed: %s", e)
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # Standalone pages — each has its own template directory
    @app.get("/actions/", response_class=HTMLResponse)
    async def actions_page(request: Request):
        return page_templates.TemplateResponse(request, "actions/actions.html", _template_context(request, "/actions"))

    @app.get("/actions", response_class=HTMLResponse)
    async def actions_redirect():
        return RedirectResponse(url="/actions/", status_code=302)

    @app.get("/snippets/", response_class=HTMLResponse)
    async def snippets_page(request: Request):
        return page_templates.TemplateResponse(request, "snippets/snippets.html", _template_context(request, "/snippets"))

    @app.get("/snippets", response_class=HTMLResponse)
    async def snippets_redirect():
        return RedirectResponse(url="/snippets/", status_code=302)

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
            return [
                {"name": p.name, "path": p.folder_location}
                for p in projects
                if (p.folder_location or "").strip()
            ]
        except Exception:
            return []

    # Skills API — serve the skills registry
    def _resolve_skill_directory(skill_id: str):
        from distr.core.skills.catalog import registry_entry_for

        skills_root = project_root / "skills"
        entry = registry_entry_for(skill_id)
        if entry:
            raw_path = str(entry.get("path") or "").strip()
            if raw_path:
                candidate = project_root / raw_path if "/" in raw_path else skills_root / raw_path
                if candidate.exists() and candidate.is_dir():
                    return candidate
        candidate = skills_root / skill_id
        if candidate.exists() and candidate.is_dir():
            return candidate
        return None

    @app.get("/api/skills")
    async def get_skills_registry():
        from distr.core.skills.catalog import load_registry

        return [dict(row) for row in load_registry()]

    @app.get("/api/skills/{skill_id}")
    async def get_skill_detail(skill_id: str):
        from distr.core.skills.catalog import skill_file_for_id

        skill_file = skill_file_for_id(skill_id)
        if not skill_file:
            raise HTTPException(status_code=404, detail="Skill not found")
        content = skill_file.read_text(encoding="utf-8", errors="replace")
        return {"id": skill_id, "content": content}

    @app.post("/api/skills/{skill_id}/spoken-overview")
    async def skill_spoken_overview(skill_id: str):
        """LLM overview of a skill (subagent-style) plus TTS audio as base64 MP3 for the Skills UI."""
        import asyncio
        import base64

        from distr.core.skills.catalog import skill_file_for_id

        skill_file = skill_file_for_id(skill_id)
        if not skill_file:
            raise HTTPException(status_code=404, detail="Skill not found")

        raw = skill_file.read_text(encoding="utf-8", errors="replace")
        body = raw
        if body.startswith("---"):
            end = body.find("---", 3)
            if end > 0:
                body = body[end + 3 :].strip()
        if len(body) > 14000:
            body = body[:14000] + "\n\n[Document truncated for overview.]"

        from distr.core.settings import load_settings_from_db
        from distr.core.llm_factory import create_stream
        from distr.gui.web.routes.settings.projects import _resolve_terminal_overview_llm
        from distr.core.audio.tts_handler import generate_tts_audio, wav_to_mp3

        settings = load_settings_from_db()
        provider, model = _resolve_terminal_overview_llm(settings)

        system_prompt = (
            "You are a subagent that explains Pi CLI skills (markdown SKILL.md documents) to a developer. "
            "Your reply will be read aloud by text-to-speech and shown on screen.\n\n"
            "Rules:\n"
            "- Plain English only. No markdown, no headings, no bullet characters, no numbered lists, no code fences.\n"
            "- Do not read URLs, file paths, or YAML literally; describe ideas in words.\n"
            "- Cover what the skill helps with, when to use it, and what outcome to expect.\n"
            "- Four to eight sentences. Warm, concise, confident.\n"
        )
        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Skill id: {skill_id}\n\nSkill document:\n{body}"},
        ]

        def _generate_overview() -> str:
            parts = []
            try:
                for token in create_stream(provider, model, llm_messages, settings):
                    parts.append(token)
                return "".join(parts).strip()
            except Exception as exc:
                logger.error("Skill overview LLM failed: %s", exc, exc_info=True)
                raise

        try:
            overview = await asyncio.get_running_loop().run_in_executor(None, _generate_overview)
        except Exception as exc:
            from distr.core.llm_errors import format_model_error

            raise HTTPException(
                status_code=500,
                detail=format_model_error(
                    exc,
                    provider=provider,
                    model=model,
                    operation="generate a skill overview",
                ),
            )

        if not overview:
            raise HTTPException(status_code=500, detail="Overview was empty.")

        speed = float(settings.get("playback_speed") or 1.0)
        speed = max(0.5, min(2.0, speed))

        try:
            wav_path = await asyncio.wait_for(
                asyncio.to_thread(generate_tts_audio, overview, None, None, speed),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Skill overview TTS timed out after 30s — returning text-only response")
            return JSONResponse({"overview": overview, "audio_base64": None, "audio_type": "mp3"})
        except Exception as exc:
            logger.error("Skill overview TTS failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail="Could not synthesize speech for the overview.")

        from pathlib import Path as Pth

        mp3_path = Pth(wav_path).with_suffix(".mp3")
        await asyncio.wait_for(
            asyncio.to_thread(wav_to_mp3, wav_path, str(mp3_path)),
            timeout=15.0,
        )
        if not mp3_path.exists():
            raise HTTPException(status_code=500, detail="MP3 conversion failed.")

        mp3_bytes = mp3_path.read_bytes()
        b64 = base64.standard_b64encode(mp3_bytes).decode("ascii")
        return {"overview": overview, "audio_mp3_base64": b64}

    @app.post("/api/skills/{skill_id}/push")
    async def push_skill_to_project(skill_id: str, request: Request):
        """Push a skill to a project's CLI command directory."""
        body = await request.json()
        project_path = body.get("project_path", ".")
        instructions = body.get("instructions", "")
        # Web UI and API: install to Pi CLI only (.pi/skills).
        target = "pi"
        target_dir_name = ".pi/skills"

        from distr.core.skills.catalog import registry_entry_for
        from distr.core.workflow.skill_provision import push_skill_to_project as provision_skill_to_project

        registry_row = registry_entry_for(skill_id)
        if not registry_row:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
        skill_name = str(registry_row.get("name") or skill_id)

        # Resolve project path
        from pathlib import Path as P
        project = P(project_path).resolve()
        if not project.exists():
            project.mkdir(parents=True, exist_ok=True)

        dest = provision_skill_to_project(skill_id=skill_id, project_folder=str(project), backend_id=target)
        if not dest:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' could not be provisioned")
        dest_file = P(dest)
        dest_skill_dir = dest_file.parent
        actual_skill_id = dest_skill_dir.name
        pushed_files = [str(dest_file)]

        for subdir_name in ["scripts", "references", "reference"]:
            dest_subdir = dest_skill_dir / subdir_name
            if dest_subdir.exists() and dest_subdir.is_dir():
                pushed_files.append(str(dest_subdir))

        from distr.core.pi_skill_push_files import USER_INTENT_FILENAME, write_pi_skill_user_intent

        intent_path = write_pi_skill_user_intent(dest_skill_dir, actual_skill_id, instructions)
        if intent_path:
            pushed_files.append(str(intent_path))

        msg = (
            f"Pushed '{skill_name}' into {target_dir_name}/{actual_skill_id}/ on disk — "
            f"Pi will load SKILL.md when you open that project (CLI does not need to be running). "
            f"Run: /skill:{actual_skill_id}"
        )
        if intent_path:
            msg += f" Your ‘Use this skill to’ notes are in {USER_INTENT_FILENAME} beside SKILL.md."

        return {
            "success": True,
            "skill_id": actual_skill_id,
            "skill_name": skill_name,
            "target": target,
            "source": registry_row.get("source") or "local",
            "destination": str(dest_file),
            "user_intent_file": str(intent_path) if intent_path else None,
            "files": pushed_files,
            "message": msg,
        }

    # ── Skill Creator / Editor ────────────────────────────────────
    @app.post("/api/skills/create")
    async def create_skill(request: Request):
        """Create a new skill from the web UI."""
        import json as _json
        import re
        body = await request.json()
        name = (body.get("name") or "").strip()
        description = (body.get("description") or "").strip()
        content = (body.get("content") or "").strip()

        if not name:
            raise HTTPException(status_code=400, detail="Skill name is required")
        if not content:
            raise HTTPException(status_code=400, detail="Skill content is required")

        # Generate ID from name: lowercase, spaces→dashes, remove special chars
        skill_id = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))
        if not skill_id:
            raise HTTPException(status_code=400, detail="Could not derive skill ID from name")

        skills_root = project_root / "skills"
        skill_dir = skills_root / skill_id
        if skill_dir.exists():
            raise HTTPException(status_code=409, detail=f"Skill '{skill_id}' already exists")

        # Build frontmatter
        frontmatter = f"---\nname: {name}\ndescription: \"{description}\"\n---\n\n{content}"

        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(frontmatter, encoding="utf-8")

        # Update registry
        registry_file = skills_root / "skills_registry.json"
        if registry_file.exists():
            registry = _json.loads(registry_file.read_text())
        else:
            registry = []

        registry.append({
            "id": skill_id,
            "name": name,
            "description": description,
            "path": skill_id,
            "source": "local",
            "editable": True,
        })
        registry_file.write_text(_json.dumps(registry, indent=2), encoding="utf-8")
        try:
            from distr.core.skills.catalog import load_registry

            load_registry.cache_clear()
        except Exception:
            pass

        # Auto-push to pi skills
        pi_skills_dir = project_root / ".pi" / "skills" / skill_id
        pi_skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_dir / "SKILL.md", pi_skills_dir / "SKILL.md")

        return {
            "success": True,
            "skill_id": skill_id,
            "skill_name": name,
            "message": f"Created skill '{name}' ({skill_id}). Available as /skill:{skill_id}"
        }

    @app.put("/api/skills/{skill_id}/save")
    async def save_skill(skill_id: str, request: Request):
        """Save/update an existing skill from the web UI."""
        import json as _json
        body = await request.json()
        name = (body.get("name") or "").strip()
        description = (body.get("description") or "").strip()
        content = (body.get("content") or "").strip()

        skills_root = project_root / "skills"
        skill_dir = skills_root / skill_id
        if not skill_dir.exists() or not skill_dir.is_dir():
            from distr.core.skills.catalog import registry_entry_for

            row = registry_entry_for(skill_id)
            if row and str(row.get("source") or "").lower() == "ecc_vendor":
                raise HTTPException(status_code=409, detail="Vendored ECC skills are read-only. Create a local copy before editing.")
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

        if name or description or content:
            # Rebuild frontmatter (preserve existing if fields are empty)
            existing = ""
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                existing = skill_file.read_text(encoding="utf-8")

            if name:
                # Update name in registry
                frontmatter = f"---\nname: {name}\ndescription: \"{description}\"\n---\n\n{content}"
                skill_file.write_text(frontmatter, encoding="utf-8")

                # Update registry entry
                registry_file = skills_root / "skills_registry.json"
                if registry_file.exists():
                    registry = _json.loads(registry_file.read_text())
                    for entry in registry:
                        if entry.get("id") == skill_id:
                            entry["name"] = name
                            entry["description"] = description or entry.get("description", "")
                            break
                    registry_file.write_text(_json.dumps(registry, indent=2), encoding="utf-8")
                    try:
                        from distr.core.skills.catalog import load_registry

                        load_registry.cache_clear()
                    except Exception:
                        pass

        # Auto-push to pi skills
        pi_skills_dir = project_root / ".pi" / "skills" / skill_id
        pi_skills_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists():
            shutil.copy2(skill_file, pi_skills_dir / "SKILL.md")

        return {
            "success": True,
            "skill_id": skill_id,
            "message": f"Saved skill '{skill_id}'"
        }

    @app.delete("/api/skills/{skill_id}")
    async def delete_skill(skill_id: str):
        """Delete a skill (user-created skills only)."""
        import shutil as _shutil
        skills_root = project_root / "skills"
        skill_dir = skills_root / skill_id
        if not skill_dir.exists():
            from distr.core.skills.catalog import registry_entry_for

            row = registry_entry_for(skill_id)
            if row and str(row.get("source") or "").lower() == "ecc_vendor":
                raise HTTPException(status_code=409, detail="Vendored ECC skills are read-only and cannot be deleted here.")
            raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

        _shutil.rmtree(skill_dir)

        # Remove from registry
        registry_file = skills_root / "skills_registry.json"
        if registry_file.exists():
            registry = __import__("json").loads(registry_file.read_text())
            registry = [e for e in registry if e.get("id") != skill_id]
            registry_file.write_text(__import__("json").dumps(registry, indent=2), encoding="utf-8")
        try:
            from distr.core.skills.catalog import load_registry

            load_registry.cache_clear()
        except Exception:
            pass

        return {"success": True, "message": f"Deleted skill '{skill_id}'"}

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
    @app.get("/tickets/", response_class=HTMLResponse)
    async def tickets_page(request: Request):
        return page_templates.TemplateResponse(request, "kanban/kanban.html", _template_context(request, "/tickets"))

    @app.get("/tickets", response_class=HTMLResponse)
    async def tickets_redirect():
        return RedirectResponse(url="/tickets/", status_code=302)

    @app.get("/kanban/", response_class=HTMLResponse)
    async def kanban_legacy_page_redirect():
        return RedirectResponse(url="/tickets/", status_code=302)

    @app.get("/kanban", response_class=HTMLResponse)
    async def kanban_legacy_redirect():
        return RedirectResponse(url="/tickets/", status_code=302)

    try:
        from distr.gui.web.routes.kanban import create_routes as create_kanban_routes
        kanban_router = create_kanban_routes()
        app.include_router(kanban_router, prefix="/api", tags=["kanban"])
        logger.info("Ticket Board API routes mounted at /api")
    except Exception as e:
        logger.error("Failed to load Ticket Board routes: %s", e, exc_info=True)

    # Automations
    @app.get("/automations/", response_class=HTMLResponse)
    async def automations_page(request: Request):
        return page_templates.TemplateResponse(request, "automations/automations.html", _template_context(request, "/automations"))

    @app.get("/automations", response_class=HTMLResponse)
    async def automations_redirect():
        return RedirectResponse(url="/automations/", status_code=302)

    try:
        from distr.gui.web.routes.automations import create_routes as create_automation_routes
        automation_router = create_automation_routes()
        app.include_router(automation_router, prefix="/api", tags=["automations"])
        logger.info("Automation API routes mounted at /api")
    except Exception as e:
        logger.error("Failed to load Automation routes: %s", e, exc_info=True)

    try:
        from distr.gui.web.routes.schedule_blocks import create_routes as create_schedule_block_routes
        schedule_block_router = create_schedule_block_routes()
        app.include_router(schedule_block_router, prefix="/api", tags=["schedule_blocks"])
        logger.info("Schedule block API routes mounted at /api")
    except Exception as e:
        logger.error("Failed to load Schedule block routes: %s", e, exc_info=True)

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
    
    # Cancel any workflow runs left open from a previous session (crash/restart).
    # Must run before any workflow operations so orphaned runs don't block new pushes.
    @app.on_event("startup")
    async def _cancel_orphaned_workflow_runs():
        try:
            from distr.core.workflow.dispatcher import _cleanup_orphaned_runs_on_startup
            _cleanup_orphaned_runs_on_startup()
        except Exception as e:
            logger.warning("Orphaned workflow run cleanup could not run: %s", e)

    @app.on_event("startup")
    async def _compact_orchestrator_machine_activity():
        try:
            from distr.core.orchestrator_memory import run_weekly_machine_activity_compaction

            run_weekly_machine_activity_compaction()
        except Exception as e:
            logger.debug("Orchestrator machine activity compaction skipped: %s", e)

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
            os.environ["DECISIONS_WEB_PORT"] = str(self.port)
            os.environ["DECISIONS_API_BASE"] = f"http://{self.host}:{self.port}"
            
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
