"""DecisionsAI-owned standalone Headroom MCP entrypoint.

The upstream CLI probes an optional provider proxy on every compression. The
DecisionsAI default is deliberately local and on-demand, so this entrypoint
disables that probe while retaining Headroom's in-process store and retrieval.
"""

from __future__ import annotations

import asyncio
import logging


async def serve() -> None:
    from headroom.ccr.mcp_server import HeadroomMCPServer

    server = HeadroomMCPServer(check_proxy=False)
    try:
        await server.run_stdio()
    finally:
        await server.cleanup()


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
