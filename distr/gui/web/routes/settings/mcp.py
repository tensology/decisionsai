"""
MCP configuration routes — GET/POST ``/mcp`` (``mcp_config.json``, R7).

Persists via ``save_mcp_config``; file watcher hot-reloads the desktop MCP stack.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from ._shared import route_handler


class MCPServerRow(BaseModel):
    """One server row from the Web UI (matches ``mcp_config.json`` schema)."""

    name: str = Field(..., min_length=1, max_length=128)
    enabled: bool = True
    transport: Literal["stdio", "sse"] = "stdio"
    command: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)


class MCPConfigPayload(BaseModel):
    servers: list[MCPServerRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_server_names(self) -> MCPConfigPayload:
        names = [(s.name or "").strip().lower() for s in self.servers]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate MCP server names are not allowed")
        return self


def _payload_to_raw(payload: MCPConfigPayload) -> dict[str, Any]:
    servers: list[dict[str, Any]] = []
    for row in payload.servers:
        d = row.model_dump()
        name = (d.get("name") or "").strip()
        if not name:
            continue
        entry: dict[str, Any] = {
            "name": name,
            "enabled": bool(d.get("enabled", True)),
            "transport": d.get("transport") or "stdio",
        }
        transport = entry["transport"]
        if transport == "stdio":
            cmd = d.get("command") or []
            if isinstance(cmd, list):
                entry["command"] = [str(x) for x in cmd]
            env = d.get("env") or {}
            if isinstance(env, dict) and env:
                entry["env"] = {str(k): str(v) for k, v in env.items()}
        else:
            entry["url"] = str(d.get("url") or "").strip()
            hdr = d.get("headers") or {}
            if isinstance(hdr, dict) and hdr:
                entry["headers"] = {str(k): str(v) for k, v in hdr.items()}
        servers.append(entry)
    return {"servers": servers}


def register_routes(router, templates) -> None:
    @router.get("/mcp")
    @route_handler("load MCP config", fallback={"servers": []})
    async def get_mcp_config():
        from distr.core.mcp.config import document_to_dict, load_mcp_config

        doc = load_mcp_config()
        return JSONResponse(document_to_dict(doc))

    @router.post("/mcp")
    @route_handler("save MCP config")
    async def post_mcp_config(body: MCPConfigPayload):
        from distr.core.mcp.config import parse_config_dict, save_mcp_config

        raw = _payload_to_raw(body)
        doc = parse_config_dict(raw)
        if len(doc.servers) != len(raw["servers"]):
            return JSONResponse(
                {
                    "success": False,
                    "error": (
                        "Invalid MCP configuration: check server names, "
                        "stdio command arrays, and SSE URLs."
                    ),
                },
                status_code=400,
            )
        try:
            save_mcp_config(doc)
        except ValueError as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=400)
        return JSONResponse({"success": True, "message": "MCP config saved to mcp_config.json"})
