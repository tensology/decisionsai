"""Owner side of the single MCP hub: ``/api/internal/mcp/hub/*``.

``/api/internal/*`` is internal-token protected by the server middleware. The
web-server process lazily starts (once) the real hub; spawned workers use
:class:`distr.core.mcp.remote_hub.RemoteMCPHub` against these routes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/internal/mcp/hub", tags=["mcp-hub"])


def _owner_hub():
    from distr.core.mcp.runtime import get_mcp_hub, init_mcp_stack

    if get_mcp_hub() is None:
        init_mcp_stack()
    return get_mcp_hub()


def hub_state() -> dict[str, Any]:
    hub = _owner_hub()
    servers = []
    if hub is not None:
        for name in hub.enabled_connected_servers():
            try:
                tools = hub.list_tools(name)
            except Exception as e:
                logger.debug("hub state list_tools %s failed: %s", name, e)
                continue
            servers.append({"name": name, "tools": tools})
    return {"servers": servers}


@router.get("/state")
async def get_hub_state():
    return JSONResponse(await asyncio.to_thread(hub_state))


@router.post("/call")
async def post_hub_call(request: Request):
    body = await request.json()
    server, tool = str(body.get("server") or ""), str(body.get("tool") or "")
    args = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}

    def _call():
        hub = _owner_hub()
        if hub is None:
            raise RuntimeError("MCP hub unavailable")
        return hub.call_tool(server, tool, args)

    try:
        result = await asyncio.to_thread(_call)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:500]})
    return JSONResponse({"ok": True, "result": result})
