"""Settings API for Decisions MCP server mode (Third party > MCP server card).

All paths are under ``/api/mcp/`` which the server middleware token-gates for
GET as well as writes. The token is never logged; responses are ``no-store``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mcp/server-mode", tags=["mcp-server-mode"])
_NO_STORE = {"Cache-Control": "no-store"}


def _base_url(request: Request) -> str:
    host = request.url.hostname or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost", "::1"):
        host = "127.0.0.1"
    port = request.url.port or 8765
    return f"http://{host}:{port}"


@router.get("")
async def get_server_mode(request: Request):
    from distr.core.mcp_server import config as c

    enabled = c.is_enabled()
    mounted = bool(getattr(request.app.state, "mcp_server_mounted", False))
    tok = c.get_or_create_token()
    return JSONResponse({
        "enabled": enabled,
        "relay_vendors": c.relay_vendors_enabled(),
        "mounted": mounted,
        "restart_required": enabled != mounted,
        "url": _base_url(request) + "/mcp/",
        "token_masked": c.mask_token(tok),
        "token_from_env": c.token_is_env_override(),
        "stdio_command": ["<decisions venv>/bin/python", "-m", "distr.core.mcp_server"],
    }, headers=_NO_STORE)


@router.post("")
async def post_server_mode(request: Request):
    from distr.core.mcp_server import config as c

    body = await request.json()
    c.set_enabled(bool(body.get("enabled")))
    mounted = bool(getattr(request.app.state, "mcp_server_mounted", False))
    enabled = c.is_enabled()
    return JSONResponse({"success": True, "enabled": enabled,
                         "restart_required": enabled != mounted}, headers=_NO_STORE)


@router.get("/token")
async def get_token():
    from distr.core.mcp_server.config import get_or_create_token

    return JSONResponse({"token": get_or_create_token()}, headers=_NO_STORE)


@router.post("/token/regenerate")
async def regenerate():
    from distr.core.mcp_server import config as c

    try:
        tok = c.regenerate_token()
    except RuntimeError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=409)
    return JSONResponse({"success": True, "token_masked": c.mask_token(tok)}, headers=_NO_STORE)
