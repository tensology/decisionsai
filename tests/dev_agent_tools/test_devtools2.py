"""devtools2: DB approvals + UI-only grant, calendar (Google/iCloud), files, browser, Cursor merge/restore.
Temp HOME + DECISIONS_DB_DIR only; fake calendar backends; no real Chrome, Trash or network."""
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOKEN = "test-internal-token-xyz"
BASE = "http://127.0.0.1:8765"
UI = {"Origin": BASE, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "X-Decisions-User-Action": "click"}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Desktop").mkdir()
    (home / "Documents").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("DECISIONS_CHAT_ID", "")
    monkeypatch.setenv("DECISIONS_TURN_ID", "")
    monkeypatch.delenv("DECISIONS_CURSOR_DEV_MCP", raising=False)
    assert "settings.db" not in os.environ.get("DECISIONS_DB_DIR", "") and os.environ.get("DECISIONS_DB_DIR")
    from distr.core.paths import DB_DIR
    assert "DecisionsAI/db" not in DB_DIR  # never the real database
    return home


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes import dev_approvals as route
    from distr.core.workflow import dev_approvals as ap
    import distr.core.db  # noqa: F401  (creates dev_approval_requests in the temp DB)

    cards = []
    monkeypatch.setattr(route, "_post_card", lambda row: cards.append(("post", row.request_id)) or None)
    monkeypatch.setattr(route, "_update_card", lambda row: cards.append(("update", row.status)))
    app = FastAPI()
    app.include_router(route.router)
    c = TestClient(app, base_url=BASE)
    c.cards = cards

    def transport(method, path, body):
        r = c.request(method, path, json=body, headers={"X-DecisionsAI-Internal-Token": TOKEN})
        return r.status_code, r.json()

    monkeypatch.setattr(ap, "transport", transport)
    monkeypatch.setattr(ap, "INLINE_WAIT_S", 0)
    monkeypatch.setattr(ap, "POLL_S", 0.01)
    return c


def _approve(client, rid, headers=UI):
    return client.post(f"/api/dev-approvals/{rid}/approve", headers={"X-DecisionsAI-Internal-Token": TOKEN, **headers})


# ======================= 1) approvals =======================
def test_agent_module_has_no_grant():
    from distr.core.workflow import dev_approvals as ap
    for name in ("grant", "approve", "_BOOT_KEY", "deny"):
        assert not hasattr(ap, name)
    src = Path(ap.__file__).read_text()
    assert "/approve" not in src and "distr.core.db" not in src.split('"""', 2)[2]


def test_subprocess_cannot_import_a_grant():
    code = ("from distr.core.workflow import dev_approvals as a; import json;"
            "print(json.dumps([n for n in dir(a) if 'grant' in n.lower() or 'approve' == n.lower()]))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         cwd=str(Path(__file__).resolve().parents[2]), env=os.environ.copy())
    assert out.returncode == 0 and json.loads(out.stdout.strip().splitlines()[-1]) == []


def test_flow_request_approve_wait_consume_once(client):
    from distr.core.workflow import dev_approvals as ap
    args = {"action": "delete", "event_id": "e1"}
    blocked = ap.gate("calendar", "delete", args, None, "delete e1", {"before": {"summary": "x"}})
    assert blocked["needs_approval"] and ("post", blocked["request_id"]) in client.cards
    rid = blocked["request_id"]
    assert ap.approval_wait(rid, 0)["status"] == "pending"
    r = _approve(client, rid)
    assert r.status_code == 200 and r.json()["status"] == "approved" and "approval_id" not in r.json()
    w = ap.approval_wait(rid, 0)
    aid = w["approval_id"]
    assert ap.approval_wait(rid, 0).get("approval_id") is None  # handed out once
    assert not ap.consume(aid, "calendar", "delete", {"action": "delete", "event_id": "e2"})  # other args
    assert not ap.consume(aid, "calendar", "create", args)  # other action
    assert ap.gate("calendar", "delete", args, aid, "delete e1") is None  # consumed
    assert not ap.consume(aid, "calendar", "delete", args)  # single use


def test_deny_and_turn_binding(client, monkeypatch):
    from distr.core.workflow import dev_approvals as ap
    r = ap.request_approval("files", "trash", {"path": "/x"}, "trash /x")
    d = client.post(f"/api/dev-approvals/{r['request_id']}/deny", headers={"X-DecisionsAI-Internal-Token": TOKEN, **UI})
    assert d.json()["status"] == "denied" and _approve(client, r["request_id"]).status_code == 409
    monkeypatch.setenv("DECISIONS_CHAT_ID", "5")
    monkeypatch.setenv("DECISIONS_TURN_ID", "1")
    r = ap.request_approval("files", "trash", {"path": "/y"}, "trash /y")
    _approve(client, r["request_id"])
    aid = ap.approval_wait(r["request_id"], 0)["approval_id"]
    monkeypatch.setenv("DECISIONS_TURN_ID", "2")  # next turn
    assert not ap.consume(aid, "files", "trash", {"path": "/y"})


@pytest.mark.parametrize("headers", [
    {},                                                    # curl with the token
    {"Origin": BASE},                                      # forged origin only
    {"Origin": "http://evil.example", "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "X-Decisions-User-Action": "click"},
    {"Origin": BASE, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"},  # no click marker
])
def test_scripts_cannot_approve(client, headers):
    from distr.core.workflow import dev_approvals as ap
    r = ap.request_approval("files", "trash", {"path": "/z"}, "trash /z")
    assert _approve(client, r["request_id"], headers).status_code == 403
    assert client.post(f"/api/dev-approvals/{r['request_id']}/approve", headers=UI).status_code == 401  # no token


def test_db_tampering_cannot_self_approve(client):
    """A process that writes the DB row (even with its own HMAC key) still fails consume."""
    import hashlib, hmac, secrets
    from distr.core.db import DevApprovalRequest, get_session
    from distr.core.workflow import dev_approvals as ap
    args = {"path": "/w"}
    r = ap.request_approval("files", "trash", args, "trash /w")
    forged = "apr_forged"
    with get_session() as s:
        row = s.query(DevApprovalRequest).filter_by(request_id=r["request_id"]).first()
        row.status = "approved"
        row.approval_id_hash = hashlib.sha256(forged.encode()).hexdigest()
        other_key = secrets.token_bytes(32)  # an attacker's process never has the app's per-boot key
        msg = "|".join(str(x) for x in (row.request_id, row.chat_id, row.turn_id, row.tool, row.action, row.args_hash, row.approval_id_hash))
        row.signature = hmac.new(other_key, msg.encode(), hashlib.sha256).hexdigest()
        s.commit()
    assert not ap.consume(forged, "files", "trash", args)


def test_app_unreachable_is_needs_approval(monkeypatch):
    from distr.core.workflow import dev_approvals as ap
    monkeypatch.setenv("DECISIONS_API_BASE", "http://127.0.0.1:9")
    out = ap.gate("files", "trash", {"path": "/q"}, None, "trash /q")
    assert out["needs_approval"] and "not reachable" in out["summary"]


# ======================= 4) calendar =======================
class FakeCal:
    def __init__(self, name="google", healthy=True):
        self.name, self.healthy, self.calls = name, healthy, []
        self.events = {"e1": {"id": "e1", "summary": "Standup", "start": "2026-10-23T09:00:00+02:00"}}

    def health(self):
        return (True, "") if self.healthy else (False, "not linked")

    def list(self, tmin, tmax, n):
        self.calls.append(("list", tmin, tmax, n))
        return list(self.events.values())

    def get(self, eid):
        return self.events.get(eid)

    def create(self, *a):
        self.calls.append(("create",) + a)
        return "new1"

    def update(self, eid, *a):
        self.calls.append(("update", eid) + a)
        return True

    def delete(self, eid):
        self.calls.append(("delete", eid))
        return True


def test_provider_resolution():
    from distr.core.workflow.dev_calendar_tools import NotConnected, resolve_provider
    g_ok, g_bad, ic = FakeCal("google"), FakeCal("google", healthy=False), FakeCal("icloud")
    assert resolve_provider("auto", {}, google=g_ok, icloud=ic) is g_ok
    assert resolve_provider("auto", {}, google=g_bad, icloud=ic) is ic
    assert resolve_provider("auto", {"dev_calendar_provider": "icloud"}, google=g_ok, icloud=ic) is ic
    with pytest.raises(NotConnected):
        resolve_provider("auto", {}, google=g_bad)
    with pytest.raises(NotConnected, match="iCloud is not configured"):
        resolve_provider("icloud", {})
    p = resolve_provider("icloud", {"icloud_apple_id": "a@icloud.com", "icloud_app_password": "abcd-efgh"})
    assert p.name == "icloud" and "abcd" not in repr(vars(p).get("apple_id"))


def test_eventkit_never_prompts_and_falls_back(monkeypatch):
    from distr.core.workflow import dev_calendar_tools as cal
    monkeypatch.delenv("DECISIONS_IN_APP_PROCESS", raising=False)
    assert cal.eventkit_status() == "background"
    with pytest.raises(cal.NotConnected, match="permission needed"):
        cal.resolve_provider("eventkit", {})
    ic = FakeCal("icloud")
    assert cal.resolve_provider("eventkit", {}, icloud=ic) is ic


def test_calendar_not_connected():
    from distr.core.workflow.dev_calendar_tools import calendar
    r = calendar("list", backend=FakeCal(healthy=False))
    assert not r["ok"] and r["error"] == "not_connected"


def test_calendar_reads_default_tz():
    from distr.core.workflow.dev_calendar_tools import calendar
    c = FakeCal()
    r = calendar("list", time_min="2026-10-23T09:00", backend=c)
    assert r["ok"] and str(c.calls[0][1].tzinfo) == "Africa/Johannesburg"
    assert calendar("get", event_id="e1", backend=c)["event"]["summary"] == "Standup"


@pytest.mark.parametrize("name", ["google", "icloud"])
@pytest.mark.parametrize("action,kw", [
    ("create", dict(summary="Demo", start_time="2026-10-23T10:00", end_time="2026-10-23T11:00")),
    ("update", dict(event_id="e1", summary="Renamed")),
    ("delete", dict(event_id="e1")),
])
def test_calendar_writes_go_through_approval(client, name, action, kw):
    from distr.core.workflow import dev_approvals as ap
    from distr.core.workflow.dev_calendar_tools import calendar
    c = FakeCal(name)
    r = calendar(action, backend=c, **kw)
    assert r["needs_approval"] and not [x for x in c.calls if x[0] == action]
    _approve(client, r["request_id"])
    aid = ap.approval_wait(r["request_id"], 0)["approval_id"]
    r2 = calendar(action, backend=c, approval_id=aid, **kw)
    assert r2["ok"] and [x for x in c.calls if x[0] == action]
    assert calendar(action, backend=c, approval_id=aid, **kw)["needs_approval"]  # replay refused


def test_calendar_card_shows_existing_event_for_delete(client, monkeypatch):
    from distr.core.workflow import dev_approvals as ap
    from distr.core.workflow.dev_calendar_tools import calendar
    seen = {}
    real = ap.request_approval
    monkeypatch.setattr(ap, "request_approval", lambda *a, **k: seen.update(details=a[4] if len(a) > 4 else k.get("details")) or real(*a, **k))
    calendar("delete", event_id="e1", backend=FakeCal())
    assert seen["details"]["before"]["summary"] == "Standup"
    assert calendar("delete", event_id="nope", backend=FakeCal())["summary"].startswith("event nope not found")


def test_icloud_caldav_adapter_with_fake_client():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import icalendar
    from distr.core.workflow.dev_calendar_tools import ICloudCalendar

    class Ev:
        def __init__(self, comp): self.icalendar_component, self.saved, self.deleted = comp, 0, False
        def save(self): self.saved += 1
        def delete(self): self.deleted = True

    class Cal:
        name = "Home"
        def __init__(self): self.store = {}
        def search(self, **kw): return list(self.store.values())
        def event_by_uid(self, uid): return self.store[uid]
        def save_event(self, **p):
            comp = icalendar.Event()
            for k, v in p.items(): comp.add(k, v)
            self.store[p["uid"]] = Ev(comp)

    class Client:
        def __init__(self): self.cal = Cal()
        def principal(self): return self
        def calendars(self): return [self.cal]

    cl = Client()
    ic = ICloudCalendar("a@icloud.com", "pw", "home", client=cl)
    tz = ZoneInfo("Africa/Johannesburg")
    uid = ic.create("Demo", datetime(2026, 10, 23, 10, tzinfo=tz), datetime(2026, 10, 23, 11, tzinfo=tz), None, "Office", "Africa/Johannesburg")
    assert ic.health() == (True, "")
    assert ic.get(uid)["summary"] == "Demo" and ic.list(None, None, 10)[0]["location"] == "Office"
    assert ic.update(uid, "Renamed", None, None, None, None, "x") and ic.get(uid)["summary"] == "Renamed"
    assert ic.delete(uid) and cl.cal.store[uid].deleted
    assert ICloudCalendar("a", "b", "Work", client=cl).health()[0] is False


# ======================= 5) files =======================
@pytest.fixture
def trashed(monkeypatch):
    from distr.core.workflow import dev_file_tools as f
    seen = []
    monkeypatch.setattr(f, "trash_path", lambda p: seen.append(str(p)) or "/Trash/x")
    return seen


def test_files_allowlist_and_denylist(_env, tmp_path):
    from distr.core.workflow.dev_file_tools import files
    proj = tmp_path / "proj"; proj.mkdir()
    S = {"dev_files_enabled": True}
    assert files("create_file", "~/Downloads/a.txt", content="hi", settings=S, project_folder=str(proj))["ok"]
    assert files("read", "~/Downloads/a.txt", settings=S)["content"] == "hi"
    assert files("create_file", "notes.md", content="x", settings=S, project_folder=str(proj))["ok"]
    for bad in ("~/.ssh/id_rsa", "~/Library/x", "/etc/hosts", "~/Downloads/../.decisionsai/internal_api_token",
                str(tmp_path / "elsewhere.txt"), "~/Downloads/settings.db"):
        r = files("read", bad, settings=S, project_folder=str(proj))
        assert not r["ok"] and "Not allowed" in r["summary"], bad
    (_env / "Downloads" / "link").symlink_to("/etc")
    assert "Not allowed" in files("list", "~/Downloads/link", settings=S)["summary"]
    assert not files("read", "x", settings={"dev_files_enabled": False})["ok"]
    assert files("read", "/", settings=S, project_folder="/")["ok"] is False  # project=/ never widens access


def test_files_trash_move_overwrite_need_approval(client, trashed, _env):
    from distr.core.workflow import dev_approvals as ap
    from distr.core.workflow.dev_file_tools import files
    S = {"dev_files_enabled": True}
    d = _env / "Downloads"
    (d / "a.txt").write_text("1")
    r = files("trash", "~/Downloads/a.txt", settings=S)
    assert r["needs_approval"] and not trashed and (d / "a.txt").exists()
    _approve(client, r["request_id"])
    aid = ap.approval_wait(r["request_id"], 0)["approval_id"]
    assert files("trash", "~/Downloads/a.txt", settings=S, approval_id=aid)["ok"] and trashed == [str((d / "a.txt").resolve())]
    (d / "b.txt").write_text("b")
    assert files("move", "~/Downloads/b.txt", destination="~/Desktop", settings=S)["needs_approval"]
    assert files("write", "~/Downloads/b.txt", content="new", settings=S)["ok"] is False  # needs overwrite=true
    assert files("write", "~/Downloads/b.txt", content="new", overwrite=True, settings=S)["needs_approval"]
    assert files("copy", "~/Downloads/b.txt", destination="~/Desktop/c.txt", settings=S)["ok"]  # no clobber: free
    assert files("trash", "~/Downloads", settings=S)["ok"] is False  # never a root


def test_trash_never_uses_rm_finder_or_osascript(monkeypatch, tmp_path):
    from distr.core.workflow import dev_file_tools as f
    src = Path(f.__file__).read_text()
    for banned in ("osascript", "Finder", "os.remove", "unlink(", "rmtree", "rmdir("):
        assert banned not in src.split('"""', 2)[2], banned
    import builtins
    real_import = builtins.__import__
    def no_backends(name, *a, **k):
        if name in ("Foundation", "send2trash"):
            raise ImportError(name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_backends)
    p = tmp_path / "keep.txt"; p.write_text("x")
    with pytest.raises(OSError, match="refusing to delete"):
        f.trash_path(p)
    assert p.exists()


# ======================= 6) browser =======================
def test_browser_off_by_default():
    from distr.core.workflow.dev_browser_tools import browser
    assert "off" in browser("open", url="https://example.com", settings={})["summary"]


@pytest.mark.parametrize("url,ok", [
    ("file:///etc/passwd", False), ("chrome://settings", False), ("http://10.0.0.5/", False),
    ("http://192.168.1.1/", False), ("http://169.254.169.254/latest", False),
    ("http://127.0.0.1:8765/development", False), ("http://localhost:8765/", False),
    ("http://127.0.0.1:5173/", True), ("https://93.184.215.14/", True),
])
def test_browser_url_guard(url, ok):
    from distr.core.workflow.dev_browser_tools import check_url
    assert (check_url(url) is None) is ok


class FakeBrowser:
    def __init__(self, element):
        self.element, self.sent = element, []

    def __call__(self, req, headed):
        self.sent.append((req["cmd"], headed))
        if req["cmd"] == "describe":
            return {"ok": True, "element": self.element}
        return {"ok": True, "url": "https://shop.example/cart", "title": "Cart"}


def test_browser_password_and_risky_clicks(client, monkeypatch):
    from distr.core.workflow import dev_approvals as ap
    from distr.core.workflow import dev_browser_tools as b
    S = {"dev_browser_enabled": True}
    fb = FakeBrowser({"tag": "input", "type": "password", "is_password": True})
    monkeypatch.setattr(b, "transport", fb)
    assert "password" in b.browser("type", selector="#pw", text="x", settings=S)["summary"]
    assert ("type", False) not in fb.sent
    fb = FakeBrowser({"tag": "button", "text": "Place order", "is_submit": True})
    monkeypatch.setattr(b, "transport", fb)
    r = b.browser("click", selector="text=Place order", settings=S)
    assert r["needs_approval"] and ("click", False) not in fb.sent
    _approve(client, r["request_id"])
    aid = ap.approval_wait(r["request_id"], 0)["approval_id"]
    assert b.browser("click", selector="text=Place order", settings=S, approval_id=aid)["ok"] and ("click", False) in fb.sent
    fb = FakeBrowser({"tag": "a", "text": "Docs", "is_submit": False})
    monkeypatch.setattr(b, "transport", fb)
    assert b.browser("click", selector="text=Docs", settings=S)["ok"]  # harmless click: no approval
    fb = FakeBrowser({"tag": "input", "type": "search"})
    monkeypatch.setattr(b, "transport", fb)
    assert b.browser("type", selector="#q", text="hi", submit=True, settings=S)["needs_approval"]
    assert b.browser("type", selector="#q", text="hi", settings=S)["ok"]
    assert b.browser("open", url="https://93.184.215.14/", headed=True, settings=S)["ok"] and ("open", True) in fb.sent


def test_browser_one_at_a_time_and_profile(monkeypatch, _env):
    from distr.core.workflow import dev_browser_daemon as d
    from distr.core.workflow import dev_browser_tools as b
    assert str(d.profile_dir()).endswith(".decisions/chrome-profile")
    lock = open(d.lock_path(), "w"); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert d.serve(headless=True) == 3  # second browser refused before Chrome starts
    finally:
        lock.close()
    monkeypatch.setattr(b, "_running_mode", lambda: "headless")
    assert "already running" in b._socket_transport({"cmd": "open"}, True)["error"]
    main = _env / "Library" / "Application Support" / "Google" / "Chrome"
    monkeypatch.setattr(d, "state_dir", lambda: main)
    with pytest.raises(RuntimeError, match="main Chrome profile"):
        d.profile_dir()


# ======================= 3) Cursor temp .cursor/mcp.json =======================
def _ctask(folder):
    from distr.core.project_cli_backends.base import ProjectTask
    return ProjectTask(project_id=1, project_name="P", folder=str(folder), instruction="do", origin="development", chat_id=7)


def test_cursor_default_on_creates_and_removes(tmp_path):
    from distr.core.project_cli_backends import registry as r
    proj = tmp_path / "proj"; proj.mkdir()
    st = r._cursor_dev_mcp_install(_ctask(proj))
    assert json.loads((proj / ".cursor" / "mcp.json").read_text())["mcpServers"]["decisions_dev"]
    assert "--approve-mcps" in r.CursorBackend()._build_command("cursor-agent", _ctask(proj))
    r._cursor_dev_mcp_remove(st)
    assert not (proj / ".cursor").exists()


def test_cursor_merges_and_restores_exactly(tmp_path):
    from distr.core.project_cli_backends import registry as r
    proj = tmp_path / "proj"; (proj / ".cursor").mkdir(parents=True)
    f = proj / ".cursor" / "mcp.json"
    raw = b'{\n  "mcpServers": {"mine": {"command": "x"}},\n  "other": 1\n}  \n'
    f.write_bytes(raw); os.chmod(f, 0o640); os.utime(f, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    st = r._cursor_dev_mcp_install(_ctask(proj))
    merged = json.loads(f.read_text())
    assert set(merged["mcpServers"]) == {"mine", "decisions_dev"} and merged["other"] == 1
    r._cursor_dev_mcp_remove(st)
    assert f.read_bytes() == raw and (f.stat().st_mode & 0o777) == 0o640 and f.stat().st_mtime_ns == 1_700_000_000_000_000_000


def test_cursor_recovers_after_crash_and_skips_bad_files(tmp_path):
    from distr.core.project_cli_backends import registry as r
    proj = tmp_path / "proj"; (proj / ".cursor").mkdir(parents=True)
    f = proj / ".cursor" / "mcp.json"
    f.write_text('{"mcpServers": {}}')
    st = r._cursor_dev_mcp_install(_ctask(proj))
    st["lock"].close()  # simulate a crash: finally never ran
    assert "decisions_dev" in f.read_text()
    st2 = r._cursor_dev_mcp_install(_ctask(proj))  # next run restores first, then merges again
    r._cursor_dev_mcp_remove(st2)
    assert f.read_text() == '{"mcpServers": {}}'
    f.write_text("not json")
    assert r._cursor_dev_mcp_install(_ctask(proj)) is None and f.read_text() == "not json"


def test_cursor_send_task_removes_on_exception(tmp_path, monkeypatch):
    import asyncio
    from distr.core.project_cli_backends import registry as r
    proj = tmp_path / "proj"; proj.mkdir()
    async def boom(self, task, on_event=None):
        assert (proj / ".cursor" / "mcp.json").exists()
        raise RuntimeError("crash")
    monkeypatch.setattr(r.OneShotCliBackend, "send_task", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(r.CursorBackend().send_task(_ctask(proj)))
    assert not (proj / ".cursor").exists()


def test_cursor_opt_out_and_concurrent_runs(tmp_path, monkeypatch):
    from distr.core.project_cli_backends import registry as r
    proj = tmp_path / "proj"; proj.mkdir()
    st = r._cursor_dev_mcp_install(_ctask(proj))
    assert r._cursor_dev_mcp_install(_ctask(proj)) is None  # second concurrent run never stacks edits
    r._cursor_dev_mcp_remove(st)
    monkeypatch.setenv("DECISIONS_CURSOR_DEV_MCP", "off")
    assert r._cursor_dev_mcp_install(_ctask(proj)) is None and not (proj / ".cursor").exists()
