"""Stdio JSON-RPC session against a minimal inline MCP server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from distr.core.mcp.jsonrpc_stdio import MCPStdioRpcError, StdioJsonRpcSession


def _write_minimal_server(path: Path) -> None:
    path.write_text(
        """
import json, sys

def send(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":")) + "\\n")
    sys.stdout.flush()

while True:
    line = sys.stdin.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    mid = req.get("id")
    method = req.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2024-11-05","capabilities":{},"serverInfo":{"name":"t","version":"0"}}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc":"2.0","id":mid,"result":{"tools":[{"name":"echo","description":"e","inputSchema":{"type":"object"}}]}})
    elif method == "tools/call":
        p = req.get("params") or {}
        if p.get("name") != "echo":
            send({"jsonrpc":"2.0","id":mid,"error":{"code":-32602,"message":"unknown tool"}})
        else:
            send({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text": json.dumps(p.get("arguments") or {})}]}})
    elif mid is not None:
        send({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":"nope"}})
""",
        encoding="utf-8",
    )


def test_stdio_list_and_call_tool(tmp_path) -> None:
    srv = tmp_path / "mcp_srv.py"
    _write_minimal_server(srv)
    import sys

    sess = StdioJsonRpcSession([sys.executable, str(srv)])
    sess.start()
    try:
        tools = sess.list_tools().get("tools", [])
        assert any(t.get("name") == "echo" for t in tools)
        out = sess.call_tool("echo", {"msg": "hi"})
        assert "content" in out
    finally:
        sess.close()


def test_stdio_unknown_tool_rpc_error(tmp_path) -> None:
    srv = tmp_path / "mcp_srv.py"
    _write_minimal_server(srv)
    import sys

    sess = StdioJsonRpcSession([sys.executable, str(srv)])
    sess.start()
    try:
        with pytest.raises(MCPStdioRpcError):
            sess.call_tool("missing", {})
    finally:
        sess.close()


def test_stdio_killed_process_breaks_rpc(tmp_path) -> None:
    """Simulate MCP server crash: RPC fails without raising bare AssertionError."""
    srv = tmp_path / "mcp_srv.py"
    _write_minimal_server(srv)
    import sys

    sess = StdioJsonRpcSession([sys.executable, str(srv)])
    sess.start()
    try:
        assert sess._proc is not None
        sess._proc.kill()
        sess._proc.wait(timeout=10)
        with pytest.raises((BrokenPipeError, OSError)):
            sess.list_tools()
    finally:
        sess.close()
