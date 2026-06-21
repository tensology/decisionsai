"""
MCP configuration routes — GET/POST ``/mcp`` (``mcp_config.json``, R7).

Persists via ``save_mcp_config``; file watcher hot-reloads the desktop MCP stack.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from fastapi import Request
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


def _safe_server_name(value: Any, fallback: str) -> str:
    raw = str(value or fallback or "mcp_server").strip()
    raw = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
    return raw[:128] or "mcp_server"


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in value.items()
        if isinstance(k, str) and v is not None
    }


def _list_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x is not None]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_external_server(raw: dict[str, Any], fallback_name: str) -> dict[str, Any] | None:
    name = _safe_server_name(
        raw.get("name") or raw.get("id") or raw.get("serverName") or raw.get("key"),
        fallback_name,
    )
    enabled = raw.get("enabled", raw.get("disabled") is not True)
    transport_raw = str(raw.get("transport") or raw.get("type") or "").strip().lower()
    url = str(raw.get("url") or raw.get("endpoint") or raw.get("serverUrl") or "").strip()

    if url or transport_raw in {"sse", "http", "https", "streamable-http", "streamable_http"}:
        return {
            "name": name,
            "enabled": bool(enabled),
            "transport": "sse",
            "url": url,
            "headers": _string_map(raw.get("headers") or raw.get("httpHeaders")),
        }

    command_parts: list[str] = []
    command = raw.get("command") or raw.get("cmd") or raw.get("executable") or raw.get("bin")
    args = raw.get("args") or raw.get("arguments")
    if isinstance(command, list):
        command_parts.extend(_list_strings(command))
    elif isinstance(command, str) and command.strip():
        command_parts.append(command.strip())
    command_parts.extend(_list_strings(args))

    if not command_parts and isinstance(raw.get("stdio"), dict):
        return _normalize_external_server({**raw["stdio"], "name": name, "enabled": enabled}, name)
    if not command_parts:
        return None

    entry: dict[str, Any] = {
        "name": name,
        "enabled": bool(enabled),
        "transport": "stdio",
        "command": command_parts,
    }
    env = _string_map(raw.get("env") or raw.get("environment"))
    if env:
        entry["env"] = env
    return entry


def _extract_external_servers(value: Any) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    candidates: list[tuple[str, dict[str, Any]]] = []

    def add_candidate(name: str, item: Any) -> None:
        if isinstance(item, dict):
            enriched = dict(item)
            enriched.setdefault("name", name)
            candidates.append((name, enriched))

    if isinstance(value, list):
        for idx, item in enumerate(value, 1):
            add_candidate(f"mcp_server_{idx}", item)
    elif isinstance(value, dict):
        if isinstance(value.get("mcpServers"), dict):
            for key, item in value["mcpServers"].items():
                add_candidate(str(key), item)
        elif isinstance(value.get("servers"), list):
            for idx, item in enumerate(value["servers"], 1):
                add_candidate(f"mcp_server_{idx}", item)
        elif isinstance(value.get("server"), dict):
            add_candidate(str(value.get("server").get("name") or value.get("name") or "mcp_server"), value["server"])
        elif isinstance(value.get("mcp"), dict):
            add_candidate(str(value.get("name") or value.get("id") or "mcp_server"), value["mcp"])
        elif any(k in value for k in ("command", "cmd", "executable", "bin", "args", "url", "endpoint", "transport")):
            add_candidate(str(value.get("name") or value.get("id") or "mcp_server"), value)
        else:
            for key, item in value.items():
                if isinstance(item, dict) and isinstance(item.get("mcp"), dict):
                    add_candidate(str(key), {**item["mcp"], "name": item.get("name") or key})
                elif isinstance(item, dict):
                    add_candidate(str(key), item)

    servers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fallback, candidate in candidates:
        normalized = _normalize_external_server(candidate, fallback)
        if not normalized:
            warnings.append(f"Skipped {fallback}: no command or URL found")
            continue
        base = normalized["name"]
        name = base
        suffix = 2
        while name.lower() in seen:
            name = f"{base}_{suffix}"
            suffix += 1
        normalized["name"] = name
        seen.add(name.lower())
        servers.append(normalized)

    return servers, warnings


def _parse_json_from_text(text: str) -> Any:
    trimmed = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", trimmed, flags=re.IGNORECASE)
    if fence:
        trimmed = fence.group(1).strip()
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        start_candidates = [i for i in (trimmed.find("{"), trimmed.find("[")) if i >= 0]
        if not start_candidates:
            raise
        start = min(start_candidates)
        end = max(trimmed.rfind("}"), trimmed.rfind("]"))
        if end <= start:
            raise
        return json.loads(trimmed[start : end + 1])


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

    @router.post("/mcp/import")
    @route_handler("normalize MCP import")
    async def import_mcp_config(request: Request):
        raw_body = await request.body()
        text = raw_body.decode("utf-8", errors="replace").strip()
        if not text:
            return JSONResponse({"success": False, "error": "Paste MCP JSON first."}, status_code=400)
        try:
            value = _parse_json_from_text(text)
        except json.JSONDecodeError as e:
            return JSONResponse(
                {"success": False, "error": f"Could not parse JSON: {e.msg}"},
                status_code=400,
            )

        from distr.core.mcp.config import parse_config_dict

        servers, warnings = _extract_external_servers(value)
        doc = parse_config_dict({"servers": servers})
        if len(doc.servers) != len(servers):
            return JSONResponse(
                {"success": False, "error": "The pasted MCP JSON did not produce a valid server configuration."},
                status_code=400,
            )
        return JSONResponse({"success": True, "servers": servers, "warnings": warnings})
