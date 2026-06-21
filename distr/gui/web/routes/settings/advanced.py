"""
Advanced routes — /advanced, /advanced/directories/*, /advanced/files/*,
/advanced/reindex, /advanced/connection-status, /advanced/validate/*,
/advanced/google/*, /advanced/trello/*, /advanced/accounts, /advanced/telegram/*
"""
from fastapi import Request, HTTPException, File, UploadFile, Form
from fastapi.responses import JSONResponse
from typing import Dict, Any
import json
import os

from distr.core.integrations.telegram.utils import relay_internal_token

from ._shared import (
    logger,
    parse_connected_accounts,
    redact_connected_account,
    resolve_secret_update,
    validate_safe_outbound_url,
    rate_limiter,
    route_handler,
    AdvancedSettings,
)

# In-memory store for OAuth state tokens
_oauth_states: Dict[str, float] = {}


def _relay_headers() -> dict:
    """Return headers for authenticating with the www.decisionsai.net relay server."""
    token = relay_internal_token()
    if token:
        return {"X-Relay-Internal-Token": token}
    return {}


def _whatsapp_relay_base_url() -> str:
    """
    Resolve WhatsApp relay API base URL independent of generic DEBUG.

    Local UI development often sets DEBUG without a local relay process, which
    would otherwise force failing localhost calls.
    """
    explicit = str(os.environ.get("DECISIONSAI_WA_API_BASE") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    use_local_relay = str(os.environ.get("DECISIONSAI_USE_LOCAL_RELAY", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if use_local_relay:
        return "http://localhost:8090/api/whatsapp"
    return "https://www.decisionsai.net/api/whatsapp"


def _telegram_relay_base_url() -> str:
    """
    Resolve Telegram relay base URL independent of generic DEBUG.

    Local UI development often enables DEBUG without also running the local
    Telegram relay, so using DEBUG as the switch causes false localhost
    connection failures from the settings screen.
    """
    explicit = str(os.environ.get("DECISIONSAI_TELEGRAM_API_BASE") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    use_local_relay = str(os.environ.get("DECISIONSAI_USE_LOCAL_RELAY", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if use_local_relay:
        return "http://localhost:8090"
    return "https://www.decisionsai.net"


def _telegram_relay_candidates() -> list[str]:
    primary = _telegram_relay_base_url().rstrip("/")
    candidates = [primary]
    hosted = "https://www.decisionsai.net"
    if primary != hosted:
        candidates.append(hosted)
    return candidates


def register_routes(router, templates):

    @router.get("/advanced")
    @route_handler("load advanced settings")
    async def get_advanced_settings():
        """Get current advanced settings (indexed_folders + excluded_files, same as native advanced tab)."""
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()

        indexed_folders = settings.get("indexed_folders") or "[]"
        if isinstance(indexed_folders, str):
            try:
                indexed_folders = json.loads(indexed_folders)
            except Exception:
                indexed_folders = []
        if not isinstance(indexed_folders, list):
            indexed_folders = []

        return JSONResponse({
            "indexed_folders": indexed_folders,
            "exclude_types": settings.get("excluded_files", ""),
            "orchestrator_enabled": bool(settings.get("orchestrator_enabled", True)),
            "orchestrator_memory_export_enabled": bool(settings.get("orchestrator_memory_export_enabled", False)),
            "orchestrator_provider": settings.get("orchestrator_provider", "") or "",
            "orchestrator_model": settings.get("orchestrator_model", "") or "",
            "orchestrator_validator_provider": settings.get("orchestrator_validator_provider", "") or "",
            "orchestrator_validator_model": settings.get("orchestrator_validator_model", "") or "",
            "orchestrator_correction_provider": settings.get("orchestrator_correction_provider", "") or "",
            "orchestrator_correction_model": settings.get("orchestrator_correction_model", "") or "",
        })

    @router.post("/advanced")
    @route_handler("save advanced settings")
    async def save_advanced_settings(settings_data: AdvancedSettings):
        """Save advanced settings (indexed_folders + excluded_files, same as native advanced tab)."""
        from distr.core.settings import load_settings_from_db, save_settings_to_db

        settings = load_settings_from_db()
        settings["excluded_files"] = (settings_data.exclude_types or "").strip()
        settings["indexed_folders"] = settings_data.indexed_folders
        settings["orchestrator_enabled"] = bool(settings_data.orchestrator_enabled)
        settings["orchestrator_memory_export_enabled"] = bool(settings_data.orchestrator_memory_export_enabled)
        settings["orchestrator_provider"] = (settings_data.orchestrator_provider or "").strip()
        settings["orchestrator_model"] = (settings_data.orchestrator_model or "").strip()
        settings["orchestrator_validator_provider"] = (settings_data.orchestrator_validator_provider or "").strip()
        settings["orchestrator_validator_model"] = (settings_data.orchestrator_validator_model or "").strip()
        settings["orchestrator_correction_provider"] = (settings_data.orchestrator_correction_provider or "").strip()
        settings["orchestrator_correction_model"] = (settings_data.orchestrator_correction_model or "").strip()
        if settings["orchestrator_provider"] or settings["orchestrator_model"]:
            settings["workflow_llm_provider"] = settings["orchestrator_provider"]
            settings["workflow_llm_model"] = settings["orchestrator_model"]
        save_settings_to_db(settings)

        return JSONResponse({"success": True, "message": "Advanced settings saved"})

    @router.get("/advanced/directories/root")
    @route_handler("get directories root", fallback={"path": "", "name": "~", "children": []})
    async def get_directories_root():
        """Return home directory path and its child directories."""
        import os
        home = os.path.realpath(os.path.expanduser("~"))
        children = []
        try:
            for name in sorted(os.listdir(home)):
                if name.startswith("."):
                    continue
                full = os.path.join(home, name)
                if os.path.isdir(full):
                    children.append({"name": name, "path": full})
        except OSError as e:
            logger.warning(f"Could not list home dir: {e}")
        return JSONResponse({"path": home, "name": os.path.basename(home) or "~", "children": children})

    @router.get("/advanced/directories/children")
    async def get_directories_children(request: Request):
        """Return child directories for a path (lazy load like native populate_directory). Path must be under ~."""
        try:
            import os
            path = request.query_params.get("path", "")
            path = path.strip()
            home = os.path.realpath(os.path.expanduser("~"))
            try:
                real = os.path.realpath(path)
            except Exception:
                return JSONResponse({"path": path, "children": []})
            if real != home and not real.startswith(home + os.sep):
                return JSONResponse({"path": path, "children": []})
            children = []
            try:
                for name in sorted(os.listdir(real)):
                    if name.startswith("."):
                        continue
                    full = os.path.join(real, name)
                    if os.path.isdir(full):
                        children.append({"name": name, "path": full})
            except OSError as e:
                logger.warning(f"Could not list {real}: {e}")
            return JSONResponse({"path": real, "children": children})
        except Exception as e:
            logger.error(f"Failed to get directory children: {e}", exc_info=True)
            return JSONResponse({"path": path, "children": []})

    @router.get("/advanced/directories/files")
    async def get_directory_files(request: Request):
        """Return files (not directories) in a given path. Path must be under ~."""
        try:
            import os
            path = request.query_params.get("path", "").strip()
            home = os.path.realpath(os.path.expanduser("~"))
            try:
                real = os.path.realpath(path)
            except Exception:
                return JSONResponse({"path": path, "files": []})
            if real != home and not real.startswith(home + os.sep):
                return JSONResponse({"path": path, "files": []})
            files = []
            try:
                for name in sorted(os.listdir(real)):
                    if name.startswith("."):
                        continue
                    full = os.path.join(real, name)
                    if os.path.isfile(full):
                        try:
                            stat = os.stat(full)
                            files.append({"name": name, "path": full, "size": stat.st_size})
                        except OSError:
                            files.append({"name": name, "path": full, "size": 0})
            except OSError as e:
                logger.warning(f"Could not list files in {real}: {e}")
            return JSONResponse({"path": real, "files": files})
        except Exception as e:
            logger.error(f"Failed to get directory files: {e}", exc_info=True)
            return JSONResponse({"path": path, "files": []})

    @router.get("/advanced/files/download")
    async def download_file(request: Request):
        """Download a single file by path. Path must be under ~."""
        import os
        from fastapi.responses import FileResponse
        path = request.query_params.get("path", "").strip()
        if not path:
            raise HTTPException(400, "Missing path parameter")
        home = os.path.realpath(os.path.expanduser("~"))
        try:
            real = os.path.realpath(path)
        except Exception:
            raise HTTPException(400, "Invalid path")
        if real != home and not real.startswith(home + os.sep):
            raise HTTPException(403, "Path outside home directory")
        if not os.path.isfile(real):
            raise HTTPException(404, "File not found")
        return FileResponse(real, filename=os.path.basename(real))

    @router.post("/advanced/files/upload")
    async def upload_files_to_directory(
        request: Request,
        directory: str = Form(...),
        files: list[UploadFile] = File(...)
    ):
        """Upload one or more files to a target directory on the desktop. Directory must be under ~."""
        import os
        home = os.path.realpath(os.path.expanduser("~"))
        try:
            real_dir = os.path.realpath(directory)
        except Exception:
            raise HTTPException(400, "Invalid directory")
        if real_dir != home and not real_dir.startswith(home + os.sep):
            raise HTTPException(403, "Directory outside home")
        if not os.path.isdir(real_dir):
            raise HTTPException(404, "Directory not found")
        saved = []
        for f in files:
            safe_name = os.path.basename(f.filename) if f.filename else "unnamed"
            dest = os.path.join(real_dir, safe_name)
            try:
                content = await f.read()
                with open(dest, "wb") as out:
                    out.write(content)
                saved.append({"name": safe_name, "path": dest, "size": len(content)})
            except Exception as e:
                logger.error(f"Failed to save uploaded file {safe_name}: {e}")
                saved.append({"name": safe_name, "error": str(e)})
        return JSONResponse({"success": True, "files": saved})

    @router.post("/advanced/files/upload-base64")
    async def upload_file_base64(request: Request):
        """Upload a single file via base64 JSON (for WebSocket relay from remote).
        Body: { "directory": "/path/to/dir", "filename": "photo.jpg", "data": "<base64>" }
        """
        import os, base64
        body = await request.json()
        directory = body.get("directory", "").strip()
        filename = body.get("filename", "").strip()
        b64data = body.get("data", "")
        if not directory or not filename or not b64data:
            raise HTTPException(400, "directory, filename, and data required")
        home = os.path.realpath(os.path.expanduser("~"))
        try:
            real_dir = os.path.realpath(directory)
        except Exception:
            raise HTTPException(400, "Invalid directory")
        if real_dir != home and not real_dir.startswith(home + os.sep):
            raise HTTPException(403, "Directory outside home")
        if not os.path.isdir(real_dir):
            raise HTTPException(404, "Directory not found")
        safe_name = os.path.basename(filename)
        dest = os.path.join(real_dir, safe_name)
        try:
            content = base64.b64decode(b64data)
            with open(dest, "wb") as out:
                out.write(content)
            return JSONResponse({"success": True, "name": safe_name, "path": dest, "size": len(content)})
        except Exception as e:
            logger.error(f"Failed to save base64 uploaded file {safe_name}: {e}")
            raise HTTPException(500, str(e))

    @router.get("/advanced/files/download-base64")
    async def download_file_base64(request: Request):
        """Return file content as base64 JSON (for WebSocket relay to remote).
        Query: ?path=/path/to/file
        """
        import os, base64
        path = request.query_params.get("path", "").strip()
        if not path:
            raise HTTPException(400, "Missing path parameter")
        home = os.path.realpath(os.path.expanduser("~"))
        try:
            real = os.path.realpath(path)
        except Exception:
            raise HTTPException(400, "Invalid path")
        if real != home and not real.startswith(home + os.sep):
            raise HTTPException(403, "Path outside home directory")
        if not os.path.isfile(real):
            raise HTTPException(404, "File not found")
        MAX_SIZE = 50 * 1024 * 1024  # 50MB limit
        size = os.path.getsize(real)
        if size > MAX_SIZE:
            raise HTTPException(413, f"File too large ({size} bytes, max {MAX_SIZE})")
        try:
            with open(real, "rb") as f:
                content = f.read()
            return JSONResponse({
                "name": os.path.basename(real),
                "path": real,
                "size": size,
                "data": base64.b64encode(content).decode("ascii")
            })
        except Exception as e:
            logger.error(f"Failed to read file for base64 download: {e}")
            raise HTTPException(500, str(e))

    @router.post("/advanced/reindex")
    @route_handler("reindex models")
    async def reindex_models():
        """Reindex all folders from settings into the RAG system."""
        import asyncio
        from distr.core.settings import load_settings_from_db as _load
        settings = _load()
        model_name = settings.get('agent_model', 'deepseek-v4-pro:cloud') or 'deepseek-v4-pro:cloud'
        exclude_text = settings.get('excluded_files', '')
        exclude_extensions = None
        if exclude_text:
            exclude_extensions = [
                ext.strip() if ext.strip().startswith('.') else f".{ext.strip()}"
                for ext in exclude_text.split(',')
                if ext.strip()
            ]

        from distr.core.agent.services.rag.integration import index_settings_folders
        logger.info("Reindexing folders from settings...")
        result = await asyncio.to_thread(
            index_settings_folders,
            model_name=model_name,
            exclude_extensions=exclude_extensions,
        )

        if result.get('success'):
            return JSONResponse({
                "success": True,
                "message": f"Reindexed {result.get('folders_indexed', 0)} folder(s), "
                           f"{result.get('files_processed', 0)} file(s), "
                           f"{result.get('chunks_created', 0)} chunk(s)"
            })
        else:
            return JSONResponse(
                {"success": False, "error": result.get('error', 'Unknown error')},
                status_code=500
            )

    # --- Connection status and accounts (Google, Jira, Trello, Telegram) ---

    @router.get("/advanced/connection-status")
    @route_handler(
        "get connection status",
        fallback={
            "google_connected": False,
            "telegram_connected": False,
            "whatsapp_connected": False,
            "discord_bot_configured": False,
            "slack_bot_configured": False,
            "slack_signing_configured": False,
            "jira_accounts": [],
            "trello_accounts": [],
            "jira_has_valid": False,
            "trello_has_valid": False,
            "clickup_configured": False,
            "monday_configured": False,
        },
    )
    async def get_connection_status():
        """Return connection status for Google, Telegram, Jira, Trello."""
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        connected_accounts = parse_connected_accounts(settings)
        google_connected = any(
            isinstance(acc, dict) and acc.get("provider") == "google" and acc.get("access_token")
            for acc in connected_accounts
        )
        # Google also needs the OAuth client secret file for token refresh
        if google_connected:
            from distr.gui.web.oauth import load_google_oauth_config
            if not load_google_oauth_config():
                google_connected = False
        whatsapp_connected = any(
            isinstance(acc, dict) and acc.get("provider") == "whatsapp" and acc.get("status") == "connected"
            for acc in connected_accounts
        )
        telegram_connected = any(
            isinstance(acc, dict) and acc.get("provider") == "telegram" and (acc.get("app_user_id") or acc.get("user_id"))
            for acc in connected_accounts
        )
        jira_accounts = [acc for acc in connected_accounts if isinstance(acc, dict) and acc.get("provider") == "jira"]
        trello_accounts = [acc for acc in connected_accounts if isinstance(acc, dict) and acc.get("provider") == "trello"]
        jira_has_valid = sum(1 for a in jira_accounts if a.get("is_valid", False)) > 0
        trello_has_valid = sum(1 for a in trello_accounts if a.get("is_valid", False)) > 0

        discord_bot_configured = bool((os.environ.get("DECISIONSAI_DISCORD_BOT_TOKEN") or "").strip())
        slack_bot_configured = bool((os.environ.get("DECISIONSAI_SLACK_BOT_TOKEN") or "").strip())
        slack_signing_configured = bool((os.environ.get("DECISIONSAI_SLACK_SIGNING_SECRET") or "").strip())
        clickup_configured = False
        monday_configured = False
        for acc in connected_accounts:
            if not isinstance(acc, dict):
                continue
            if acc.get("provider") == "discord_bot" and (acc.get("bot_token") or "").strip():
                discord_bot_configured = True
            if acc.get("provider") == "slack_app":
                if (acc.get("bot_token") or "").strip():
                    slack_bot_configured = True
                if (acc.get("signing_secret") or "").strip():
                    slack_signing_configured = True
            if acc.get("provider") == "clickup" and (acc.get("api_token") or "").strip():
                clickup_configured = True
            if acc.get("provider") == "monday" and (acc.get("api_token") or "").strip():
                monday_configured = True

        return JSONResponse({
            "google_connected": google_connected,
            "whatsapp_connected": whatsapp_connected,
            "telegram_connected": telegram_connected,
            "discord_bot_configured": discord_bot_configured,
            "slack_bot_configured": slack_bot_configured,
            "slack_signing_configured": slack_signing_configured,
            "jira_accounts": jira_accounts,
            "trello_accounts": trello_accounts,
            "jira_has_valid": jira_has_valid,
            "trello_has_valid": trello_has_valid,
            "clickup_configured": clickup_configured,
            "monday_configured": monday_configured,
        })

    @router.get("/advanced/integration-connectors")
    @route_handler("load integration connectors", fallback={})
    async def get_integration_connectors():
        """Masked Discord / Slack credentials saved under Advanced (or overridden by env)."""
        from distr.gui.web.security import mask_secret
        from distr.core.integrations.token_resolve import (
            PROVIDER_DISCORD_BOT,
            PROVIDER_SLACK_APP,
            integration_accounts_from_settings,
        )
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        accounts = integration_accounts_from_settings(settings)
        d_acc = next((a for a in accounts if a.get("provider") == PROVIDER_DISCORD_BOT), {})
        s_acc = next((a for a in accounts if a.get("provider") == PROVIDER_SLACK_APP), {})
        c_acc = next((a for a in accounts if a.get("provider") == "clickup"), {})
        m_acc = next((a for a in accounts if a.get("provider") == "monday"), {})
        d_tok = (d_acc.get("bot_token") or "").strip()
        s_bt = (s_acc.get("bot_token") or "").strip()
        s_sg = (s_acc.get("signing_secret") or "").strip()
        c_tok = (c_acc.get("api_token") or "").strip()
        m_tok = (m_acc.get("api_token") or "").strip()
        return JSONResponse({
            "discord_bot_token": mask_secret(d_tok),
            "slack_bot_token": mask_secret(s_bt),
            "slack_signing_secret": mask_secret(s_sg),
            "clickup_api_token": mask_secret(c_tok),
            "monday_api_token": mask_secret(m_tok),
            "discord_bot_token_set": bool(d_tok),
            "slack_bot_token_set": bool(s_bt),
            "slack_signing_secret_set": bool(s_sg),
            "clickup_api_token_set": bool(c_tok),
            "monday_api_token_set": bool(m_tok),
            "discord_from_env": bool((os.environ.get("DECISIONSAI_DISCORD_BOT_TOKEN") or "").strip()),
            "slack_bot_from_env": bool((os.environ.get("DECISIONSAI_SLACK_BOT_TOKEN") or "").strip()),
            "slack_signing_from_env": bool((os.environ.get("DECISIONSAI_SLACK_SIGNING_SECRET") or "").strip()),
            "slack_events_url_hint": "/hooks/slack/events",
        })

    @router.post("/advanced/integration-connectors")
    @route_handler("save integration connectors")
    async def post_integration_connectors(body: dict):
        """Persist Discord bot token and Slack bot token + signing secret (encrypted with other connected_accounts)."""
        from distr.core.settings import load_settings_from_db, save_settings_to_db
        from distr.core.integrations.token_resolve import PROVIDER_DISCORD_BOT, PROVIDER_SLACK_APP

        def strip_provider(lst: list, provider: str) -> list:
            return [a for a in lst if not (isinstance(a, dict) and a.get("provider") == provider)]

        settings = load_settings_from_db()
        accounts = parse_connected_accounts(settings)

        existing_d = next(
            (a for a in accounts if isinstance(a, dict) and a.get("provider") == PROVIDER_DISCORD_BOT),
            {},
        )
        existing_s = next(
            (a for a in accounts if isinstance(a, dict) and a.get("provider") == PROVIDER_SLACK_APP),
            {},
        )
        existing_c = next(
            (a for a in accounts if isinstance(a, dict) and a.get("provider") == "clickup"),
            {},
        )
        existing_m = next(
            (a for a in accounts if isinstance(a, dict) and a.get("provider") == "monday"),
            {},
        )

        if "discord_bot_token" in body:
            accounts = strip_provider(accounts, PROVIDER_DISCORD_BOT)
            inc_d = str(body.get("discord_bot_token") or "").strip()
            if inc_d:
                new_tok = resolve_secret_update(existing_d.get("bot_token") or "", inc_d)
                if new_tok.strip():
                    accounts.append({"provider": PROVIDER_DISCORD_BOT, "bot_token": new_tok.strip()})

        if "slack_bot_token" in body or "slack_signing_secret" in body:
            accounts = strip_provider(accounts, PROVIDER_SLACK_APP)
            new_bt = (existing_s.get("bot_token") or "").strip()
            new_sg = (existing_s.get("signing_secret") or "").strip()
            if "slack_bot_token" in body:
                inc = str(body.get("slack_bot_token") or "").strip()
                if not inc:
                    new_bt = ""
                else:
                    new_bt = resolve_secret_update(existing_s.get("bot_token") or "", inc).strip()
            if "slack_signing_secret" in body:
                inc = str(body.get("slack_signing_secret") or "").strip()
                if not inc:
                    new_sg = ""
                else:
                    new_sg = resolve_secret_update(existing_s.get("signing_secret") or "", inc).strip()
            if new_bt or new_sg:
                row: Dict[str, Any] = {"provider": PROVIDER_SLACK_APP}
                if new_bt:
                    row["bot_token"] = new_bt
                if new_sg:
                    row["signing_secret"] = new_sg
                accounts.append(row)

        if "clickup_api_token" in body:
            accounts = strip_provider(accounts, "clickup")
            inc = str(body.get("clickup_api_token") or "").strip()
            if inc:
                new_tok = resolve_secret_update(existing_c.get("api_token") or "", inc).strip()
                if new_tok:
                    accounts.append({"provider": "clickup", "name": "ClickUp", "api_token": new_tok})

        if "monday_api_token" in body:
            accounts = strip_provider(accounts, "monday")
            inc = str(body.get("monday_api_token") or "").strip()
            if inc:
                new_tok = resolve_secret_update(existing_m.get("api_token") or "", inc).strip()
                if new_tok:
                    accounts.append({"provider": "monday", "name": "Monday", "api_token": new_tok})

        settings["connected_accounts"] = accounts
        save_settings_to_db(settings)

        try:
            from distr.core.integrations.slack.outbound import start_slack_outbound_worker_background

            start_slack_outbound_worker_background()
        except Exception:
            logger.debug("Slack outbound worker refresh after save skipped", exc_info=True)

        return JSONResponse({
            "success": True,
            "restart_note": "Restart the desktop app if you changed the Discord bot token so the bot thread picks it up.",
        })

    @router.post("/advanced/google/disconnect")
    async def disconnect_google():
        """Remove Google OAuth tokens and delete the client secret file."""
        try:
            from distr.core.settings import load_settings_from_db, save_settings_to_db
            # Remove Google account from connected_accounts in DB
            settings = load_settings_from_db()
            connected_accounts = parse_connected_accounts(settings)
            connected_accounts = [a for a in connected_accounts if not (isinstance(a, dict) and a.get("provider") == "google")]
            # Pass a list — save_settings_to_db encrypts per-account secrets then JSON-encodes once.
            # json.dumps(list) here skipped encryption and double-encoded, so Google tokens could reappear as "connected" incorrectly.
            save_settings_to_db({"connected_accounts": connected_accounts})
            # Delete the client secret file from canonical location
            from distr.core.paths import GOOGLE_OAUTH_SECRET_PATH
            if os.path.isfile(GOOGLE_OAUTH_SECRET_PATH):
                os.unlink(GOOGLE_OAUTH_SECRET_PATH)
            logger.info("Google disconnected: tokens removed, secret file deleted")
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error(f"Google disconnect: {e}", exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    # ── WhatsApp proxy endpoints ──────────────────────────────────────────

    @router.get("/advanced/whatsapp/qr")
    async def get_whatsapp_qr():
        """Proxy: get current WhatsApp QR code from the Baileys service."""
        try:
            import httpx
            base_url = _whatsapp_relay_base_url()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base_url}/qr", headers=_relay_headers())
                return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except Exception as e:
            logger.error(f"WhatsApp QR proxy: {e}")
            return JSONResponse({"status": "error", "qr_code": None, "error": str(e)}, status_code=500)

    @router.get("/advanced/whatsapp/status")
    async def get_whatsapp_status():
        """Proxy: get current WhatsApp connection status."""
        try:
            import httpx
            base_url = _whatsapp_relay_base_url()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base_url}/status", headers=_relay_headers())
                return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except Exception as e:
            logger.error(f"WhatsApp status proxy: {e}")
            return JSONResponse({"status": "error", "error": str(e)}, status_code=500)

    @router.post("/advanced/whatsapp/disconnect")
    async def disconnect_whatsapp():
        """Proxy: disconnect WhatsApp and clear session."""
        try:
            import httpx
            base_url = _whatsapp_relay_base_url()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"{base_url}/disconnect", headers=_relay_headers())
                data = resp.json()
                if data.get("success"):
                    # Also remove from connected_accounts
                    settings = load_settings_from_db()
                    connected_accounts = parse_connected_accounts(settings)
                    connected_accounts = [a for a in connected_accounts if not (isinstance(a, dict) and a.get("provider") == "whatsapp")]
                    settings["connected_accounts"] = connected_accounts
                    save_settings_to_db(settings)
                return JSONResponse(content=data, status_code=resp.status_code)
        except Exception as e:
            logger.error(f"WhatsApp disconnect proxy: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/advanced/whatsapp/save")
    async def whatsapp_save(body: dict):
        """Save WhatsApp connection to connected_accounts."""
        try:
            from distr.core.settings import load_settings_from_db, save_settings_to_db
            jid = body.get("jid") or ""
            name = body.get("name") or ""
            push_name = body.get("push_name") or ""
            settings = load_settings_from_db()
            connected_accounts = parse_connected_accounts(settings)
            existing = next((a for a in connected_accounts if isinstance(a, dict) and a.get("provider") == "whatsapp"), None)
            if not existing:
                connected_accounts.append({"provider": "whatsapp", "jid": jid, "name": name, "push_name": push_name, "status": "connected"})
            else:
                existing["jid"] = jid
                existing["name"] = name
                existing["push_name"] = push_name
                existing["status"] = "connected"
            settings["connected_accounts"] = connected_accounts
            save_settings_to_db(settings)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error(f"WhatsApp save: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)})

    @router.post("/advanced/validate/jira")
    async def validate_jira(body: dict):
        """Validate Jira credentials (same logic as native CredentialValidationThread._validate_jira)."""
        try:
            import base64
            import requests
            # soft global throttle on credential-validation probes
            if not rate_limiter.allow("settings_validate_jira", limit=30, window_seconds=60):
                return JSONResponse({"valid": False, "error": "Too many validation requests. Try again shortly."})
            server_url = (body.get("server_url") or "").strip()
            email = (body.get("email") or "").strip()
            api_token = (body.get("api_token") or "").strip()
            if not server_url or not email or not api_token:
                return JSONResponse({"valid": False, "error": "All fields are required"})
            if not server_url.startswith(("http://", "https://")):
                server_url = "https://" + server_url
            server_url = validate_safe_outbound_url(server_url)
            api_url = f"{server_url}/rest/api/3/myself"
            auth_string = f"{email}:{api_token}"
            auth_b64 = base64.b64encode(auth_string.encode("ascii")).decode("ascii")
            headers = {"Authorization": f"Basic {auth_b64}", "Accept": "application/json"}
            response = requests.get(api_url, headers=headers, timeout=10, allow_redirects=False)
            if response.status_code == 200:
                return JSONResponse({"valid": True, "error": ""})
            if response.status_code == 401:
                return JSONResponse({"valid": False, "error": "Invalid credentials. Check email and API token."})
            if response.status_code == 404:
                return JSONResponse({"valid": False, "error": "Server URL not found."})
            return JSONResponse({"valid": False, "error": f"Connection failed: HTTP {response.status_code}"})
        except Exception as e:
            err = str(e)
            if "Connection" in err or "timeout" in err.lower():
                err = "Connection error. Check server URL and network."
            return JSONResponse({"valid": False, "error": err})

    @router.post("/advanced/validate/trello")
    async def validate_trello(body: dict):
        """Validate Trello credentials (same as native CredentialValidationThread._validate_trello)."""
        try:
            from distr.core.integrations.trello_api import TrelloAPI
            if not rate_limiter.allow("settings_validate_trello", limit=30, window_seconds=60):
                return JSONResponse({"valid": False, "error": "Too many validation requests. Try again shortly."})
            api_key = (body.get("api_key") or "").strip()
            api_token = (body.get("api_token") or "").strip()
            if not api_key or not api_token:
                return JSONResponse({"valid": False, "error": "Both API key and API token are required"})
            trello_api = TrelloAPI(api_key, api_token)
            if trello_api.test_connection():
                return JSONResponse({"valid": True, "error": ""})
            return JSONResponse({"valid": False, "error": "Invalid credentials. Check API key and token."})
        except Exception as e:
            return JSONResponse({"valid": False, "error": str(e)})

    @router.post("/advanced/google/upload-config")
    async def upload_google_oauth_config(request: Request):
        """Handle Google OAuth config JSON file upload."""
        try:
            import json as _json
            body = await request.json()
            content = body.get("content")
            if not content:
                return JSONResponse(status_code=400, content={"success": False, "error": "No content provided"})
            try:
                json_data = _json.loads(content) if isinstance(content, str) else content
            except (ValueError, TypeError):
                return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON"})
            if "web" not in json_data and "installed" not in json_data:
                return JSONResponse(status_code=400, content={"success": False, "error": "Not a valid Google OAuth client secret file"})
            # Auto-inject the correct redirect URI and JS origin for this server
            base = str(request.base_url).rstrip("/")
            redirect_uri = base + "/api/advanced/google/callback"
            config_key = "web" if "web" in json_data else "installed"
            uris = json_data[config_key].get("redirect_uris", [])
            if redirect_uri not in uris:
                uris.append(redirect_uri)
                json_data[config_key]["redirect_uris"] = uris
            origins = json_data[config_key].get("javascript_origins", [])
            if base not in origins:
                origins.append(base)
                json_data[config_key]["javascript_origins"] = origins
            from distr.core.paths import SECRETS_DIR, GOOGLE_OAUTH_SECRET_PATH
            os.makedirs(SECRETS_DIR, exist_ok=True)
            target_path = GOOGLE_OAUTH_SECRET_PATH
            with open(target_path, "w") as f:
                _json.dump(json_data, f, indent=2)
            logger.info(f"Google OAuth config saved to {target_path}")
            return JSONResponse({"success": True, "message": "Configuration saved"})
        except Exception as e:
            logger.error(f"Google config upload: {e}", exc_info=True)
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @router.get("/advanced/google/oauth-url")
    async def get_google_oauth_url(request: Request):
        """Return Google OAuth auth URL and state (redirect_uri = this server callback)."""
        try:
            import uuid
            from urllib.parse import urlencode
            from distr.gui.web.oauth import load_google_oauth_config
            oauth_config = load_google_oauth_config()
            base = str(request.base_url).rstrip("/")
            redirect_uri = base + "/api/advanced/google/callback"
            if not oauth_config:
                return JSONResponse({
                    "url": None,
                    "needs_config": True,
                    "javascript_origin": base,
                    "redirect_uri": redirect_uri,
                })
            web_config = oauth_config.get("web", {})
            client_id = web_config.get("client_id")
            if not client_id:
                return JSONResponse({"url": None, "error": "Client ID not found in OAuth config."})
            state = str(uuid.uuid4())
            _oauth_states[state] = __import__("time").time()
            scopes = [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/documents",
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/presentations",
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/calendar.events",
                "openid", "email", "profile",
            ]
            params = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
            auth_uri = web_config.get("auth_uri", "https://accounts.google.com/o/oauth2/auth")
            url = f"{auth_uri}?{urlencode(params)}"
            return JSONResponse({"url": url, "state": state})
        except Exception as e:
            logger.error(f"Google OAuth URL: {e}", exc_info=True)
            return JSONResponse({"url": None, "error": str(e)})

    @router.get("/advanced/google/callback")
    async def google_oauth_callback(request: Request):
        """Exchange code for tokens and save to DB; redirect to settings with success."""
        import requests as req
        from datetime import datetime
        from fastapi.responses import RedirectResponse
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")
        if error:
            return RedirectResponse(url="/settings?google=error#advanced", status_code=302)
        if not code or not state:
            return RedirectResponse(url="/settings#advanced", status_code=302)
        if state not in _oauth_states:
            return RedirectResponse(url="/settings#advanced", status_code=302)
        del _oauth_states[state]
        try:
            from distr.gui.web.oauth import load_google_oauth_config
            from distr.core.settings import load_settings_from_db, save_settings_to_db
            oauth_config = load_google_oauth_config()
            if not oauth_config:
                return RedirectResponse(url="/settings#advanced", status_code=302)
            web_config = oauth_config.get("web", {})
            client_id = web_config.get("client_id")
            client_secret = web_config.get("client_secret")
            base = str(request.base_url).rstrip("/")
            redirect_uri = base + "/api/advanced/google/callback"
            token_data = {"code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"}
            token_response = req.post("https://oauth2.googleapis.com/token", data=token_data)
            token_response.raise_for_status()
            tokens = token_response.json()
            settings = load_settings_from_db()
            connected_accounts = parse_connected_accounts(settings)
            google_account = next((a for a in connected_accounts if isinstance(a, dict) and a.get("provider") == "google"), None)
            if not google_account:
                google_account = {"provider": "google"}
                connected_accounts.append(google_account)
            google_account["access_token"] = tokens.get("access_token")
            google_account["refresh_token"] = tokens.get("refresh_token")
            google_account["token_type"] = tokens.get("token_type", "Bearer")
            google_account["expires_in"] = tokens.get("expires_in")
            google_account["scope"] = tokens.get("scope")
            google_account["connected_at"] = datetime.utcnow().isoformat()
            settings["connected_accounts"] = connected_accounts
            save_settings_to_db(settings)
            # Reload Google Workspace service with new credentials
            try:
                from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceService
                gws = GoogleWorkspaceService()
                gws._load_credentials()
                logger.info("Google Workspace service reloaded with new credentials")
            except Exception as reload_err:
                logger.debug(f"Could not reload Google Workspace service: {reload_err}")
            return RedirectResponse(url="/settings?google=connected#advanced", status_code=302)
        except Exception as e:
            logger.error(f"Google callback: {e}", exc_info=True)
            return RedirectResponse(url="/settings?google=error#advanced", status_code=302)

    @router.get("/advanced/trello/auth-url")
    async def get_trello_auth_url(request: Request):
        """Return Trello auth URL for generating token (same as native Generate Token)."""
        api_key = request.query_params.get("api_key", "").strip()
        if not api_key:
            return JSONResponse({"url": None, "error": "api_key required"})
        url = f"https://trello.com/1/authorize?key={api_key}&name=DecisionsAI&expiration=never&scope=read,write&response_type=token"
        return JSONResponse({"url": url})

    @router.get("/advanced/accounts")
    async def get_accounts(request: Request):
        """List accounts for provider (jira or trello)."""
        provider = request.query_params.get("provider", "")
        if provider not in ("jira", "trello"):
            return JSONResponse({"accounts": []})
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            connected_accounts = parse_connected_accounts(settings)
            accounts = [
                redact_connected_account(a)
                for a in connected_accounts
                if isinstance(a, dict) and a.get("provider") == provider
            ]
            return JSONResponse({"accounts": accounts})
        except Exception as e:
            logger.error(f"Get accounts: {e}", exc_info=True)
            return JSONResponse({"accounts": []})

    @router.post("/advanced/accounts")
    async def post_account(request: Request):
        """Add or update Jira/Trello account; validate before save."""
        try:
            from datetime import datetime
            from distr.core.settings import load_settings_from_db, save_settings_to_db
            body = await request.json()
            provider = (body.get("provider") or "").strip()
            if provider not in ("jira", "trello"):
                return JSONResponse({"success": False, "error": "provider must be jira or trello"})
            name = (body.get("name") or "").strip()
            if not name:
                return JSONResponse({"success": False, "error": "name required"})
            if provider == "jira":
                server_url = (body.get("server_url") or "").strip()
                email = (body.get("email") or "").strip()
                api_token = (body.get("api_token") or "").strip()
                if not server_url or not email or not api_token:
                    return JSONResponse({"success": False, "error": "server_url, email, api_token required"})
                import base64
                import requests as req
                s = server_url
                if not s.startswith("http"): s = "https://" + s
                s = validate_safe_outbound_url(s)
                api_url = f"{s}/rest/api/3/myself"
                auth_b64 = base64.b64encode(f"{email}:{api_token}".encode("ascii")).decode("ascii")
                r = req.get(
                    api_url,
                    headers={"Authorization": f"Basic {auth_b64}", "Accept": "application/json"},
                    timeout=10,
                    allow_redirects=False,
                )
                if r.status_code != 200:
                    return JSONResponse({"success": False, "error": "Jira validation failed. Check credentials."})
                account = {"provider": "jira", "name": name, "server_url": server_url, "email": email, "api_token": api_token, "is_valid": True, "created_at": datetime.utcnow().isoformat()}
            else:
                api_key = (body.get("api_key") or "").strip()
                api_token = (body.get("api_token") or "").strip()
                if not api_key or not api_token:
                    return JSONResponse({"success": False, "error": "api_key and api_token required"})
                from distr.core.integrations.trello_api import TrelloAPI
                if not TrelloAPI(api_key, api_token).test_connection():
                    return JSONResponse({"success": False, "error": "Trello validation failed."})
                account = {"provider": "trello", "name": name, "api_key": api_key, "api_token": api_token, "is_valid": True, "created_at": datetime.utcnow().isoformat()}
            original_name = (body.get("original_name") or "").strip()
            settings = load_settings_from_db()
            connected_accounts = parse_connected_accounts(settings)
            if original_name:
                for i, acc in enumerate(connected_accounts):
                    if isinstance(acc, dict) and acc.get("provider") == provider and acc.get("name") == original_name:
                        connected_accounts[i] = account
                        settings["connected_accounts"] = connected_accounts
                        save_settings_to_db(settings)
                        return JSONResponse({"success": True})
                connected_accounts.append(account)
            else:
                connected_accounts.append(account)
            settings["connected_accounts"] = connected_accounts
            save_settings_to_db(settings)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error(f"Post account: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)})

    @router.delete("/advanced/accounts")
    async def delete_account(request: Request):
        """Remove Jira/Trello account by provider and name."""
        try:
            from distr.core.settings import load_settings_from_db, save_settings_to_db
            body = await request.json()
            provider = (body.get("provider") or "").strip()
            name = (body.get("name") or "").strip()
            if provider not in ("jira", "trello") or not name:
                return JSONResponse({"success": False, "error": "provider and name required"})
            settings = load_settings_from_db()
            connected_accounts = parse_connected_accounts(settings)
            before = len(connected_accounts)
            connected_accounts = [a for a in connected_accounts if not (isinstance(a, dict) and a.get("provider") == provider and a.get("name") == name)]
            if len(connected_accounts) < before:
                settings["connected_accounts"] = connected_accounts
                save_settings_to_db(settings)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error(f"Delete account: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)})

    @router.post("/advanced/telegram/request")
    async def telegram_link_request():
        """Proxy to decisionsai.net to get Telegram QR link (same as native _fetch_qr_code)."""
        try:
            import requests as req

            headers = {"Content-Type": "application/json"}
            relay_token = relay_internal_token()
            if relay_token:
                headers["X-Relay-Internal-Token"] = relay_token

            payload_str = "{}"
            last_error = None
            for server_base in _telegram_relay_candidates():
                api_url = f"{server_base}/api/telegram/link/request/"
                try:
                    response = req.post(api_url, headers=headers, data=payload_str, timeout=10)
                    if response.status_code != 200:
                        last_error = f"HTTP {response.status_code}"
                        continue
                    data = response.json()
                    return JSONResponse({"qr_code": data.get("qr_code"), "token": data.get("token"), "link": data.get("link"), "app_user_id": data.get("app_user_id")})
                except Exception as relay_error:
                    last_error = str(relay_error)
                    continue
            return JSONResponse({"qr_code": None, "token": None, "link": None, "app_user_id": None, "error": last_error or "Telegram relay unavailable"})
        except Exception as e:
            logger.error(f"Telegram request: {e}", exc_info=True)
            return JSONResponse({"qr_code": None, "token": None, "link": None, "app_user_id": None, "error": str(e)})

    @router.post("/advanced/telegram/status")
    async def telegram_link_status(body: dict):
        """Check Telegram link status (proxy to decisionsai.net)."""
        try:
            import requests as req

            headers = {"Content-Type": "application/json"}
            for server_base in _telegram_relay_candidates():
                api_url = f"{server_base}/api/telegram/link/status/"
                try:
                    response = req.get(api_url, headers=headers, params={"token": body.get("token")}, timeout=5)
                    if response.status_code != 200:
                        continue
                    data = response.json()
                    return JSONResponse({"status": data.get("status"), "user_id": data.get("user_id")})
                except Exception:
                    continue
            return JSONResponse({"status": "error"})
        except Exception:
            return JSONResponse({"status": "error"})

    @router.post("/advanced/telegram/save")
    async def telegram_save(body: dict):
        """Save Telegram connection to connected_accounts (same as native signal_manager.telegram_connected)."""
        try:
            from distr.core.settings import load_settings_from_db, save_settings_to_db
            token = body.get("token") or ""
            app_user_id = body.get("app_user_id") or ""
            user_id = body.get("user_id")
            settings = load_settings_from_db()
            connected_accounts = parse_connected_accounts(settings)
            existing = next((a for a in connected_accounts if isinstance(a, dict) and a.get("provider") == "telegram"), None)
            if not existing:
                connected_accounts.append({"provider": "telegram", "token": token, "app_user_id": app_user_id, "user_id": user_id})
            else:
                existing["token"] = token
                existing["app_user_id"] = app_user_id
                existing["user_id"] = user_id
            settings["connected_accounts"] = connected_accounts
            save_settings_to_db(settings)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error(f"Telegram save: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)})

    @router.post("/advanced/telegram/disconnect")
    async def telegram_disconnect():
        """Remove Telegram connection details from connected_accounts."""
        try:
            from distr.core.settings import load_settings_from_db, save_settings_to_db

            settings = load_settings_from_db()
            connected_accounts = parse_connected_accounts(settings)
            settings["connected_accounts"] = [
                a for a in connected_accounts
                if not (isinstance(a, dict) and a.get("provider") == "telegram")
            ]
            save_settings_to_db(settings)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error(f"Telegram disconnect: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.get("/advanced/macos-permissions")
    @route_handler("macOS permission status")
    async def get_macos_permissions():
        import platform

        if platform.system() != "Darwin":
            return JSONResponse({"supported": False, "platform": platform.system()})
        from distr.core.macos_permissions import collect_permission_report

        return JSONResponse(collect_permission_report(start_sidecar=True))

    @router.post("/advanced/macos-permissions/open-pane")
    @route_handler("open macOS privacy pane")
    async def open_macos_privacy_pane(body: dict):
        import platform

        if platform.system() != "Darwin":
            return JSONResponse({"success": False, "error": "macOS only"})
        pane = str(body.get("pane") or "").strip()
        if not pane:
            raise HTTPException(status_code=400, detail="pane is required")
        from distr.core.macos_permissions import open_privacy_pane

        ok = open_privacy_pane(pane)
        return JSONResponse({"success": ok, "pane": pane})

    @router.post("/advanced/macos-permissions/prompt")
    @route_handler("request macOS permission prompt")
    async def prompt_macos_permission(body: dict):
        import platform

        if platform.system() != "Darwin":
            return JSONResponse({"success": False, "error": "macOS only"})
        kind = str(body.get("kind") or "").strip()
        if not kind:
            raise HTTPException(status_code=400, detail="kind is required")
        from distr.core.macos_permissions import request_python_permission

        ok, detail = request_python_permission(kind)
        return JSONResponse({"success": ok, "kind": kind, "detail": detail})
