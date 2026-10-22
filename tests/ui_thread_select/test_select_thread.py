"""select_thread / open_thread tools (2026-10-22 selthread). Never opens a real browser."""

from __future__ import annotations

import pytest

from distr.core import ui_thread_select as uts

DEV = [
    {"id": 944, "title": "can you tell me what this project", "modified_date": "2026-10-21T11:54:47", "parent_id": None},
    {"id": 1798, "title": "Billing export fix", "modified_date": "2026-10-22T09:00:00", "parent_id": None},
    {"id": 1550, "title": "Build responsive approval lifecycle", "modified_date": "2026-10-21T11:53:22", "parent_id": None},
]
CHAT = [{"id": 1789, "title": "Morning chat", "modified_date": "2026-10-22T07:00:00", "parent_id": None}]
BASE = "http://127.0.0.1:8765"


def fetch(surface, base):
    return DEV if surface == "development" else CHAT


class Recorder:
    def __init__(self, acked=0):
        self.acked, self.emits, self.opened = acked, [], []

    def emit(self, surface, tid, base, **kw):
        self.emits.append((surface, tid, kw))
        return {"acked": self.acked}

    def opener(self, url):
        self.opened.append(url)
        return True


@pytest.mark.parametrize("q", ["latest", "the latest", "most recent", "newest thread", "", None])
def test_latest_is_most_recently_updated(q):
    assert uts.resolve_thread(q, DEV)["id"] == 1798


def test_resolve_by_id_and_title():
    assert uts.resolve_thread("1550", DEV)["id"] == 1550
    assert uts.resolve_thread("thread 944", DEV)["id"] == 944
    assert uts.resolve_thread("approval lifecycle", DEV)["id"] == 1550
    assert uts.resolve_thread("nothing like this", DEV) is None


def test_default_surface_is_development():
    assert uts.norm_surface(None) == "development"
    assert uts.norm_surface("chat") == "chat"


def test_select_path_when_tab_acks():
    r = Recorder(acked=2)
    res = uts.select_thread("latest", None, base=BASE, fetch=fetch, emit=r.emit, opener=r.opener)
    assert res["ok"] and res["path"] == "selected" and res["thread_id"] == 1798 and res["acked"] == 2
    assert r.emits[0][:2] == ("development", 1798) and r.opened == []


def test_select_falls_back_to_open_when_no_tab():
    r = Recorder(acked=0)
    res = uts.select_thread("latest", "chat", base=BASE, fetch=fetch, emit=r.emit, opener=r.opener)
    assert res["ok"] and res["path"] == "opened"
    assert r.opened == [f"{BASE}/chat/?id=1789"]
    assert len(r.emits) == 1  # no second focus emit on fallback


def test_open_builds_development_url():
    r = Recorder(acked=0)
    res = uts.open_thread("1550", "development", base=BASE, fetch=fetch, emit=r.emit, opener=r.opener)
    assert res["path"] == "opened" and r.opened == [f"{BASE}/development/threads/1550/"]


def test_open_reuses_open_tab():
    r = Recorder(acked=1)
    res = uts.open_thread("latest", None, base=BASE, fetch=fetch, emit=r.emit, opener=r.opener)
    assert res["path"] == "selected" and r.opened == [] and r.emits[0][2]["focus"] is True


def test_no_server():
    import unittest.mock as m
    with m.patch.object(uts, "base_url", return_value=None):
        res = uts.select_thread("latest", fetch=fetch, opener=lambda u: pytest.fail("opened"))
    assert not res["ok"]


def test_registered_for_oracle():
    from distr.core.agent.tools import loader
    from distr.core.agent.tools.chat.select_thread import OpenThreadTool, SelectThreadTool

    assert loader._get_tool_class("SelectThreadTool") is SelectThreadTool
    assert loader._get_tool_class("OpenThreadTool") is OpenThreadTool
    assert SelectThreadTool().name == "select_thread" and OpenThreadTool().name == "open_thread"
    from distr.core.agent.tool_intents import forced_tool_names_for_text as f
    assert "select_thread" in f("select the latest thread")
    assert "open_thread" in f("open my most recent thread")


def test_registered_for_development_agent_and_mcp():
    from distr.core.workflow import dev_agent_tools as d

    exposed = d.exposed_tools()
    for name in ("select_thread", "open_thread"):
        assert name in d.TOOL_SCHEMAS and name in d.DISPATCH and name in exposed
        assert set(exposed[name]["parameters"]["properties"]) == {"thread", "surface"}


def test_dev_dispatch_uses_shared_logic(monkeypatch):
    from distr.core.workflow import dev_agent_tools as d

    monkeypatch.setattr(uts, "select_thread", lambda t, s: {"ok": True, "path": "selected", "summary": "ok", "thread_id": 1798})
    out = d.call_tool("select_thread", {"thread": "latest"})
    assert out["ok"] and out["tool"] == "select_thread" and out["path"] == "selected"


def test_endpoint_emits_event_and_counts_acks(monkeypatch):
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", "t" * 32)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from distr.core.events import get_event_bus
    from distr.core.events.types import ALL_EVENT_TYPES, UI_THREAD_SELECT
    from distr.gui.web.routes import events_stream as es

    assert UI_THREAD_SELECT in ALL_EVENT_TYPES
    seen = []

    def tab(event_type, data):  # a browser tab that acks
        seen.append(data)
        with es._ack_lock:
            es._pending_acks[data["request_id"]] += 1

    app = FastAPI()
    app.include_router(es.router)
    client = TestClient(app)
    hdr = {"X-DecisionsAI-Internal-Token": "t" * 32}
    bus = get_event_bus()
    bus.subscribe(UI_THREAD_SELECT, tab)
    try:
        r = client.post("/api/events/ui/select-thread", json={"surface": "development", "thread_id": 1798, "timeout": 0.5}, headers=hdr)
    finally:
        bus.unsubscribe(UI_THREAD_SELECT, tab)
    assert r.status_code == 200 and r.json()["acked"] == 1
    assert seen[0]["thread_id"] == 1798 and seen[0]["surface"] == "development"
    r = client.post("/api/events/ui/select-thread", json={"surface": "chat", "thread_id": 5, "timeout": 0.3}, headers=hdr)
    assert r.json()["acked"] == 0
    assert client.post("/api/events/ui/select-thread", json={"thread_id": 1}).status_code in (401, 403)
