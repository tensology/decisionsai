"""MCP client, config, adapter (R6), and optional SDK-backed remote transport (R5)."""

from distr.core.mcp.adapter import (
    MCPNativeToolCollisionError,
    MCP_TOOL_REGISTRATION_CAP,
    MCPToolAdapter,
    TOOL_CALL_TIMEOUT_SEC,
    prefixed_tool_registry_name,
)
from distr.core.mcp.client import (
    MCPClientHub,
    MCPTransportError,
    MCPServerSession,
    RECONNECT_INTERVAL_SEC,
    RECONNECT_MAX_ATTEMPTS,
)
from distr.core.mcp.config import (
    MCPConfigDocument,
    MCPConfigWatcher,
    MCPServerConfig,
    load_mcp_config,
    parse_config_dict,
    save_mcp_config,
    server_names_to_reconnect,
)
from distr.core.mcp.jsonrpc_stdio import MCPStdioRpcError, StdioJsonRpcSession
from distr.core.mcp.runtime import (
    get_mcp_hub,
    init_mcp_stack,
    reset_mcp_runtime_for_tests,
    tick_mcp_reconnect,
)
from distr.core.mcp.streamable_sdk import StreamableSdkSession, mcp_sdk_available

__all__ = [
    "MCPNativeToolCollisionError",
    "MCP_TOOL_REGISTRATION_CAP",
    "MCPToolAdapter",
    "MCPClientHub",
    "MCPConfigDocument",
    "MCPConfigWatcher",
    "MCPStdioRpcError",
    "MCPServerConfig",
    "MCPServerSession",
    "MCPTransportError",
    "TOOL_CALL_TIMEOUT_SEC",
    "RECONNECT_INTERVAL_SEC",
    "RECONNECT_MAX_ATTEMPTS",
    "StdioJsonRpcSession",
    "StreamableSdkSession",
    "get_mcp_hub",
    "init_mcp_stack",
    "load_mcp_config",
    "mcp_sdk_available",
    "prefixed_tool_registry_name",
    "parse_config_dict",
    "reset_mcp_runtime_for_tests",
    "save_mcp_config",
    "server_names_to_reconnect",
    "tick_mcp_reconnect",
]
