"""Thread terminal button: status endpoint, start/stop routing, static markup."""
import time
import types
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core import terminal as term
from distr.core.terminals import thread_terminal as tt

WEB = Path(__file__).resolve().parents[1] / "distr" / "gui" / "web"


class _FakeSess:
    def __init__(self, project_id, purpose="startup", alive=True):
        self.project_id = project_id
        self.purpose = purpose
        self.is_alive = alive
        self.pid = 4242
        self.shell_command = "npm run dev"
        self.created_at = time.time()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(term, "_startup_sessions", {})
    monkeypatch.setattr(term, "cleanup_dead_startup_sessions", lambda: 0)
    projects = {1: 7, 2: None}

    def resolve(tid):
        if tid not in projects:
            raise LookupError(f"Thread {tid} not found")
        return projects[tid]

    monkeypatch.setattr(tt, "resolve_thread_project_id", resolve)
    from distr.gui.web.routes.development.terminals import register_routes
    router = APIRouter()
    register_routes(router, None)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_status_stopped_and_running(client):
    r = client.get("/api/threads/1/terminal/status").json()
    assert r["thread_id"] == 1 and r["project_id"] == 7 and r["running"] is False and r["pid"] is None
    term._startup_sessions["abc"] = _FakeSess(7)
    term._startup_sessions["shell"] = _FakeSess(7, purpose="cli_shell", alive=True)
    term._startup_sessions["other"] = _FakeSess(8)
    r = client.get("/api/threads/1/terminal/status").json()
    assert r["running"] is True and r["pid"] == 4242 and r["session_id"] == "abc"
    assert r["session_count"] == 1 and r["started_at"]


def test_status_404_for_missing_thread(client):
    assert client.get("/api/threads/999/terminal/status").status_code == 404


def test_start_stop_routes_delegate(client, monkeypatch):
    import distr.core.project_startup_terminals as pst
    calls = []

    async def fake_start(pid, **kw):
        calls.append(("start", pid, kw.get("announce")))
        term._startup_sessions["s1"] = _FakeSess(pid)
        return types.SimpleNamespace(success=True, action="started", message="ok")

    def fake_stop(pid, **kw):
        calls.append(("stop", pid, kw.get("announce")))
        term._startup_sessions.clear()
        return types.SimpleNamespace(success=True, action="stopped", message="ok")

    monkeypatch.setattr(pst, "start_project_startup_terminals_async", fake_start)
    monkeypatch.setattr(pst, "stop_project_startup_terminals", fake_stop)
    r = client.post("/api/threads/1/terminal/start").json()
    assert r["running"] is True and r["success"] is True
    r = client.post("/api/threads/1/terminal/stop").json()
    assert r["running"] is False
    assert calls == [("start", 7, False), ("stop", 7, False)]
    assert client.post("/api/threads/2/terminal/start").status_code == 400
    assert client.post("/api/threads/999/terminal/stop").status_code == 404


def test_start_failure_returns_error(client, monkeypatch):
    import distr.core.project_startup_terminals as pst

    async def fail(pid, **kw):
        return types.SimpleNamespace(success=False, action="error", message="no commands")

    monkeypatch.setattr(pst, "start_project_startup_terminals_async", fail)
    r = client.post("/api/threads/1/terminal/start")
    assert r.status_code == 400 and r.json()["running"] is False


def test_static_markup_and_js():
    html = (WEB / "templates/development/index.html").read_text()
    js = (WEB / "static/development/app.js").read_text()
    css = (WEB / "static/development/threads/styles.css").read_text()
    assert 'class="terminal-project-action start thread-terminal-button hidden"' in html
    assert html.index('id="thread-more-button"') < html.index('id="thread-terminal-button"')
    assert 'src="/static/development/app.js?v=' in html
    for needle in ("/terminal/status", "setTerminalProjectRunning", "Start terminal", "Stop terminal", "toggleThreadTerminal"):
        assert needle in js
    assert ".thread-terminal-button" in css
