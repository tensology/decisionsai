"""stdio entry point: ``python -m distr.core.mcp_server``.

Refuses to start unless MCP server mode is enabled (setting or env). stdio is
local by construction (the client spawns us), so no bearer token is needed.
With relay on, this process starts its own vendor hub (spawns configured
vendor servers) — set ``mcp_server_relay_vendors`` false to avoid that.
"""

from __future__ import annotations

import sys


def main() -> int:
    from distr.core.mcp_server.config import is_enabled, relay_vendors_enabled

    if not is_enabled():
        print("Decisions MCP server mode is disabled (setting mcp_server_enabled).", file=sys.stderr)
        return 2
    import anyio
    from mcp.server.stdio import stdio_server

    from distr.core.mcp_server.tools import RelayContext, build_server

    import os

    relay = relay_vendors_enabled() and os.environ.get("DECISIONS_MCP_SERVER_RELAY", "1") != "0"

    def _hub():
        from distr.core.mcp.runtime import get_mcp_hub, init_mcp_stack

        if get_mcp_hub() is None:
            init_mcp_stack()
        return get_mcp_hub()

    server = build_server(RelayContext(relay=relay, hub_getter=_hub))

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
