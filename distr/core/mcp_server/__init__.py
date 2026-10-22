"""Decisions-as-an-MCP-server (OFF by default).

External MCP clients connect over stdio (``python -m distr.core.mcp_server``) or
streamable HTTP at ``/mcp`` on the local web app (only mounted when enabled).
"""

from distr.core.mcp_server.config import (
    is_enabled,
    relay_vendors_enabled,
    get_or_create_token,
    token_matches,
)
from distr.core.mcp_server.tools import build_server, list_exposed_tools

__all__ = [
    "build_server",
    "get_or_create_token",
    "is_enabled",
    "list_exposed_tools",
    "relay_vendors_enabled",
    "token_matches",
]
