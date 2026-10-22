"""MCP hub dedupe, /api/mcp masking + local commands, server-mode settings API.

Requires DECISIONS_DB_DIR, DECISIONS_MODELS_DIR and HOME under /tmp (never the real DB/config).
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

for _v in ("DECISIONS_DB_DIR", "DECISIONS_MODELS_DIR", "HOME"):
    if not str(os.environ.get(_v, "")).startswith(("/tmp", "/private/tmp")):
        pytest.skip(f"{_v} must be under /tmp", allow_module_level=True)

from fastapi import APIRouter, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from distr.core.mcp import runtime  # noqa: E402
from distr.core.mcp.config import default_config_path  # noqa: E402

CFG = {"servers": [
    {"name": "alpha", "enabled": True, "transport": "stdio",
     "command": ["/bin/echo", "hello world", "--flag"], "env": {"SECRET_KEY": "s3cr3t-value"}},
    {"name": "remote1", "enabled": True, "transport": "sse", "url": "https://example.invalid/mcp",
     "headers": {"Authorization": "Bearer hdr-secret"}},
]}


@pytest.fixture(autouse=True)
def _cfg(tmp_path, monkeypatch):
    path = default_config_path()
    assert str(path).startswith(("/tmp", "/private/tmp"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(CFG))
    monkeypatch.setenv("DECISIONS_MCP_SERVER_TOKEN_FILE", str(tmp_path / "tok"))
    runtime.reset_mcp_runtime_for_tests()
    yield path
    runtime.reset_mcp_runtime_for_tests()


# ------------------------------------------------------------------ dedupe

class CountingHub:
    instances = 0

    def __init__(self, bus=None, config_path=None):
        type(self).instances += 1

    def load_and_apply(self):
        time.sleep(0.2)  # widen the old race window

    def enabled_connected_servers(self):
        return []

    def disconnect_all(self):
        pass


def test_concurrent_init_creates_one_hub(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISIONS_MCP_HUB_MODE", "local")
    CountingHub.instances = 0
    monkeypatch.setattr(runtime, "MCPClientHub", CountingHub)
    cfg = tmp_path / "mcp.json"
    cfg.write_text('{"servers": []}')
    ts = [threading.Thread(target=runtime.init_mcp_stack, args=(cfg,)) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert CountingHub.instances == 1


def test_worker_mode_uses_remote_hub_and_spawns_nothing(monkeypatch):
    from distr.core.mcp import remote_hub

    monkeypatch.setenv("DECISIONS_MCP_HUB_MODE", "remote")
    CountingHub.instances = 0
    monkeypatch.setattr(runtime, "MCPClientHub", CountingHub)
    monkeypatch.setattr(remote_hub, "_default_transport", lambda *a, **k: {"servers": []})
    monkeypatch.setattr(remote_hub, "POLL_INTERVAL_SEC", 3600)
    runtime.init_mcp_stack()
    assert CountingHub.instances == 0
    assert isinstance(runtime.get_mcp_hub(), remote_hub.RemoteMCPHub)


def test_auto_mode_detects_spawned_worker(monkeypatch):
    import multiprocessing

    monkeypatch.delenv("DECISIONS_MCP_HUB_MODE", raising=False)
    assert runtime._hub_mode() == "local"
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())
    assert runtime._hub_mode() == "remote"


def test_remote_hub_relays_and_poller_reconciles():
    from distr.core.mcp.client import MCPTransportError
    from distr.core.mcp.remote_hub import RemoteHubPoller, RemoteMCPHub

    state = {"servers": [{"name": "laya", "tools": [{"name": "laya_preset"}]}]}
    calls = []

    def transport(method, path, body, timeout):
        calls.append((method, path))
        if method == "GET":
            return state
        if body["server"] == "laya":
            return {"ok": True, "result": {"content": [{"type": "text", "text": "hi"}]}}
        return {"ok": False, "error": "unknown"}

    hub = RemoteMCPHub(transport)
    assert hub.enabled_connected_servers() == ["laya"] and hub.is_connected("laya")
    assert hub.list_tools("laya")[0]["name"] == "laya_preset"
    assert hub.call_tool("laya", "laya_preset", {})["content"][0]["text"] == "hi"
    with pytest.raises(MCPTransportError):
        hub.call_tool("nope", "x", {})

    class Ad:
        n = 0

        def reconcile(self):
            Ad.n += 1

    p = RemoteHubPoller(hub, Ad())
    assert p.poll_once() and not p.poll_once()
    state["servers"].append({"name": "headroom", "tools": [{"name": "h"}]})
    assert p.poll_once() and Ad.n == 2


def test_owner_hub_state_route(monkeypatch):
    from distr.gui.web.routes import mcp_hub_internal as m

    class H:
        def enabled_connected_servers(self):
            return ["a"]

        def list_tools(self, n):
            return [{"name": "t"}]

        def call_tool(self, s, t, a):
            return {"content": [], "echo": [s, t, a]}

    monkeypatch.setattr(m, "_owner_hub", lambda: H())
    app = FastAPI()
    app.include_router(m.router)
    c = TestClient(app)
    assert c.get("/api/internal/mcp/hub/state").json() == {"servers": [{"name": "a", "tools": [{"name": "t"}]}]}
    r = c.post("/api/internal/mcp/hub/call", json={"server": "a", "tool": "t", "arguments": {"x": 1}}).json()
    assert r["ok"] and r["result"]["echo"] == ["a", "t", {"x": 1}]


# ------------------------------------------------------------------ /api/mcp

def _settings_client():
    from distr.gui.web.routes.settings import mcp

    router = APIRouter()
    mcp.register_routes(router, None)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app), mcp


def test_get_masks_env_and_headers_and_reveal_works():
    c, _ = _settings_client()
    body = c.get("/api/mcp").text
    assert "s3cr3t-value" not in body and "hdr-secret" not in body and "SECRET_KEY" in body
    assert "s3cr3t-value" in c.get("/api/mcp?reveal_env=1").text


def test_post_masked_roundtrip_keeps_stored_secret(_cfg):
    c, _ = _settings_client()
    data = c.get("/api/mcp").json()
    data["servers"][0]["command"] = ["/bin/echo", "changed"]
    assert c.post("/api/mcp", json=data).json()["success"]
    saved = json.loads(_cfg.read_text())
    s0 = next(s for s in saved["servers"] if s["name"] == "alpha")
    assert s0["env"]["SECRET_KEY"] == "s3cr3t-value" and s0["command"][-1] == "changed"
    r1 = next(s for s in saved["servers"] if s["name"] == "remote1")
    assert r1["headers"]["Authorization"] == "Bearer hdr-secret"


def test_local_commands_lists_stdio_only_without_env():
    c, _ = _settings_client()
    rows = c.get("/api/mcp/local-commands").json()["servers"]
    assert rows == [{"name": "alpha", "enabled": True, "command": ["/bin/echo", "hello world", "--flag"]}]
    assert "s3cr3t" not in json.dumps(rows) and "SECRET_KEY" not in json.dumps(rows)


def test_delete_server_on_tmp_copy(_cfg):
    c, _ = _settings_client()
    assert c.delete("/api/mcp/servers/nope").status_code == 404
    assert c.delete("/api/mcp/servers/alpha").json()["deleted"] == "alpha"
    assert [s["name"] for s in json.loads(_cfg.read_text())["servers"]] == ["remote1"]


def test_middleware_gates_mcp_reads():
    src = open(os.path.join(os.path.dirname(__file__), "..", "distr", "gui", "web", "server.py")).read()
    assert 'is_mcp_path = path == "/api/mcp" or path.startswith("/api/mcp/")' in src
    assert "path in sensitive_settings_reads or is_mcp_path" in src


# ------------------------------------------------------------------ server-mode settings

def test_server_mode_settings_api(monkeypatch):
    from distr.core.mcp_server import config as cfg
    from distr.gui.web.routes import mcp_server_settings as m

    monkeypatch.delenv(cfg.ENV_ENABLED, raising=False)
    monkeypatch.delenv(cfg.ENV_TOKEN, raising=False)
    app = FastAPI()
    app.include_router(m.router)
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    st = c.get("/api/mcp/server-mode").json()
    tok = c.get("/api/mcp/server-mode/token").json()["token"]
    assert st["url"] == "http://127.0.0.1:8765/mcp/" and tok not in json.dumps(st)
    assert st["token_masked"].endswith(tok[-4:])
    r = c.post("/api/mcp/server-mode", json={"enabled": True}).json()
    assert r["enabled"] is True and r["restart_required"] is True
    assert c.post("/api/mcp/server-mode", json={"enabled": False}).json()["enabled"] is False
    c.post("/api/mcp/server-mode/token/regenerate")
    assert c.get("/api/mcp/server-mode/token").json()["token"] != tok
