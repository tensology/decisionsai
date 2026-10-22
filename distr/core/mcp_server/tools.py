"""Tool surface for Decisions MCP server mode.

Read-mostly native tools + relayed vendor MCP tools (``vendor__<server>__<tool>``).
Hard denylist: speech injection / direct TTS can never be exposed or relayed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

SERVER_NAME = "decisions"
VENDOR_PREFIX = "vendor__"
_DENY_RE = re.compile(r"(speak_text_directly|speech[_\-/]?inject|inject[_\-]?speech|/api/speech)", re.I)


def is_denied(name: str) -> bool:
    return bool(_DENY_RE.search(name or ""))


def _vendor_name(server: str, tool: str) -> str:
    seg = lambda s: re.sub(r"[^A-Za-z0-9_\-]", "_", s)[:64]  # noqa: E731
    return f"{VENDOR_PREFIX}{seg(server)}__{seg(tool)}"


# ---------------------------------------------------------------- native tools

def _version() -> str:
    try:
        from distr.core.paths import CORE_DIR
        from pathlib import Path

        p = Path(CORE_DIR) / "VERSION"
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except Exception:
        return ""


def _native_status(_args: dict, ctx: "RelayContext") -> dict:
    return {
        "server": SERVER_NAME,
        "version": _version(),
        "relay_vendors": ctx.relay,
        "vendor_servers_connected": ctx.connected_servers(),
    }


def _native_list_mcp_servers(_args: dict, ctx: "RelayContext") -> dict:
    """Names/transport/enabled/connected only — never commands, env or headers."""
    from distr.core.mcp.config import load_mcp_config

    doc = load_mcp_config()
    connected = set(ctx.connected_servers())
    return {
        "servers": [
            {"name": s.name, "enabled": s.enabled, "transport": s.transport,
             "connected": s.name in connected}
            for s in doc.servers
        ]
    }


def _native_list_agent_tools(_args: dict, _ctx: "RelayContext") -> dict:
    """Names of tools registered in the Decisions agent registry (names only)."""
    try:
        from distr.core.agent.tools.registry import get_tool_registry  # type: ignore

        reg = get_tool_registry()
        names = sorted({r.tool.name for r in reg.iter_records()})
    except Exception:
        names = []
    return {"tools": [n for n in names if not is_denied(n)]}


NATIVE_TOOLS: dict[str, tuple[str, dict, Callable[[dict, "RelayContext"], dict]]] = {
    "decisions_status": ("Decisions server status and connected vendor MCP servers.",
                         {"type": "object", "properties": {}}, _native_status),
    "decisions_list_mcp_servers": ("List configured vendor MCP servers (no secrets).",
                                   {"type": "object", "properties": {}}, _native_list_mcp_servers),
    "decisions_list_agent_tools": ("List tool names registered in the Decisions agent (read-only).",
                                   {"type": "object", "properties": {}}, _native_list_agent_tools),
}


# ---------------------------------------------------------------- vendor relay

class RelayContext:
    """Wraps the process MCP hub. ``hub_factory`` lets stdio mode own a hub."""

    def __init__(self, relay: bool, hub_getter: Callable[[], Any] | None = None) -> None:
        self.relay = relay
        self._hub_getter = hub_getter or _default_hub

    def hub(self):
        if not self.relay:
            return None
        try:
            return self._hub_getter()
        except Exception:
            logger.debug("MCP hub unavailable", exc_info=True)
            return None

    def connected_servers(self) -> list[str]:
        hub = self.hub()
        if hub is None:
            return []
        try:
            return sorted(hub.enabled_connected_servers())
        except Exception:
            return []

    def vendor_tools(self) -> dict[str, tuple[str, str, dict]]:
        out: dict[str, tuple[str, str, dict]] = {}
        hub = self.hub()
        if hub is None:
            return out
        for server in self.connected_servers():
            if server == SERVER_NAME or is_denied(server):
                continue
            try:
                specs = hub.list_tools(server)
            except Exception:
                logger.warning("relay list_tools failed for %r", server, exc_info=True)
                continue
            for spec in specs or []:
                tn = spec.get("name") if isinstance(spec, dict) else None
                if not isinstance(tn, str) or not tn.strip() or is_denied(tn):
                    continue
                out[_vendor_name(server, tn)] = (server, tn, spec)
        return out


def _default_hub():
    from distr.core.mcp.runtime import get_mcp_hub

    return get_mcp_hub()  # never spawns: only relays if the app already started the hub


def list_exposed_tools(ctx: RelayContext) -> list[dict]:
    tools = [
        {"name": n, "description": d, "inputSchema": s}
        for n, (d, s, _f) in NATIVE_TOOLS.items()
    ]
    for name, (server, tn, spec) in sorted(ctx.vendor_tools().items()):
        desc = spec.get("description") if isinstance(spec.get("description"), str) else ""
        schema = spec.get("inputSchema") if isinstance(spec.get("inputSchema"), dict) else {"type": "object"}
        tools.append({"name": name,
                      "description": f"[relayed from {server}] {desc or tn}"[:8000],
                      "inputSchema": schema})
    return [t for t in tools if not is_denied(t["name"])]


def call_exposed_tool(ctx: RelayContext, name: str, arguments: dict | None) -> dict:
    """Returns ``{"text": str, "is_error": bool}``."""
    args = arguments or {}
    if is_denied(name):
        return {"text": f"Tool {name!r} is not available", "is_error": True}
    if name in NATIVE_TOOLS:
        return {"text": json.dumps(NATIVE_TOOLS[name][2](args, ctx), default=str), "is_error": False}
    if name.startswith(VENDOR_PREFIX):
        target = ctx.vendor_tools().get(name)
        hub = ctx.hub()
        if target is None or hub is None:
            return {"text": f"Unknown or disconnected vendor tool {name!r}", "is_error": True}
        server, tn, _spec = target
        raw = hub.call_tool(server, tn, args)
        parts = []
        for item in (raw or {}).get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        text = "\n".join(parts) if parts else json.dumps(raw, default=str)
        return {"text": text, "is_error": bool((raw or {}).get("isError"))}
    return {"text": f"Unknown tool {name!r}", "is_error": True}


def build_server(ctx: RelayContext):
    """Low-level ``mcp`` SDK server with dynamic tool list."""
    from mcp import types
    from mcp.server.lowlevel import Server

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        specs = await asyncio.to_thread(list_exposed_tools, ctx)
        return [types.Tool(name=s["name"], description=s["description"],
                           inputSchema=s["inputSchema"]) for s in specs]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None):
        res = await asyncio.to_thread(call_exposed_tool, ctx, name, arguments)
        if res["is_error"]:
            raise ValueError(res["text"])
        return [types.TextContent(type="text", text=res["text"])]

    return server
