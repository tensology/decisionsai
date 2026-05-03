"""Tests for MCP config load/save, validation, and hot-reload diff helpers."""

from __future__ import annotations

import json

import pytest

from distr.core.events.bus import EventBus
from distr.core.events.types import MCP_SERVER_CONNECTED, MCP_SERVER_DISCONNECTED
from distr.core.mcp.config import (
    MCPConfigDocument,
    MCPConfigWatcher,
    MCPServerConfig,
    load_mcp_config,
    parse_config_dict,
    save_mcp_config,
    server_names_to_reconnect,
)
from distr.core.mcp.client import MCPClientHub


def test_load_malformed_json_returns_empty(tmp_path) -> None:
    p = tmp_path / "mcp_config.json"
    p.write_text("{not json", encoding="utf-8")
    doc = load_mcp_config(p)
    assert doc.servers == ()


def test_duplicate_server_names_skipped(tmp_path, caplog) -> None:
    p = tmp_path / "mcp_config.json"
    p.write_text(
        json.dumps(
            {
                "servers": [
                    {"name": "a", "transport": "stdio", "command": ["true"]},
                    {"name": "a", "transport": "stdio", "command": ["false"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    doc = load_mcp_config(p)
    assert len(doc.servers) == 1
    assert doc.servers[0].command == ("true",)


def test_save_rejects_duplicate_programmatic_names(tmp_path) -> None:
    dup = MCPServerConfig(
        name="x",
        enabled=True,
        transport="stdio",
        command=("true",),
    )
    with pytest.raises(ValueError, match="duplicate"):
        save_mcp_config(MCPConfigDocument(servers=(dup, dup)), tmp_path / "c.json")


def test_save_roundtrip_atomic(tmp_path) -> None:
    p = tmp_path / "mcp_config.json"
    doc = MCPConfigDocument(
        servers=(
            MCPServerConfig(
                name="s1",
                enabled=True,
                transport="stdio",
                command=("python", "-m", "noop"),
                env=frozenset({("K", "v")}),
            ),
        )
    )
    save_mcp_config(doc, p)
    got = load_mcp_config(p)
    assert got.servers[0].name == "s1"
    assert got.servers[0].env == frozenset({("K", "v")})


def test_reorder_servers_no_reconnect() -> None:
    a = MCPServerConfig(
        name="a",
        transport="stdio",
        command=("echo", "a"),
        enabled=True,
    )
    b = MCPServerConfig(
        name="b",
        transport="stdio",
        command=("echo", "b"),
        enabled=True,
    )
    prev = MCPConfigDocument(servers=(a, b))
    cur = MCPConfigDocument(servers=(b, a))
    removed, changed, _ = server_names_to_reconnect(prev, cur)
    assert removed == set()
    assert changed == set()


def test_command_change_triggers_reconnect() -> None:
    prev = MCPConfigDocument(
        servers=(
            MCPServerConfig(
                name="a",
                transport="stdio",
                command=("old",),
                enabled=True,
            ),
        )
    )
    cur = MCPConfigDocument(
        servers=(
            MCPServerConfig(
                name="a",
                transport="stdio",
                command=("new",),
                enabled=True,
            ),
        )
    )
    removed, changed, _ = server_names_to_reconnect(prev, cur)
    assert changed == {"a"}
    assert removed == set()


def test_parse_config_sse_requires_url() -> None:
    doc = parse_config_dict(
        {"servers": [{"name": "r", "transport": "sse", "enabled": True}]}
    )
    assert doc.servers == ()


def test_watcher_invokes_callback(tmp_path) -> None:
    p = tmp_path / "mcp_config.json"
    p.write_text('{"servers": []}', encoding="utf-8")
    hits: list[int] = []

    def cb():
        hits.append(1)

    w = MCPConfigWatcher(path=p, on_change=cb, poll_interval=0.05)
    w.start()
    try:
        import time

        time.sleep(0.12)
        save_mcp_config(
            MCPConfigDocument(
                servers=(
                    MCPServerConfig(
                        name="x",
                        transport="stdio",
                        command=("true",),
                        enabled=True,
                    ),
                )
            ),
            p,
        )
        time.sleep(0.15)
        assert len(hits) >= 1
    finally:
        w.stop()


def test_hub_publishes_bus_events(tmp_path) -> None:
    bus = EventBus()
    log: list[tuple[str, object]] = []

    def cap(et: str, data: object) -> None:
        log.append((et, data))

    bus.subscribe(MCP_SERVER_CONNECTED, cap)
    bus.subscribe(MCP_SERVER_DISCONNECTED, cap)

    srv = tmp_path / "srv.py"
    srv.write_text(
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
        send({"jsonrpc":"2.0","id":mid,"result":{"tools":[]}})
    elif method == "tools/call":
        send({"jsonrpc":"2.0","id":mid,"result":{"content":[]}})
""",
        encoding="utf-8",
    )

    cfg_path = tmp_path / "mcp_config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "local",
                        "enabled": True,
                        "transport": "stdio",
                        "command": [__import__("sys").executable, str(srv)],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    hub = MCPClientHub(bus=bus, config_path=cfg_path)
    hub.load_and_apply()
    types_connected = [x[0] for x in log]
    assert MCP_SERVER_CONNECTED in types_connected
    hub.disconnect_all()
    types_dc = [x[0] for x in log]
    assert MCP_SERVER_DISCONNECTED in types_dc
