"""Decisions MCP server mode — runs only against a /tmp DB copy and /tmp HOME."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

if not str(os.environ.get("DECISIONS_DB_DIR", "")).startswith(("/tmp", "/private/tmp")):
    pytest.skip("set DECISIONS_DB_DIR and HOME under /tmp to run", allow_module_level=True)

from distr.core.mcp_server import config as cfg  # noqa: E402
from distr.core.mcp_server.tools import (  # noqa: E402
    RelayContext, call_exposed_tool, is_denied, list_exposed_tools,
)


class FakeHub:
    def enabled_connected_servers(self):
        return ["laya", "evil"]

    def list_tools(self, server):
        if server == "evil":
            return [{"name": "speak_text_directly"}, {"name": "speech_inject"}, {"name": "ok_tool"}]
        return [{"name": "laya_preset", "description": "preset", "inputSchema": {"type": "object"}}]

    def call_tool(self, server, tool, args):
        return {"content": [{"type": "text", "text": f"{server}:{tool}:{json.dumps(args)}"}]}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv(cfg.ENV_TOKEN_FILE, str(tmp_path / "tok"))
    monkeypatch.delenv(cfg.ENV_TOKEN, raising=False)
    monkeypatch.delenv(cfg.ENV_ENABLED, raising=False)


def test_off_by_default():
    assert cfg.is_enabled({}) is False


def test_token_file_private_and_stable():
    t = cfg.get_or_create_token()
    assert len(t) >= 32 and cfg.get_or_create_token() == t
    assert oct(cfg.token_path().stat().st_mode & 0o777) == "0o600"
    assert cfg.token_matches(t) and not cfg.token_matches("nope") and not cfg.token_matches("")


def test_denylist_and_namespacing():
    ctx = RelayContext(relay=True, hub_getter=FakeHub)
    names = {t["name"] for t in list_exposed_tools(ctx)}
    assert "decisions_status" in names
    assert "vendor__laya__laya_preset" in names and "vendor__evil__ok_tool" in names
    assert not any(is_denied(n) for n in names)
    assert call_exposed_tool(ctx, "vendor__evil__speak_text_directly", {})["is_error"]
    r = call_exposed_tool(ctx, "vendor__laya__laya_preset", {"a": 1})
    assert r == {"text": 'laya:laya_preset:{"a": 1}', "is_error": False}


def test_relay_off_exposes_native_only():
    ctx = RelayContext(relay=False, hub_getter=FakeHub)
    assert all(not t["name"].startswith("vendor__") for t in list_exposed_tools(ctx))


def test_not_mounted_when_disabled():
    from fastapi import FastAPI
    from distr.gui.web.routes.mcp_server_http import maybe_mount_mcp_server

    os.environ[cfg.ENV_ENABLED] = "0"
    try:
        assert maybe_mount_mcp_server(FastAPI()) is False
    finally:
        os.environ.pop(cfg.ENV_ENABLED, None)


def test_http_transport_auth_and_tools(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from distr.gui.web.routes import mcp_server_http as m

    monkeypatch.setenv(cfg.ENV_ENABLED, "1")
    app = FastAPI()
    app.mount("/mcp", m.MCPServerASGI(RelayContext(relay=True, hub_getter=FakeHub)))
    tok = cfg.get_or_create_token()
    hdr = {"accept": "application/json, text/event-stream", "content-type": "application/json"}

    async def run():
        tr = httpx.ASGITransport(app=app, client=("127.0.0.1", 5555))
        async with httpx.AsyncClient(transport=tr, base_url="http://127.0.0.1:8765") as c:
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            assert (await c.post("/mcp/", json=body, headers=hdr)).status_code == 401
            bad = {**hdr, "authorization": "Bearer wrong"}
            assert (await c.post("/mcp/", json=body, headers=bad)).status_code == 401
            ok = {**hdr, "authorization": f"Bearer {tok}"}
            init = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"}}}
            r0 = await c.post("/mcp/", json=init, headers=ok)
            assert r0.status_code == 200, r0.text
            r = await c.post("/mcp/", json=body, headers=ok)
            assert r.status_code == 200, r.text
            names = {t["name"] for t in r.json()["result"]["tools"]}
            assert "vendor__laya__laya_preset" in names and "speak_text_directly" not in str(names)
            call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "decisions_status", "arguments": {}}}
            r2 = await c.post("/mcp/", json=call, headers=ok)
            assert '"server": "decisions"' in r2.json()["result"]["content"][0]["text"]
        tr2 = httpx.ASGITransport(app=app, client=("10.0.0.5", 5555))
        async with httpx.AsyncClient(transport=tr2, base_url="http://127.0.0.1:8765") as c:
            assert (await c.post("/mcp/", json=body, headers=ok)).status_code == 403

    asyncio.run(run())


def test_stdio_entrypoint_refuses_when_disabled():
    import subprocess

    env = {**os.environ, cfg.ENV_ENABLED: "0"}
    p = subprocess.run([sys.executable, "-m", "distr.core.mcp_server"], env=env,
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert p.returncode == 2


def test_stdio_entrypoint_lists_tools():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, cfg.ENV_ENABLED: "1"}
    # relay off in stdio test so no vendor servers are spawned
    env["DECISIONS_MCP_SERVER_RELAY"] = "0"

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "distr.core.mcp_server"],
                                       env=env, cwd=str(Path(__file__).resolve().parents[1]))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                assert {"decisions_status", "decisions_list_mcp_servers"} <= names
                assert not any(n.startswith("vendor__") for n in names)

    asyncio.run(asyncio.wait_for(run(), 90))
