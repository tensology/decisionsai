"""
Projects routes — /projects/*, /browse-folder
"""
from fastapi import HTTPException, File, UploadFile, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import Optional
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import re
import urllib.error
import urllib.request

from ._shared import logger, ProjectUpdate, ContextItemCreate, ContextItemUpdate, PROJECT_UPLOADS_DIR


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _backend_id_for_project(project) -> str:
    from distr.core.project_cli_backends import get_project_backend_id

    return get_project_backend_id(project)


def _codex_plugin_candidates() -> list[Path]:
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    agents_home = Path(os.environ.get("AGENTS_HOME") or (Path.home() / ".agents"))
    return [
        _repo_root() / "codex_plugin" / "decisions-codex",
        Path.home() / "plugins" / "decisions-codex",
        codex_home / "plugins" / "decisions-codex",
        agents_home / "plugins" / "decisions-codex",
    ]


def _codex_plugin_state() -> dict:
    candidates = _codex_plugin_candidates()
    found = next((p for p in candidates if (p / ".codex-plugin" / "plugin.json").exists()), None)
    return {
        "available": bool(found),
        "path": str(found or candidates[0]),
        "candidates": [str(p) for p in candidates],
        "manifest_exists": bool(found),
    }


def _install_local_codex_plugin() -> dict:
    """Install/register the repo-local DecisionsAI Codex plugin for local Codex."""
    from distr.core.project_cli_backends import get_backend

    status = get_backend("codex").setup_status().to_dict()
    if not status.get("ready"):
        return {
            "installed": False,
            "skipped": True,
            "reason": status.get("message") or "Codex CLI is not available on PATH.",
            "backend": status,
            "plugin": _codex_plugin_state(),
        }

    source = _repo_root() / "codex_plugin" / "decisions-codex"
    manifest = source / ".codex-plugin" / "plugin.json"
    if not manifest.exists():
        return {
            "installed": False,
            "skipped": True,
            "reason": "DecisionsAI Codex plugin source was not found in this checkout.",
            "backend": status,
            "plugin": _codex_plugin_state(),
        }

    target = Path.home() / "plugins" / "decisions-codex"
    marketplace = Path.home() / ".agents" / "plugins" / "marketplace.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"),
    )

    if marketplace.exists():
        try:
            payload = json.loads(marketplace.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("name", "local-codex-plugins")
    payload.setdefault("interface", {"displayName": "Local Codex Plugins"})
    plugins = payload.get("plugins")
    if not isinstance(plugins, list):
        plugins = []
    plugins = [
        item for item in plugins
        if not (isinstance(item, dict) and item.get("name") == "decisions-codex")
    ]
    plugins.append({
        "name": "decisions-codex",
        "source": {"type": "local", "path": "./plugins/decisions-codex"},
        "policy": {"installation": "AVAILABLE", "authentication": "NONE"},
        "category": "Developer Tools",
    })
    payload["plugins"] = plugins
    marketplace.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return {
        "installed": True,
        "skipped": False,
        "path": str(target),
        "marketplace": str(marketplace),
        "backend": status,
        "plugin": _codex_plugin_state(),
    }


def _codex_project_sync_payload(project) -> dict:
    from distr.core.project_cli_backends import get_backend

    folder = (project.folder_location or "").strip()
    folder_exists = bool(folder and Path(folder).expanduser().exists())
    plugin = _codex_plugin_state()
    try:
        backend_status = get_backend("codex").setup_status().to_dict()
    except Exception as exc:
        backend_status = {
            "id": "codex",
            "name": "Codex CLI",
            "ready": False,
            "state": "unavailable",
            "message": str(exc),
            "setup_required": True,
            "setup_instructions": "Install and authenticate Codex CLI, then make sure the codex command is on PATH.",
        }

    backend_ready = bool(backend_status.get("ready"))
    correlated = folder_exists and (backend_ready or plugin.get("available"))
    return {
        "project_id": project.id,
        "project_name": project.name or "",
        "project_folder": folder,
        "folder_exists": folder_exists,
        "current_backend": _backend_id_for_project(project),
        "recommended_backend": "codex",
        "correlated": correlated,
        "sync_ready": folder_exists and backend_ready,
        "plugin": plugin,
        "backend": backend_status,
        "message": (
            "Codex CLI is ready for this project."
            if folder_exists and backend_ready
            else "Project folder is missing."
            if not folder_exists
            else backend_status.get("message") or "Codex CLI needs setup."
        ),
    }



def _resolve_terminal_overview_llm(settings: dict) -> tuple:
    """Provider/model for CLI Read Overview: match Projects CLI (coding_llm_*), then conversational LLM.

    Terminal overview previously read agent_model_name/default_model_name, which are often empty or
    OpenAI-style names while the app uses conversational_llm_* / coding_llm_* — causing Ollama to
    receive e.g. gpt-4o-mini and return 404.
    """
    from distr.core.llm_factory import normalize_provider, resolve_settings_keys

    cm = (settings.get("coding_llm_model") or "").strip()
    cp = (settings.get("coding_llm_provider") or "").strip()
    if cm:
        p = normalize_provider(cp or "Ollama")
        logger.debug("Terminal overview LLM: using coding_llm (%s / %s)", p, cm)
        return p, cm

    prov, model = resolve_settings_keys(settings)
    prov = normalize_provider(prov)
    model = (model or "").strip()
    if model:
        logger.debug("Terminal overview LLM: using resolve_settings_keys (%s / %s)", prov, model)
        return prov, model

    am = (settings.get("agent_model") or settings.get("agent_model_name") or settings.get("default_model_name") or "").strip()
    ap = (settings.get("agent_provider") or settings.get("default_provider") or "").strip()
    if am:
        p = normalize_provider(ap or "Ollama")
        logger.debug("Terminal overview LLM: using legacy agent model keys (%s / %s)", p, am)
        return p, am

    p = normalize_provider("Ollama")
    logger.debug("Terminal overview LLM: using fallback llama3.2 for Ollama")
    return p, "llama3.2"


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
                        "coding_backend": _backend_id_for_project(p),
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

    def _pi_cli_models():
        """Return available models for the CLI dropdown.
        Reads custom models from ~/.pi/agent/models.json and built-in providers
        configured in settings."""
        import json as _json
        
        models = []
        
        # Load custom models from pi's models.json
        try:
            models_path = os.path.expanduser("~/.pi/agent/models.json")
            if os.path.exists(models_path):
                with open(models_path) as f:
                    cfg = _json.load(f)
                for prov_name, prov in (cfg.get("providers") or {}).items():
                    for m in (prov.get("models") or []):
                        mid = m.get("id", "")
                        mname = m.get("name") or mid
                        if mid:
                            models.append({"id": mid, "name": mname, "provider": prov_name})
        except Exception as e:
            logger.debug(f"Failed to load models.json: {e}")
        
        # Also add well-known OpenAI models (not from models.json)
        builtin_openai = [
            "gpt-5.4-pro", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
            "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2-codex", "gpt-5.2-pro",
            "gpt-5.1-codex-max", "gpt-5.1-codex", "gpt-5-pro", "gpt-5",
            "o3-pro", "o3", "o3-deep-research", "o4-mini", "o4-mini-deep-research",
            "o1", "o1-pro", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
            "gpt-4o", "gpt-4o-mini",
        ]
        for mid in builtin_openai:
            if not any(m["id"] == mid for m in models):
                models.append({"id": mid, "name": mid, "provider": "openai"})

        # Deduplicate by model id
        seen = set()
        unique_models = []
        for m in models:
            if m["id"] not in seen:
                seen.add(m["id"])
                unique_models.append(m)
        models = unique_models

        return models

    def _model_entry(model_id: str, provider: str, name: str | None = None) -> dict:
        model_id = (model_id or "").strip()
        return {"id": model_id, "name": (name or model_id).strip() or model_id, "provider": provider}

    def _dedupe_model_entries(models: list[dict]) -> list[dict]:
        seen = set()
        out = []
        for item in models:
            mid = (item.get("id") or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            out.append(item)
        return out

    def _cursor_api_models(settings: dict) -> tuple[list[dict], str, str]:
        enabled = bool(settings.get("cursor_enabled"))
        api_key = (settings.get("cursor_key") or "").strip()
        if not enabled:
            return [], "disabled", "Enable Cursor in Third Party API Keys to load Cursor CLI models."
        if not api_key:
            return [], "no_key", "Add a Cursor API key in Third Party API Keys to load Cursor CLI models."
        req = urllib.request.Request(
            "https://api.cursor.com/v0/models",
            headers={
                "Authorization": f"Bearer {api_key}",
                "accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return [], "error", f"Cursor models API returned HTTP {exc.code}."
        except Exception as exc:
            return [], "error", f"Could not fetch Cursor models API: {exc}"
        return [_model_entry(mid, "cursor") for mid in payload.get("models") or []], "cursor-api", ""

    def _anthropic_models(settings: dict) -> tuple[list[dict], str, str]:
        api_key = (os.environ.get("ANTHROPIC_API_KEY") or settings.get("anthropic_key") or "").strip()
        aliases = [
            _model_entry("default", "claude_code", "Default"),
            _model_entry("sonnet", "claude_code", "Sonnet"),
            _model_entry("opus", "claude_code", "Opus"),
            _model_entry("haiku", "claude_code", "Haiku"),
            _model_entry("sonnet[1m]", "claude_code", "Sonnet 1M"),
            _model_entry("opusplan", "claude_code", "Opus plan"),
        ]
        if not api_key:
            return aliases, "claude-code-aliases", "No Anthropic API key configured; showing Claude Code aliases only."
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return aliases, "claude-code-aliases", f"Anthropic models API returned HTTP {exc.code}; showing aliases."
        except Exception as exc:
            return aliases, "claude-code-aliases", f"Could not fetch Anthropic models: {exc}; showing aliases."
        models = [
            _model_entry(item.get("id") or "", "anthropic", item.get("display_name"))
            for item in payload.get("data") or []
            if item.get("id")
        ]
        return _dedupe_model_entries(aliases + models), "anthropic-api", ""

    def _codex_models(settings: dict) -> tuple[list[dict], str, str]:
        try:
            import subprocess

            result = subprocess.run(
                ["codex", "models"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if result.returncode == 0:
                text = (result.stdout or "").strip()
                models = []
                for line in text.splitlines():
                    mid = line.strip().split()[0] if line.strip() else ""
                    if mid and not mid.lower().startswith(("-", "usage", "error")):
                        models.append(_model_entry(mid, "codex"))
                if models:
                    return _dedupe_model_entries(models), "codex-cli", ""
        except Exception:
            pass

        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or settings.get("openai_key")
            or settings.get("openai_api_key")
            or ""
        ).strip()
        fallback = [
            _model_entry("auto", "codex", "Auto"),
            _model_entry("gpt-5.3-codex", "openai"),
            _model_entry("gpt-5.3-codex-spark", "openai"),
            _model_entry("gpt-5.2-codex", "openai"),
        ]
        if not api_key:
            return fallback, "codex-defaults", "Codex CLI did not report models; showing safe defaults."
        return fallback, "codex-defaults", "Codex CLI did not report models; showing OpenAI Codex defaults."

    def _models_for_cli_backend(backend_id: str, settings: dict | None = None):
        from distr.core.project_cli_backends import normalize_backend_id

        backend_id = normalize_backend_id(backend_id)
        settings = settings or {}
        if backend_id == "cursor":
            models, source, message = _cursor_api_models(settings)
            return {"models": models, "source": source, "message": message}
        if backend_id == "claude_code":
            models, source, message = _anthropic_models(settings)
            return {"models": models, "source": source, "message": message}
        if backend_id == "codex":
            models, source, message = _codex_models(settings)
            return {"models": models, "source": source, "message": message}
        return {"models": _pi_cli_models(), "source": "pi-models", "message": ""}

    def _project_backend_model(project_id: int | None, backend_id: str, fallback_model: str = ""):
        if project_id:
            try:
                from distr.core.db import get_session
                from distr.core.db.projects import Project

                with get_session() as session:
                    project = session.query(Project).filter(Project.id == project_id).first()
                    if project and (project.coding_backend_model or "").strip():
                        return (project.coding_backend_model or "").strip()
            except Exception:
                pass
        return fallback_model

    @router.get("/projects/cli-models")
    async def get_cli_models(request: Request):
        """Return available models for the selected project CLI backend."""
        from distr.core.project_cli_backends import normalize_backend_id

        backend_id = normalize_backend_id(request.query_params.get("backend_id"))
        try:
            project_id = int(request.query_params.get("project_id") or "0") or None
        except Exception:
            project_id = None
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        model_result = _models_for_cli_backend(backend_id, settings)
        models = model_result.get("models") or []

        # Get current Pi model from settings as the legacy/global fallback.
        try:
            from distr.core.db import get_session as db_session
            with db_session() as session:
                from sqlalchemy import text
                row = session.execute(text("SELECT coding_llm_provider, coding_llm_model FROM settings LIMIT 1")).first()
                current_provider = (row[0] or "ollama") if row else "ollama"
                current_model = (row[1] or "") if row else ""
        except Exception:
            current_provider = "ollama"
            current_model = ""
        if backend_id != "pi":
            current_provider = backend_id
        current_model = _project_backend_model(project_id, backend_id, current_model if backend_id == "pi" else "")
        if not current_model:
            current_model = "auto" if backend_id in ("cursor", "codex") else ("default" if backend_id == "claude_code" else "")
            current_provider = backend_id

        return JSONResponse({
            "backend_id": backend_id,
            "models": models,
            "source": model_result.get("source") or "",
            "message": model_result.get("message") or "",
            "current_provider": current_provider,
            "current_model": current_model,
        })

    @router.post("/projects/cli-model")
    async def set_cli_model(request: Request):
        """Set the CLI model and restart active pi RPC sessions with the new model."""
        body = await request.json()
        from distr.core.project_cli_backends import normalize_backend_id

        model = (body.get("model") or "").strip()
        provider = (body.get("provider") or "").strip()
        backend_id = normalize_backend_id(body.get("backend_id"))
        project_id = body.get("project_id")
        if not model:
            return JSONResponse({"success": False, "error": "model required"}, status_code=400)

        try:
            from distr.core.db import get_session as db_session
            with db_session() as session:
                from sqlalchemy import text
                if project_id:
                    from distr.core.db.projects import Project
                    project = session.query(Project).filter(Project.id == int(project_id)).first()
                    if project:
                        project.coding_backend_model = model
                if backend_id == "pi":
                    session.execute(text("UPDATE settings SET coding_llm_model = :m, coding_llm_provider = :p"), {"m": model, "p": provider or "ollama"})
                session.commit()
            logger.info(f"CLI model set to: {backend_id}/{provider}/{model}")
            
            # Mark active RPC sessions as stale so they restart on next prompt
            # (don't kill all sessions — they'll lazily restart with new model)
            if backend_id == "pi":
                from distr.core.pi_rpc import _rpc_sessions
                for pid, rpc in list(_rpc_sessions.items()):
                    if project_id and int(pid) != int(project_id):
                        continue
                    rpc._provider = provider or "ollama"
                    rpc._model = model
                    # If there's a running pi, kill it so next prompt spawns with new model
                    if rpc.is_alive:
                        rpc._running = False
                        try:
                            rpc._process.terminate()
                        except Exception:
                            pass
            
            return JSONResponse({"success": True, "model": model, "provider": provider, "backend_id": backend_id})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.get("/projects/cli-backends")
    async def get_cli_backends():
        """Return all supported project coding CLI backends and setup state."""
        from distr.core.project_cli_backends import get_backend_statuses

        return JSONResponse(get_backend_statuses())

    @router.get("/projects/{project_id}/cli-backends")
    async def get_project_cli_backends(project_id: int):
        """Return supported project coding CLI backends for a specific project."""
        from distr.core.db import get_session
        from distr.core.db.projects import Project
        from distr.core.project_cli_backends import get_backend_statuses

        with get_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            return JSONResponse(get_backend_statuses(_backend_id_for_project(project)))

    @router.post("/projects/{project_id}/cli-backends/{backend_id}/setup")
    async def setup_project_cli_backend(project_id: int, backend_id: str):
        """Run or describe setup for a project coding CLI backend."""
        from distr.core.db import get_session
        from distr.core.db.projects import Project
        from distr.core.project_cli_backends import get_backend, normalize_backend_id

        with get_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")

        normalized_backend_id = normalize_backend_id(backend_id)
        if normalized_backend_id == "codex":
            install = _install_local_codex_plugin()
            payload = install.get("backend") or get_backend("codex").setup_status().to_dict()
            payload.update({
                "plugin_install": install,
                "message": (
                    "Codex CLI is available and the DecisionsAI Codex plugin was installed."
                    if install.get("installed")
                    else install.get("reason") or payload.get("message") or "Codex setup checked."
                ),
            })
            return JSONResponse(payload)

        backend = get_backend(normalized_backend_id)
        status = await backend.install_or_setup()
        return JSONResponse(status.to_dict())

    @router.put("/projects/{project_id}/coding-backend")
    async def set_project_coding_backend(project_id: int, request: Request):
        """Persist the active coding CLI backend for a project."""
        body = await request.json()
        from distr.core.db import get_session
        from distr.core.db.projects import Project
        from distr.core.project_cli_backends import get_backend_statuses, normalize_backend_id

        backend_id = normalize_backend_id(body.get("coding_backend") or body.get("backend_id"))
        plugin_install = _install_local_codex_plugin()

        with get_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            project.coding_backend = backend_id
            project.coding_backend_model = ""
            session.commit()
        return JSONResponse({"success": True, **get_backend_statuses(backend_id)})

    @router.get("/projects/{project_id}/codex-sync")
    async def get_project_codex_sync(project_id: int):
        """Report whether this Decisions project can be correlated with Codex."""
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            return JSONResponse(_codex_project_sync_payload(project))

    @router.post("/projects/{project_id}/codex-sync")
    async def sync_project_to_codex(project_id: int):
        """Bind this project to the Codex CLI backend when the user requests it."""
        from distr.core.db import get_session
        from distr.core.db.projects import Project
        from distr.core.project_cli_backends import get_backend_statuses

        with get_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            project.coding_backend = "codex"
            if not (project.coding_backend_model or "").strip():
                project.coding_backend_model = "auto"
            session.commit()
            payload = _codex_project_sync_payload(project)
        return JSONResponse({
            "success": True,
            "plugin_install": plugin_install,
            **payload,
            **get_backend_statuses("codex"),
        })

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
                    "coding_backend": _backend_id_for_project(project),
                    "coding_backend_model": project.coding_backend_model or "",
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
                if payload.coding_backend is not None:
                    from distr.core.project_cli_backends import normalize_backend_id

                    project.coding_backend = normalize_backend_id(payload.coding_backend)
                if payload.coding_backend_model is not None:
                    project.coding_backend_model = (payload.coding_backend_model or "").strip()
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
            from distr.core.project_cli_backends import normalize_backend_id
            coding_backend = normalize_backend_id(body.get("coding_backend"))
            with get_session() as session:
                project = Project(
                    name=name,
                    description="",
                    folder_location=folder,
                    additional_trigger_words="[]",
                    coding_backend=coding_backend,
                    coding_backend_model=(body.get("coding_backend_model") or "").strip(),
                )
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
                # First check the explicit kanban_board_id link (new method)
                if project.kanban_board_id:
                    board = session.query(KanbanBoard).filter(KanbanBoard.id == project.kanban_board_id).first()
                    if board:
                        return JSONResponse({"board": {"id": board.id, "name": board.name}})
                # Fallback: check for a board with default_project_id pointing to this project
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
                    # Set the explicit kanban_board_id link
                    project.kanban_board_id = existing.id
                    session.commit()
                    return JSONResponse({"board": {"id": existing.id, "name": existing.name}, "created": False})
                board = KanbanBoard(name=project.name, description=f"Board for project: {project.name}", source="database", default_project_id=project_id)
                session.add(board)
                session.flush()
                # Set the explicit kanban_board_id link
                project.kanban_board_id = board.id
                session.commit()
                
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
        """Send an instruction to the selected project coding CLI backend."""
        try:
            body = await request.json()
            instruction = (body.get("instruction") or "").strip()
            if not instruction:
                return JSONResponse({"success": False, "error": "instruction required"}, status_code=400)

            from distr.core.db import get_session
            from distr.core.db.projects import Project
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                folder = project.folder_location or ""
                project_name = project.name or ""
                backend_id = _backend_id_for_project(project)

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
                    ChatService.add_message(int(chat_id), "user", f"[{backend_id}: {project_name}] {instruction}")
            except Exception as e:
                logger.debug(f"Could not log CLI instruction to chat: {e}")

            # Create audit workflow to track this execution
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
            with get_session() as session:
                audit = AutoWorkflow(
                    name=f"[Project: {project_name}] {instruction}",
                    status="in_progress",
                    chat_id=int(chat_id) if chat_id else None,
                    workflow_type="project_cli",
                )
                session.add(audit)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=audit.id,
                    position=0,
                    name=f"{backend_id} backend",
                    instruction=instruction,
                    status="running",
                    tool_used=backend_id,
                )
                session.add(step)
                session.commit()
                audit_id = audit.id
                step_id = step.id

            from types import SimpleNamespace
            from distr.core.project_cli_backends import run_project_task

            project_ref = SimpleNamespace(
                id=project_id,
                name=project_name,
                folder_location=folder,
                coding_backend=backend_id,
            )
            result = await run_project_task(
                project_ref,
                instruction,
                chat_id=int(chat_id) if chat_id else None,
                audit_id=audit_id,
                origin="cli",
            )

            status = "completed" if result.success else "failed"
            with get_session() as session:
                step = session.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
                audit = session.query(AutoWorkflow).filter(AutoWorkflow.id == audit_id).first()
                if step:
                    step.status = status
                    step.result = (result.output or result.error or "")[:3000]
                    step.tool_used = result.backend_id
                if audit:
                    audit.status = status
                session.commit()

            if chat_id:
                try:
                    from distr.core.chat import ChatService
                    if result.success:
                        msg = result.output[:1500] if result.output else "Instruction sent. Check the CLI tab for progress."
                    else:
                        msg = f"Backend failed: {result.error or 'Unknown error'}"
                    ChatService.add_message(int(chat_id), "assistant", f"[{result.backend_id}: {project_name}] {msg}")
                except Exception:
                    pass

            payload = result.to_dict()
            payload["success"] = result.success
            if not result.success:
                return JSONResponse(payload, status_code=400)
            return JSONResponse(payload)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"CLI instruction failed: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.get("/projects/{project_id}/cli/audit")
    async def get_cli_audit(project_id: int):
        """Get audit trail of project CLI backend actions for a project."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                project_name = project.name or ""

                # Find project CLI workflows for this project. Include legacy
                # pi_agent rows so existing audit history remains visible.
                prefix = f"[Project: {project_name}]"
                workflows = (
                    session.query(AutoWorkflow)
                    .filter(
                        AutoWorkflow.name.like(f"{prefix}%"),
                        AutoWorkflow.workflow_type.in_(["project_cli", "pi_agent"]),
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
                                AutoWorkflow.workflow_type.in_(["project_cli", "pi_agent"]),
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
                project_name = project.name or ""
                backend_id = _backend_id_for_project(project)
                backend_model = (project.coding_backend_model or "").strip()
        except Exception as e:
            logger.error(f"Terminal: failed to load project: {e}")
            await websocket.send_json({"type": "error", "message": "Failed to load project"})
            await websocket.close(code=1011, reason="Internal error")
            return

        # Ensure the directory exists
        if not os.path.isdir(cwd):
            cwd = os.path.expanduser("~")

        from distr.core.project_cli_backends import get_backend, run_project_task
        backend = get_backend(backend_id)
        loop = asyncio.get_running_loop()

        if not backend.supports_rpc:
            status = backend.setup_status()
            await websocket.send_json({
                "type": "connected",
                "project_id": project_id,
                "backend": backend.id,
                "buffer": [],
            })
            if not status.ready:
                await websocket.send_json({
                    "type": "error",
                    "message": f"{backend.name} is not ready: {status.message} {status.setup_instructions}".strip(),
                })

            running_task = None

            async def _send_event(event_dict):
                try:
                    await websocket.send_json(event_dict)
                except Exception:
                    pass

            try:
                while True:
                    data = await websocket.receive_text()
                    try:
                        msg = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    msg_type = msg.get("type")
                    if msg_type == "prompt":
                        instruction = (msg.get("message") or "").strip()
                        if not instruction:
                            continue
                        if running_task and not running_task.done():
                            await websocket.send_json({"type": "error", "message": f"{backend.name} is still running. Wait for it to finish before sending another task."})
                            continue

                        async def _run_one():
                            queue: asyncio.Queue = asyncio.Queue()

                            def _queue_event(event_dict):
                                try:
                                    loop.call_soon_threadsafe(queue.put_nowait, event_dict)
                                except Exception:
                                    pass

                            async def _drain():
                                while True:
                                    event = await queue.get()
                                    if event is None:
                                        break
                                    await _send_event(event)

                            drain_task = asyncio.create_task(_drain())
                            try:
                                from types import SimpleNamespace
                                p = SimpleNamespace(
                                    id=project_id,
                                    name=project_name,
                                    folder_location=cwd,
                                    coding_backend=backend.id,
                                    coding_backend_model=backend_model,
                                )
                                result = await run_project_task(p, instruction, on_event=_queue_event, origin="cli")
                                if not result.success:
                                    await _send_event({"type": "error", "message": result.error or f"{backend.name} failed"})
                            finally:
                                await queue.put(None)
                                await drain_task

                        running_task = asyncio.create_task(_run_one())
                    elif msg_type == "abort":
                        if running_task and not running_task.done():
                            running_task.cancel()
                            await websocket.send_json({"type": "error", "message": f"{backend.name} task cancelled."})
                    elif msg_type == "restart":
                        await websocket.send_json({"type": "connected", "project_id": project_id, "backend": backend.id, "buffer": []})
                    elif msg_type == "ping":
                        await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.debug(f"Generic CLI terminal WebSocket error: {e}")
            finally:
                if running_task and not running_task.done():
                    running_task.cancel()
            return

        # Create or get the pi RPC session (lazy: don't auto-start pi until first prompt)
        try:
            rpc = await get_or_create_rpc_session(project_id, cwd, lazy_start=True)
        except Exception as e:
            logger.error(f"Terminal: failed to create pi RPC session: {e}")
            await websocket.send_json({"type": "error", "message": f"Failed to start pi: {e}"})
            await websocket.close(code=1011, reason="Terminal error")
            return

        # Queue for RPC events to be sent to this WebSocket
        event_queue = asyncio.Queue()

        # The RPC reader runs in a background thread, so we need a thread-safe
        # way to push events into the asyncio queue.
        loop = asyncio.get_event_loop()

        def _on_event(event_dict):
            try:
                loop.call_soon_threadsafe(event_queue.put_nowait, event_dict)
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
                    # User sent a prompt to pi via the CLI input
                    instruction = msg.get("message", "")
                    if instruction:
                        # send_prompt auto-starts pi if lazy-started (first prompt)
                        if not rpc.send_prompt(instruction, origin="cli"):
                            await websocket.send_json({"type": "error", "message": "Failed to send prompt — pi may not be available"})
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
                        rpc = await get_or_create_rpc_session(project_id, cwd, lazy_start=True)
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
        """Get terminal buffer content from the selected backend when available."""
        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project
        from distr.core.project_cli_backends import get_backend

        with db_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                return JSONResponse({"buffer": "", "alive": False, "project_id": project_id})
            backend = get_backend(_backend_id_for_project(project))
        buffer_text = backend.get_buffer(project_id, lines)
        if not buffer_text:
            return JSONResponse({"buffer": "", "alive": False, "project_id": project_id})

        return JSONResponse({
            "buffer": buffer_text,
            "alive": True,
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
                backend_id = _backend_id_for_project(project)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        if not os.path.isdir(cwd):
            cwd = os.path.expanduser("~")

        from distr.core.project_cli_backends import get_backend
        backend = get_backend(backend_id)
        if not backend.supports_rpc:
            result = await backend.restart(project_id, cwd)
            return JSONResponse(result.to_dict() | {"success": result.success})

        await kill_rpc_session(project_id)
        try:
            rpc = await get_or_create_rpc_session(project_id, cwd)
            return JSONResponse({"success": True, "alive": rpc.is_alive})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    # ── Startup instructions: one PTY per line (Projects → Startup tab) ─────

    @router.get("/projects/{project_id}/startup-sessions")
    async def list_startup_sessions(project_id: int):
        """Return all alive startup PTY sessions for a project.
        Used by the frontend to reconnect after a page reload.
        Automatically cleans up dead sessions from the registry."""
        from distr.core.terminal import get_startup_sessions_for_project, materialize_queued_startup_terminals
        try:
            started, failed = await materialize_queued_startup_terminals(project_id)
            if started or failed:
                logger.info("Materialized queued startup sessions for project %s: started=%s failed=%s", project_id, started, failed)
        except Exception as e:
            logger.warning("Failed to materialize queued startup sessions for project %s: %s", project_id, e)
        sessions = get_startup_sessions_for_project(project_id, purpose="startup")
        return JSONResponse({"sessions": sessions})

    @router.get("/projects/{project_id}/shell-terminal")
    async def get_project_shell_terminal(project_id: int):
        """Return alive interactive shell terminal sessions for this project."""
        from distr.core.terminal import get_startup_sessions_for_project
        sessions = get_startup_sessions_for_project(project_id, purpose="cli_shell")
        return JSONResponse({"sessions": sessions})

    @router.post("/projects/{project_id}/shell-terminal/start")
    async def start_project_shell_terminal(project_id: int):
        """Create one interactive shell PTY for the project's root folder."""
        import shutil
        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project
        from distr.core.terminal import create_startup_shell_session, get_startup_sessions_for_project

        with db_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                return JSONResponse({"success": False, "error": "Project not found"}, status_code=404)
            folder = (project.folder_location or "").strip()
            if not folder or not os.path.isdir(folder):
                return JSONResponse({"success": False, "error": "Project has no valid folder location"}, status_code=400)
            canonical = os.path.realpath(folder)

        existing = get_startup_sessions_for_project(project_id, purpose="cli_shell")
        if existing:
            return JSONResponse({"success": True, "process_id": existing[0]["process_id"], "pid": existing[0]["pid"], "reused": True})

        user_shell = os.environ.get("SHELL", "").strip()
        shell_name = os.path.basename(user_shell) if user_shell else ""
        if "zsh" in shell_name:
            shell_cmd = "[zsh] exec zsh -il"
        elif "bash" in shell_name:
            shell_cmd = "[bash] exec bash -il"
        else:
            fallback_shell = shutil.which("zsh") or shutil.which("bash") or "/bin/bash"
            if os.path.basename(fallback_shell) == "zsh":
                shell_cmd = "[zsh] exec zsh -il"
            else:
                shell_cmd = "[bash] exec bash -il"

        try:
            terminal_id, sess = await create_startup_shell_session(project_id, canonical, shell_cmd, purpose="cli_shell")
        except Exception as e:
            logger.error(f"shell-terminal spawn failed: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

        return JSONResponse({"success": True, "process_id": terminal_id, "pid": sess.pid, "reused": False})

    @router.post("/projects/startup-terminal")
    async def start_startup_terminal(request: Request):
        """Spawn a shell command in a PTY; client opens WebSocket for output."""
        body = await request.json()
        project_id = int(body.get("project_id") or 0)
        command = (body.get("command") or "").strip()
        working_dir = (body.get("working_dir") or "").strip()
        if not project_id or not command:
            return JSONResponse({"success": False, "error": "project_id and command required"}, status_code=400)

        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project
        from distr.core.terminal import create_startup_shell_session

        with db_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                return JSONResponse({"success": False, "error": "Project not found"}, status_code=404)
            folder = (project.folder_location or "").strip()
            if not folder or not os.path.isdir(folder):
                return JSONResponse({"success": False, "error": "Project has no valid folder location"}, status_code=400)
            canonical = os.path.realpath(folder)
            if working_dir:
                try:
                    req = os.path.realpath(os.path.expanduser(working_dir))
                except OSError:
                    return JSONResponse({"success": False, "error": "Invalid working_dir"}, status_code=400)
                if req != canonical:
                    return JSONResponse({"success": False, "error": "working_dir must match the project's folder"}, status_code=400)

        try:
            terminal_id, _sess = await create_startup_shell_session(project_id, canonical, command)
        except Exception as e:
            logger.error(f"startup-terminal spawn failed: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

        return JSONResponse({
            "success": True,
            "process_id": terminal_id,
            "pid": _sess.pid,
        })

    @router.websocket("/projects/startup-terminal/{terminal_id}/ws")
    async def startup_terminal_websocket(websocket: WebSocket, terminal_id: str):
        from distr.core.terminal import get_startup_session
        from distr.gui.web.security import websocket_has_valid_internal_token, is_allowed_local_origin

        origin = websocket.headers.get("origin")
        if origin and not is_allowed_local_origin(origin):
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        if not websocket_has_valid_internal_token(websocket):
            await websocket.close(code=1008, reason="Unauthorized")
            return

        sess = get_startup_session(terminal_id)
        if not sess:
            await websocket.close(code=1008, reason="Session not found")
            return

        await websocket.accept()

        # Replay buffered output so the reconnected terminal shows previous output
        if sess._raw_buffer:
            try:
                replay = sess._raw_buffer.decode("utf-8", errors="replace")
                await websocket.send_text(
                    json.dumps({"type": "output", "data": replay, "terminal_id": terminal_id})
                )
            except Exception as e:
                logger.debug(f"startup terminal replay failed: {e}")

        sess.add_websocket(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "resize":
                    rows = int(msg.get("rows") or 24)
                    cols = int(msg.get("cols") or 80)
                    sess.resize(max(2, min(rows, 200)), max(20, min(cols, 500)))
                elif msg.get("type") == "input":
                    inp = msg.get("data")
                    if isinstance(inp, str) and inp:
                        sess.write(inp)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"startup terminal ws: {e}")
        finally:
            sess.remove_websocket(websocket)

    @router.post("/projects/kill-terminal")
    async def kill_terminal_process(request: Request):
        body = await request.json()
        process_id = (body.get("process_id") or "").strip()
        if not process_id:
            return JSONResponse({"success": False, "error": "process_id required"}, status_code=400)
        from distr.core.terminal import kill_startup_session, get_startup_session
        sess = get_startup_session(process_id)
        pid = sess.pid if sess else None
        ok = await kill_startup_session(process_id)
        logger.info(f"kill-terminal: key={process_id} pid={pid} success={ok}")
        return JSONResponse({"success": ok, "pid": pid})

    @router.post("/projects/{project_id}/terminal/overview")
    async def terminal_overview(project_id: int):
        """Get selected backend transcript, produce a natural spoken summary, and speak it aloud."""
        import asyncio
        from distr.core.settings import load_settings_from_db
        from distr.core.llm_factory import create_stream
        from distr.core.signals import signal_manager
        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project
        from distr.core.project_cli_backends import get_backend

        with db_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                return JSONResponse({"error": "Project not found"}, status_code=404)
            backend = get_backend(_backend_id_for_project(project))

        # Get structured transcript
        messages = backend.get_messages(project_id)
        if not messages:
            return JSONResponse({"summary": "The terminal is empty — nothing has been output yet.", "empty": True})

        # Extract user commands, assistant responses, and tool activity
        user_msgs = []
        assistant_msgs = []
        tool_msgs = []
        for msg in messages:
            role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")
            if role == "user":
                content = ((msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")) or "").strip()
                if content:
                    user_msgs.append(content)
            elif role == "assistant":
                content = ((msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")) or "").strip()
                if content:
                    assistant_msgs.append(content)
            elif role == "tool_result":
                tool_name = (msg.get("tool_name", "") if isinstance(msg, dict) else getattr(msg, "tool_name", "")) or "tool"
                tool_result = ((msg.get("tool_result", "") if isinstance(msg, dict) else getattr(msg, "tool_result", "")) or "").strip()
                is_error = (msg.get("is_error", False) if isinstance(msg, dict) else getattr(msg, "is_error", False))
                tool_msgs.append(f"{'ERROR' if is_error else 'OK'} {tool_name}: {tool_result[:200]}")

        if not user_msgs and not assistant_msgs and not tool_msgs:
            return JSONResponse({"summary": "The terminal has no commands yet.", "empty": True})

        # Build a focused transcript for LLM summarization
        # Last commands, responses, and tool calls, truncated for the LLM
        transcript_parts = []
        for cmd in user_msgs[-5:]:
            truncated = cmd[:200] + "..." if len(cmd) > 200 else cmd
            transcript_parts.append(f"[cmd] {truncated}")
        for resp in assistant_msgs[-5:]:
            truncated = resp[:400] + "..." if len(resp) > 400 else resp
            transcript_parts.append(f"[resp] {truncated}")
        for tool_msg in tool_msgs[-5:]:
            transcript_parts.append(f"[tool] {tool_msg}")

        buffer = "\n".join(transcript_parts)
        if len(buffer) > 4000:
            buffer = buffer[-4000:]

        settings = load_settings_from_db()
        provider, model = _resolve_terminal_overview_llm(settings)
        logger.info("Terminal overview: LLM provider=%s model=%s", provider, model)

        # LLM prompt: produce natural spoken language for TTS
        system_prompt = (
            "You produce short TTS-friendly summaries of terminal activity. "
            "Always use this structure in plain spoken English:\n"
            "1) Intent: what the user asked for.\n"
            "2) Actions: what was run or done (high-level, no raw commands).\n"
            "3) Outcome: what was found or achieved.\n"
            "Rules:\n"
            "- Sound natural, as if talking to a colleague.\n"
            "- Never read file paths, directory trees, JSON blobs, or raw command output.\n"
            "- Mention errors only as high-level outcome, not stack traces/details.\n"
            "- Keep it concise (2-4 short sentences total).\n"
            "Examples:\n"
            "- 'You asked to inspect the project setup. I listed the main files and checked the package configuration. The project uses a standard frontend setup with the expected scripts and dependencies.'\n"
            "- 'You asked to verify what happened in the terminal. I reviewed the recent steps and tool checks. The flow completed successfully and produced the expected result.'\n"
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
