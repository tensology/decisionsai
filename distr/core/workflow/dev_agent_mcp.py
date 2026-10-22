"""Per-run stdio MCP bridge exposing the Development-agent tools to Claude Code,
Codex and Cursor (Pi uses pi_extensions/dev_media_tools.ts instead).

Spawned by the harness CLI only for development turns, from config injected per
run by project_cli_backends/registry.py. stdio => no port, no bearer token.
Tools come from dev_agent_tools.exposed_tools() (TOOL_SCHEMAS/DISPATCH minus
hidden + MCP deny-list), so new tools appear automatically.

Refuses to serve tools unless DECISIONS_DEV_AGENT_TOOLS=1 (set only in the
per-run config), so a stray copy of the config elsewhere exposes nothing.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from typing import Any

SERVER_NAME = "decisions-dev"
ENV_GATE = "DECISIONS_DEV_AGENT_TOOLS"
MAX_INLINE_IMAGE = 8 * 1024 * 1024


def enabled() -> bool:
    return os.environ.get(ENV_GATE, "") == "1"


def list_specs() -> list[dict[str, Any]]:
    if not enabled():
        return []
    from distr.core.workflow.dev_agent_tools import exposed_tools

    return [{"name": n, "description": s.get("description", n), "inputSchema": s["parameters"]}
            for n, s in exposed_tools().items()]


def run_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    from distr.core.workflow.dev_agent_tools import call_tool, exposed_tools

    if not enabled() or name not in exposed_tools():
        return {"ok": False, "tool": name, "summary": f"Tool {name!r} is not available", "artifacts": []}
    folder = os.environ.get("DECISIONS_PROJECT_FOLDER") or os.getcwd()
    return call_tool(name, arguments or {}, project_folder=folder)


def build_server():
    from mcp import types
    from mcp.server.lowlevel import Server

    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        specs = await asyncio.to_thread(list_specs)
        return [types.Tool(name=s["name"], description=s["description"], inputSchema=s["inputSchema"]) for s in specs]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None):
        res = await asyncio.to_thread(run_tool, name, arguments)
        out: list[Any] = [types.TextContent(type="text", text=json.dumps(res, default=str))]
        for a in res.get("artifacts") or []:
            try:
                if a.get("kind") == "image" and os.path.getsize(a["path"]) < MAX_INLINE_IMAGE:
                    with open(a["path"], "rb") as fh:
                        out.append(types.ImageContent(type="image", data=base64.b64encode(fh.read()).decode(),
                                                      mimeType=a.get("mime") or "image/png"))
            except Exception:
                pass
        return out

    return server


def main() -> int:
    if not enabled():
        print("decisions-dev MCP bridge: disabled outside a development run", file=sys.stderr)
    import anyio
    from mcp.server.stdio import stdio_server

    server = build_server()

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
