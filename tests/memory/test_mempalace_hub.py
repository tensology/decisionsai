"""Hub-first MemPalace adapter: never touches Chroma while a hub is up/configured."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class _FakeHub(BaseHTTPRequestHandler):
    calls: list = []
    token = "t-secret"

    def log_message(self, *a):  # noqa: D401
        pass

    def do_POST(self):  # noqa: N802
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self.send_response(401)
            self.end_headers()
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        name = body["params"]["name"]
        args = body["params"]["arguments"]
        type(self).calls.append((name, args))
        if name == "mempalace_search":
            payload = {"query": args["query"], "results": [{"text": "hub hit", "wing": "decisions", "room": "r"}]}
        else:
            payload = {"success": True, "drawer_id": "drawer_x", "wing": args["wing"], "room": args["room"]}
        out = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DECISIONS_MEMPALACE_HUB_URL", raising=False)
    p = tmp_path / ".decisions" / "mempalace" / "palace"
    p.mkdir(parents=True)
    return p


def _no_chroma(monkeypatch):
    from distr.core.mempalace import adapter as mod

    def boom(*a, **k):
        raise AssertionError("adapter opened Chroma directly while hub configured")

    monkeypatch.setattr(mod.MemPalaceAdapter, "_get_collection", boom)


def _register(palace, port, pid=None):
    from distr.core.mempalace import hub

    state = hub.server_state_dir(str(palace))
    state.mkdir(parents=True, exist_ok=True)
    (state / "token").write_text(_FakeHub.token + "\n")
    (state / "serverinfo.json").write_text(json.dumps({"pid": pid or os.getpid(), "host": "127.0.0.1", "port": port, "scheme": "http"}))
    return state


def test_hub_search_and_write_go_over_http(palace, monkeypatch):
    from distr.core.mempalace.adapter import MemPalaceAdapter

    srv = HTTPServer(("127.0.0.1", 0), _FakeHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _FakeHub.calls = []
        _register(palace, srv.server_address[1])
        _no_chroma(monkeypatch)
        a = MemPalaceAdapter(str(palace))
        st = a.status()
        assert st["backend"] == "mempalace-hub" and st["hub"]["up"] is True
        assert "t-secret" not in json.dumps(st)
        res = a.search("anything", wing="decisions", n_results=3)
        assert res["via"] == "hub" and res["results"][0]["text"] == "hub hit"
        assert "hub hit" in a.format_search_as_context("anything")
        w = a.add_drawer("hello", room="r", extra_meta={"k": "v"})
        assert w["success"] and w["via"] == "hub"
        name, args = _FakeHub.calls[-1]
        assert name == "mempalace_add_drawer" and args["source_file"] == "decisions://k=v"
        assert args["added_by"] == "mac:decisions:decisionsai"
    finally:
        srv.shutdown()


def test_hub_down_journals_writes_then_replays(palace, monkeypatch):
    from distr.core.mempalace import hub
    from distr.core.mempalace.adapter import MemPalaceAdapter

    state = _register(palace, 1, pid=999999)  # dead pid => hub configured but down
    _no_chroma(monkeypatch)
    monkeypatch.setenv("DECISIONS_MEMPALACE_DIRECT_READ_FALLBACK", "0")
    a = MemPalaceAdapter(str(palace))
    assert a.status()["backend"].startswith("mempalace-hub-down")
    w = a.add_drawer("queued text", room="r")
    assert w["queued"] and w["via"] == "journal"
    assert oct(os.stat(hub.journal_path(str(palace))).st_mode & 0o777) == "0o600"
    assert a.search("x")["results"] == []
    assert hub.journal_pending(str(palace)) == 1

    srv = HTTPServer(("127.0.0.1", 0), _FakeHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _FakeHub.calls = []
        _register(palace, srv.server_address[1])
        w2 = a.add_drawer("second", room="r")
        assert w2["via"] == "hub"
        names = [c[1]["content"] for c in _FakeHub.calls]
        assert names == ["queued text", "second"]
        assert hub.journal_pending(str(palace)) == 0
    finally:
        srv.shutdown()


def test_no_hub_configured_keeps_legacy_path(palace):
    from distr.core.mempalace import hub

    assert hub.hub_configured(str(palace)) is False
    assert hub.discover(str(palace)) is None
