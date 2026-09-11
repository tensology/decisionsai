from __future__ import annotations

import json
import os
import sys


def test_headroom_standalone_mcp_compresses_and_retrieves_locally(tmp_path):
    from distr.core.mcp.jsonrpc_stdio import StdioJsonRpcSession

    env = os.environ.copy()
    env.update({
        "HEADROOM_MCP_READ": "on",
        "HEADROOM_WORKSPACE_DIR": str(tmp_path / "headroom"),
    })
    session = StdioJsonRpcSession(
        [sys.executable, "-m", "distr.core.headroom_mcp"],
        env=env,
    )
    session.start()
    try:
        names = {item["name"] for item in session.list_tools()["tools"]}
        assert names >= {
            "headroom_compress",
            "headroom_retrieve",
            "headroom_stats",
            "headroom_read",
        }
        original = "workflow route evidence\n" * 200
        compressed_raw = session.call_tool("headroom_compress", {"content": original})
        compressed = json.loads(compressed_raw["content"][0]["text"])
        assert compressed["hash"]
        assert "proxy" not in compressed

        retrieved_raw = session.call_tool(
            "headroom_retrieve",
            {"hash": compressed["hash"]},
        )
        retrieved = json.loads(retrieved_raw["content"][0]["text"])
        assert retrieved["source"] == "local"
        assert retrieved["original_content"] == original
    finally:
        session.close()


def test_decisions_mcp_hub_registers_headroom_as_native_agent_tools(tmp_path):
    from distr.core.agent.tools.registry import ToolRegistry
    from distr.core.mcp.adapter import MCPToolAdapter
    from distr.core.mcp.client import MCPClientHub
    from distr.core.mcp.config import MCPConfigDocument, MCPServerConfig, save_mcp_config

    config_path = tmp_path / "mcp_config.json"
    save_mcp_config(
        MCPConfigDocument(servers=(MCPServerConfig(
            name="headroom",
            enabled=True,
            command=(sys.executable, "-m", "distr.core.headroom_mcp"),
            env=frozenset({
                ("HEADROOM_MCP_READ", "on"),
                ("HEADROOM_WORKSPACE_DIR", str(tmp_path / "headroom")),
            }),
        ),)),
        config_path,
    )
    hub = MCPClientHub(config_path=config_path)
    registry = ToolRegistry()
    adapter = MCPToolAdapter(hub=hub, registry=registry)
    try:
        hub.load_and_apply()
        assert hub.enabled_connected_servers() == ["headroom"]
        assert adapter.reconcile() == 4
        tool = registry.get_by_name("mcp__headroom__headroom_compress")
        assert tool is not None
        result = tool.invoke({"content": "orchestrated workflow evidence\n" * 100})
        assert "Original stored with hash=" in result
    finally:
        hub.disconnect_all()
