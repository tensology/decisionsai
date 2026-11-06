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
    # Paul's order: stop/start terminal button before the ⋯ menu.
    assert html.index('id="thread-terminal-button"') < html.index('id="thread-more-button"')
    assert 'src="/static/development/app.js?v=' in html
    for needle in ("/terminal/status", "setTerminalProjectRunning", "Start terminal", "Stop terminal", "toggleThreadTerminal"):
        assert needle in js
    assert ".thread-terminal-button" in css


def test_terminal_websocket_supports_attach_live_query_contract():
    """Terminal WS still accepts attach=live for receive-only live_sessions viewers."""
    from pathlib import Path

    source = Path("distr/gui/web/routes/development/terminals.py").read_text(encoding="utf-8")
    assert 'attach_live = str(websocket.query_params.get("attach")' in source
    assert "if attach_live or not backend.supports_rpc:" in source
    assert "backend_override" in source
    assert 'websocket.query_params.get("backend_id")' in source


def test_cli_mode_real_pty_contract():
    """CLI mode must attach an xterm to a real project-folder PTY, not the JSON harness mock prompt."""
    cli = Path("distr/gui/web/static/development/threads/cli-mode.js").read_text(encoding="utf-8")
    html = Path("distr/gui/web/templates/development/index.html").read_text(encoding="utf-8")
    routes = Path("distr/gui/web/routes/development/terminals.py").read_text(encoding="utf-8")
    assert "new window.Terminal" in cli or "new window.Terminal(" in cli
    assert "startup-terminal/" in cli
    assert "pi-cli-terminal/start" in cli
    assert "type: 'input'" in cli or 'type: "input"' in cli
    assert "attach=live" not in cli or "Do NOT" in cli or "cannot render" in cli
    assert 'params.set("attach"' not in cli
    assert 'id="thread-cli-xterm-hosts"' in html or 'id="thread-cli-xterm"' in html
    assert "thread-cli-prompt-input" not in html
    assert 'pi-cli-terminal/start' in routes
    assert 'purpose="pi_cli"' in routes or "purpose='pi_cli'" in routes


def test_cli_mode_one_session_per_thread_contract():
    """One CLI session per thread: no tab bar; toggle reconnects same process_id; no PTY input on replay."""
    cli = Path("distr/gui/web/static/development/threads/cli-mode.js").read_text(encoding="utf-8")
    html = Path("distr/gui/web/templates/development/index.html").read_text(encoding="utf-8")
    routes = Path("distr/gui/web/routes/development/terminals.py").read_text(encoding="utf-8")
    store = Path("distr/core/project_cli_backends/pi_cli_tabs.py").read_text(encoding="utf-8")

    # No tab chrome in the pane.
    assert "thread-cli-tabs" not in html
    assert "thread-cli-tab-new" not in cli
    assert "dismissTab" not in cli
    assert "openNewTab" not in cli
    assert 'id="thread-cli-xterm-hosts"' in html

    # One session per thread (chat id), toggle reuses process_id without spawning.
    assert "thread-${" in cli or "thread-" in cli
    assert "session.processId && !forceNew" in cli
    assert "_skipReplay" in cli
    assert "_acceptInput" in cli
    assert "parkForOtherWorkspace" in cli
    assert "scheduleFit" in cli
    assert "isPaneVisible" in cli
    assert "scrollToBottom" in cli
    css = Path("distr/gui/web/static/development/threads/styles.css").read_text(encoding="utf-8")
    # Terminal must sit flush under the chrome (no padded blank band / leftover tab strip).
    assert "padding: 8px 10px 12px" not in css.split(".thread-cli-xterm {", 1)[-1].split("}", 1)[0]
    assert "cli-onethread2" in css or "flush under chrome" in css

    # Durable persist + recover still exist (one record per thread via tabs API).
    assert "pi-cli-terminal/tabs" in cli
    assert "pi-cli-terminal/recover" in cli
    assert 'purpose="pi_cli"' in routes
    assert "pi-cli-terminal/recover" in routes
    assert '"replay": True' in routes or "'replay': True" in routes
    assert "save_project_tabs" in store
    assert "pi_cli_tabs.json" in store
    # stop must not mass-kill by default
    assert "stop_all = bool(body.get(\"all\"))" in routes or "stop_all = bool(body.get('all'))" in routes


def test_pi_cli_tabs_store_roundtrip(tmp_path, monkeypatch):
    from distr.core.project_cli_backends import pi_cli_tabs as tabs

    monkeypatch.setattr(tabs, "_path", lambda: str(tmp_path / "pi_cli_tabs.json"))
    saved = tabs.save_project_tabs(
        7,
        tabs=[
            {"tab_id": "thread-11", "process_id": "p1", "command": "pi", "visible": True},
            {"tab_id": "thread-12", "process_id": "p2", "command": "pi", "visible": True},
        ],
        active_tab_id="thread-11",
    )
    assert saved["active_tab_id"] == "thread-11"
    assert len(saved["tabs"]) == 2
    loaded = tabs.load_project_tabs(7)
    assert loaded["active_tab_id"] == "thread-11"
    assert [t["tab_id"] for t in loaded["tabs"]] == ["thread-11", "thread-12"]


def test_cli_mode_survives_project_terminals_navigation():
    """Leaving chat for project terminals must park CLI mode, not turn it off."""
    cli = Path("distr/gui/web/static/development/threads/cli-mode.js").read_text(encoding="utf-8")
    sidebar = Path("distr/gui/web/static/development/sidebar/index.js").read_text(encoding="utf-8")
    terminals_js = Path("distr/gui/web/static/development/terminals/index.js").read_text(encoding="utf-8")
    routes = Path("distr/gui/web/routes/development/terminals.py").read_text(encoding="utf-8")

    assert "parkForOtherWorkspace" in cli
    assert "must NOT turn CLI mode off" in cli or "must NOT set CLI mode off" in cli.lower() or "Leaving chat" in cli
    # The old behavior turned CLI off whenever workspaceMode !== chat.
    assert "setOn(false)" not in cli.split("function syncFromShell")[1].split("function bind")[0]

    assert "function isThreadActive" in sidebar
    assert "workspaceMode === 'terminals'" in sidebar

    assert "pi-cli-terminal/status" in terminals_js
    assert "purpose === 'pi_cli'" in terminals_js or 'purpose === "pi_cli"' in terminals_js
    # Stop all must not treat pi_cli as an auxiliary kill target.
    assert "purpose === 'pi_cli'" in terminals_js

    assert 'purpose="pi_cli"' in routes
    assert 'get_startup_sessions_for_project(project_id, purpose="pi_cli")' in routes
    # terminal-status includes pi_cli sessions for the project view list.
    assert '"pi_cli_count": len(pi_cli)' in routes or "pi_cli_count" in routes


class _RichFakeSess(_FakeSess):
    """Enough surface for get_startup_sessions_for_project / kill_all without a real PTY."""

    def __init__(self, project_id, purpose="startup", alive=True):
        super().__init__(project_id, purpose=purpose, alive=alive)
        self.session_key = f"fake-{purpose}-{project_id}"
        self.cwd = "/tmp"
        self.command = self.shell_command
        self._running = True
        self._reader_task = None
        self.master_fd = None

    def get_buffer(self, _n=80):
        return ""

    def _collect_descendants(self, _pid):
        return []


def test_startup_stop_does_not_target_pi_cli(monkeypatch):
    """Stop all startup terminals must keep purpose=pi_cli sessions alive."""
    from distr.core import project_startup_terminals as pst

    monkeypatch.setattr(pst, "_load_project", lambda project_id: {"id": project_id, "name": "Demo"})
    monkeypatch.setattr(pst, "announce_project_terminal_feedback", lambda *_a, **_k: None)
    monkeypatch.setattr(term, "discard_queued_startup_terminals_for_project", lambda *_a, **_k: 0)
    monkeypatch.setattr(term, "cleanup_dead_startup_sessions", lambda: 0)

    term._startup_sessions.clear()
    term._startup_sessions["startup-1"] = _RichFakeSess(7, purpose="startup")
    term._startup_sessions["cli-1"] = _RichFakeSess(7, purpose="pi_cli")
    term._startup_sessions["shell-1"] = _RichFakeSess(7, purpose="cli_shell")

    result = pst.stop_project_startup_terminals(7, announce=False)
    assert result.success is True
    assert result.stopped == 1
    assert "startup-1" not in term._startup_sessions
    assert "cli-1" in term._startup_sessions
    assert term._startup_sessions["cli-1"].purpose == "pi_cli"
    assert "shell-1" in term._startup_sessions


def test_startup_sessions_and_terminal_status_list_pi_cli(client, monkeypatch):
    monkeypatch.setattr(term, "cleanup_dead_startup_sessions", lambda: 0)
    monkeypatch.setattr(term, "materialize_queued_startup_terminals", lambda *_a, **_k: (0, 0))

    async def _no_materialize(project_id):
        return (0, 0)

    monkeypatch.setattr(term, "materialize_queued_startup_terminals", _no_materialize)

    import distr.gui.web.routes.development.terminals as term_routes

    monkeypatch.setattr(term_routes, "_discover_project_server_processes", lambda *a, **k: [])
    monkeypatch.setattr(term_routes, "_attach_process_usage", lambda rows: rows)
    monkeypatch.setattr(term_routes, "_managed_process_tree_pids", lambda pids: set(pids or []))

    term._startup_sessions.clear()
    term._startup_sessions["startup-1"] = _RichFakeSess(7, purpose="startup")
    term._startup_sessions["cli-1"] = _RichFakeSess(7, purpose="pi_cli")

    listed = client.get("/api/projects/7/startup-sessions").json()["sessions"]
    purposes = {row.get("purpose") for row in listed}
    assert "startup" in purposes
    assert "pi_cli" in purposes

    status = client.get("/api/projects/terminal-status?project_ids=7").json()["projects"]["7"]
    status_purposes = {row.get("purpose") for row in status["sessions"]}
    assert "pi_cli" in status_purposes
    assert status.get("pi_cli_count", 0) >= 1
