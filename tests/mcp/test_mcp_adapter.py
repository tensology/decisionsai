"""Tests for MCP → ToolRegistry adapter (R6)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import BaseTool

from distr.core.agent.tools.registry import get_tool_registry, reset_tool_registry_for_tests
from distr.core.events import EventBus, MCP_TOOL_CALLED, reset_event_bus_for_tests
from distr.core.mcp.adapter import (
    MCPNativeToolCollisionError,
    MCP_TOOL_REGISTRATION_CAP,
    MCPToolAdapter,
    prefixed_tool_registry_name,
)
from distr.core.mcp.runtime import get_mcp_hub, init_mcp_stack, reset_mcp_runtime_for_tests


@pytest.fixture(autouse=True)
def _isolate_registry_bus_runtime():
    reset_mcp_runtime_for_tests()
    reset_tool_registry_for_tests()
    reset_event_bus_for_tests()
    yield
    reset_mcp_runtime_for_tests()
    reset_tool_registry_for_tests()


def test_prefixed_tool_registry_name() -> None:
    assert prefixed_tool_registry_name("my_srv", "do_it") == "mcp__my_srv__do_it"


def test_reconcile_registers_and_invoke_calls_hub() -> None:
    hub = MagicMock()
    hub.enabled_connected_servers.return_value = ["srv"]
    hub.list_tools.return_value = [
        {
            "name": "echo",
            "description": "echo tool",
            "inputSchema": {
                "type": "object",
                "properties": {"msg": {"type": "string"}},
            },
        }
    ]
    hub.call_tool.return_value = {"content": [{"type": "text", "text": "ok"}]}
    reg = get_tool_registry()
    adapter = MCPToolAdapter(hub=hub, registry=reg, bus=None)
    assert adapter.reconcile() == 1
    reg_name = prefixed_tool_registry_name("srv", "echo")
    tool = reg.get_by_name(reg_name)
    assert tool is not None
    out = tool.invoke({"msg": "hello"})
    assert "ok" in out
    hub.call_tool.assert_called_once()
    assert hub.call_tool.call_args[0][0] == "srv"
    assert hub.call_tool.call_args[0][1] == "echo"
    assert hub.call_tool.call_args[0][2] == {"msg": "hello"}


def test_native_name_collision_raises() -> None:
    reg_name = prefixed_tool_registry_name("srv", "echo")

    class _Blocks(BaseTool):
        name: str = reg_name
        description: str = "native"
        def _run(self, **kwargs):  # noqa: ANN003
            return ""

    reg = get_tool_registry()
    reg.register(_Blocks(), "native")
    hub = MagicMock()
    hub.enabled_connected_servers.return_value = ["srv"]
    hub.list_tools.return_value = [
        {"name": "echo", "description": "x", "inputSchema": {"type": "object"}},
    ]
    adapter = MCPToolAdapter(hub=hub, registry=reg, bus=None)
    with pytest.raises(MCPNativeToolCollisionError):
        adapter.reconcile()


def test_tool_cap_truncates() -> None:
    hub = MagicMock()
    hub.enabled_connected_servers.return_value = ["srv"]
    hub.list_tools.return_value = [
        {"name": f"t{i}", "description": "", "inputSchema": {"type": "object"}}
        for i in range(MCP_TOOL_REGISTRATION_CAP + 5)
    ]
    reg = get_tool_registry()
    adapter = MCPToolAdapter(hub=hub, registry=reg, bus=None)
    adapter.reconcile()
    mcp_recs = [r for r in reg.iter_records() if r.source.startswith("mcp:")]
    assert len(mcp_recs) == MCP_TOOL_REGISTRATION_CAP


def test_tool_call_timeout_returns_message() -> None:
    hub = MagicMock()
    hub.enabled_connected_servers.return_value = ["srv"]
    hub.list_tools.return_value = [
        {"name": "slow", "description": "", "inputSchema": {"type": "object"}},
    ]

    def _slow(*_a, **_kw):
        time.sleep(2.0)
        return {}

    hub.call_tool.side_effect = _slow
    reg = get_tool_registry()
    adapter = MCPToolAdapter(hub=hub, registry=reg, bus=None)
    with patch("distr.core.mcp.adapter.TOOL_CALL_TIMEOUT_SEC", 0.15):
        adapter.reconcile()
    tool = reg.get_by_name(prefixed_tool_registry_name("srv", "slow"))
    assert tool is not None
    msg = tool.invoke({})
    assert "timed out" in msg.lower()


def test_mcp_tool_called_event_payload() -> None:
    hub = MagicMock()
    hub.enabled_connected_servers.return_value = ["srv"]
    hub.list_tools.return_value = [
        {
            "name": "echo",
            "description": "d",
            "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}},
        }
    ]
    hub.call_tool.return_value = {"content": [{"type": "text", "text": "y"}]}
    bus = EventBus()
    seen: list[dict] = []

    def cap(_et: str, data: object) -> None:
        if isinstance(data, dict):
            seen.append(data)

    bus.subscribe(MCP_TOOL_CALLED, cap)
    reg = get_tool_registry()
    adapter = MCPToolAdapter(hub=hub, registry=reg, bus=bus)
    adapter.reconcile()
    tool = reg.get_by_name(prefixed_tool_registry_name("srv", "echo"))
    assert tool is not None
    tool.invoke({"msg": "z"})
    assert seen and seen[0].get("server") == "srv"
    assert seen[0].get("tool") == "echo"
    assert len(seen[0].get("args_sha256", "")) == 64


def test_init_mcp_stack_empty_config(tmp_path) -> None:
    p = tmp_path / "mcp_config.json"
    p.write_text('{"servers": []}', encoding="utf-8")
    init_mcp_stack(config_path=p)
    try:
        assert get_mcp_hub() is not None
    finally:
        reset_mcp_runtime_for_tests()
