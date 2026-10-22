"""Tests for routes/api_audit.py (22 Oct 2026 apiaudit): replies, current chat,
message aliases, skill run, settings/all + project_cli guard, kanban retirement."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from tests.gui.test_api_extensions import HDR, TOKEN, _seed, db  # noqa: F401  (db is a fixture)


def _app(monkeypatch, *extra_routers, current_chat=None):
    from distr.core.chat import ChatService
    from distr.gui.web.routes import api_audit, api_extensions

    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setattr(api_audit, "REPLY_POLL_SECONDS", 0.05)
    monkeypatch.setattr(ChatService, "get_current_chat_id", staticmethod(lambda: current_chat))
    monkeypatch.setattr(api_extensions, "_reload_oracle_if_current", lambda root_id: False)
    app = FastAPI()
    app.include_router(api_audit.create_router(), prefix="/api")
    app.include_router(api_extensions.create_router(), prefix="/api")
    for r in extra_routers:
        app.include_router(r, prefix="/api")
    return app


# ------------------------------------------------------------------ auth
def test_every_audit_route_requires_token(db, monkeypatch):
    app = _app(monkeypatch)
    anon = TestClient(app)
    for method, path in [("GET", "/api/chat/replies/1.0.abcdef12"), ("GET", "/api/chat/current"),
                         ("PUT", "/api/chat/current"), ("POST", "/api/skills/x/run"),
                         ("GET", "/api/settings/all"), ("GET", "/api/chat/1/messages/1"),
                         ("PATCH", "/api/chat/1/messages/1"), ("DELETE", "/api/chat/1/messages/1"),
                         ("POST", "/api/chat/1/messages/1/truncate-after"),
                         ("GET", "/api/chats/1/messages"), ("GET", "/api/settings")]:
        assert anon.request(method, path).status_code == 401, path
        assert anon.request(method, path, headers={"X-DecisionsAI-Internal-Token": "wrong"}).status_code == 401


# ------------------------------------------------------------------ 1. replies
def test_reply_id_roundtrip():
    from distr.gui.web.routes.api_audit import make_reply_id, parse_reply_id

    rid = make_reply_id(7, 41, " hello ")
    chat, after, sha, turn = parse_reply_id(rid)
    assert (chat, after, turn) == (7, 41, None) and len(sha) == 8
    assert parse_reply_id("99") == (None, None, None, 99)
    with pytest.raises(Exception):
        parse_reply_id("nope")


def test_send_returns_reply_id_and_reply_endpoint_long_polls(db, monkeypatch):
    import distr.core.signals as signals
    import distr.gui.web.routes.chat as chat_routes
    from distr.core.db import Chat
    from distr.gui.web.routes import api_extensions

    monkeypatch.setattr(api_extensions, "POLL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(chat_routes, "get_session", db)
    monkeypatch.setattr(chat_routes, "is_development_thread", lambda s, c: False)
    root, _ = _seed(db, responses=("old",))
    emitted = []
    monkeypatch.setattr(signals, "signal_manager", SimpleNamespace(
        web_send_to_agent_requested=SimpleNamespace(emit=lambda *a: emitted.append(a))))
    app = _app(monkeypatch, chat_routes.create_routes(Path(".")))
    client = TestClient(app, headers=HDR)

    body = client.post(f"/api/chats/{root}/send-to-agent", json={"message": "hello agent", "speak": False}).json()
    # Backward compatible fields stay; reply_id is new.
    assert body["sent"] is True and "turn_id" in body and "after_row_id" in body
    rid = body["reply_id"]
    assert body["reply_url"] == f"/api/chat/replies/{rid}" and rid.startswith(f"{root}.")

    queued = client.get(f"/api/chat/replies/{rid}").json()
    assert queued["status"] == "queued" and queued["done"] is False and queued["content"] is None

    # Agent persists the row with a reply -> done + content.
    with db() as s:
        row = Chat(parent_id=root, input="hello agent", response="the answer",
                   created_date=datetime(2026, 10, 22, 15, 0), modified_date=datetime(2026, 10, 22, 15, 0))
        s.add(row); s.commit(); turn_id = row.id
    done = client.get(f"/api/chat/replies/{rid}?wait=2").json()
    assert done["done"] is True and done["content"] == "the answer" and done["turn_id"] == turn_id
    assert done["timed_out"] is False
    # Bare turn id works too.
    assert client.get(f"/api/chat/replies/{turn_id}").json()["content"] == "the answer"
    assert client.get("/api/chat/replies/999999").status_code == 404
    assert client.get("/api/chat/replies/bad.id").status_code == 400


def test_reply_wait_is_capped_and_times_out(db, monkeypatch):
    from distr.gui.web.routes import api_audit

    root, ids = _seed(db, responses=("partial",), active=0)  # active turn -> running
    client = TestClient(_app(monkeypatch), headers=HDR)
    assert api_audit.REPLY_WAIT_CAP_SECONDS == 60.0
    r = client.get(f"/api/chat/replies/{ids[0]}?wait=0.2").json()
    assert r["done"] is False and r["timed_out"] is True and r["content"] is None
    assert r["partial_content"] == "partial"


# ------------------------------------------------------------------ 2. message aliases
def test_message_aliases_and_truncate_confirm(db, monkeypatch):
    from distr.core.db import Chat

    root, ids = _seed(db, responses=("r0", "r1", "r2"))
    client = TestClient(_app(monkeypatch), headers=HDR)
    assert client.get(f"/api/chat/{root}/messages/{ids[0]}").json()["response"] == "r0"
    assert client.patch(f"/api/chat/{root}/messages/{ids[0]}", json={"response": "x"}).json()["response"] == "x"
    assert client.delete(f"/api/chat/{root}/messages/{ids[2]}").json()["deleted"] == 1
    no = client.post(f"/api/chat/{root}/messages/{ids[0]}/truncate-after")
    assert no.status_code == 400 and "confirm=true" in no.text
    with db() as s:
        assert s.get(Chat, ids[1]) is not None
    yes = client.post(f"/api/chat/{root}/messages/{ids[0]}/truncate-after?confirm=true")
    assert yes.status_code == 200 and yes.json()["deleted"] == 1
    with db() as s:
        assert s.get(Chat, ids[1]) is None and s.get(Chat, ids[0]) is not None


# ------------------------------------------------------------------ 3. current chat
def test_get_and_put_current_chat_broadcasts(db, monkeypatch):
    import distr.gui.web.routes.chat as chat_routes

    root, _ = _seed(db)
    other, _ = _seed(db)
    loaded, events = [], []
    r = APIRouter()

    @r.post("/chats/{chat_id}/load-in-agent")
    async def fake_load(chat_id: int):
        loaded.append(chat_id)
        return {"loaded": True}

    async def fake_broadcast(payload):
        events.append(payload)
        return 1

    monkeypatch.setattr(chat_routes, "broadcast_chat_event", fake_broadcast)
    client = TestClient(_app(monkeypatch, r, current_chat=root), headers=HDR)
    cur = client.get("/api/chat/current").json()
    assert cur["chat_id"] == root and cur["chat"]["title"] == "Root"
    res = client.put("/api/chat/current", json={"chat_id": other}).json()
    assert loaded == [other] and res["previous_chat_id"] == root and res["changed"] is True
    assert {e["chat_id"] for e in events} == {root, other}
    assert all(e["type"] == "agent_chat_changed" and e["agent_current_chat_id"] == other for e in events)
    assert client.put("/api/chat/current", json={}).status_code == 422


# ------------------------------------------------------------------ 4. skills run
def test_skill_run_oracle_one_shot(db, monkeypatch, tmp_path):
    import distr.core.skills.catalog as catalog
    import distr.core.workflow.development_threads as dt

    root, _ = _seed(db)
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("---\nname: demo\n---\nDo the demo thing.", encoding="utf-8")
    monkeypatch.setattr(catalog, "filter_known_skill_ids", lambda ids: [i for i in ids if i == "demo"])
    monkeypatch.setattr(catalog, "skill_file_for_id", lambda sid: skill_md)
    monkeypatch.setattr(dt, "is_development_thread", lambda s, c: False)
    sent = {}
    r = APIRouter()

    @r.post("/chats/{chat_id}/send-to-agent")
    async def fake_send(chat_id: int, request: Request):
        sent.update(chat_id=chat_id, body=await request.json())
        return {"sent": True, "turn_id": None, "reply_id": f"{chat_id}.0.deadbeef"}

    client = TestClient(_app(monkeypatch, r, current_chat=root), headers=HDR)
    assert client.post("/api/skills/missing/run", json={}).status_code == 404
    res = client.post("/api/skills/demo/run", json={"input": "make it so"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "oracle_one_shot" and body["reply_id"] == f"{root}.0.deadbeef"
    assert sent["chat_id"] == root and sent["body"]["speak"] is False
    assert "Do the demo thing." in sent["body"]["message"] and "make it so" in sent["body"]["message"]


def test_skill_run_needs_a_chat_and_routes_dev_threads(db, monkeypatch):
    import distr.core.skills.catalog as catalog
    import distr.core.workflow.development_threads as dt

    root, _ = _seed(db)
    monkeypatch.setattr(catalog, "filter_known_skill_ids", lambda ids: list(ids))
    seen = {}
    r = APIRouter()

    @r.post("/workflows/studio/tasks/{chat_id}/messages")
    async def fake(chat_id: int, body: dict, wait: bool = False):
        seen.update(chat_id=chat_id, body=body, wait=wait)
        return {"success": True, "turn_id": 12}

    client = TestClient(_app(monkeypatch, r, current_chat=None), headers=HDR)
    assert client.post("/api/skills/demo/run", json={}).status_code == 409
    monkeypatch.setattr(dt, "is_development_thread", lambda s, c: True)
    body = client.post("/api/skills/demo/run", json={"chat_id": root, "input": "go"}).json()
    assert body["mode"] == "development_thread" and body["reply_id"] == "12"
    assert seen["body"] == {"message": "go", "skill_ids": ["demo"]}


# ------------------------------------------------------------------ 5. settings
def test_settings_all_masks_secrets(monkeypatch):
    from distr.gui.web.routes import api_extensions

    monkeypatch.setattr(api_extensions, "SETTINGS_SECTIONS", ("audio", "thirdparty"))
    r = APIRouter()

    @r.get("/audio")
    async def a():
        return {"input_device": "Mic"}

    @r.get("/thirdparty")
    async def t():
        return {"openai_key": "sk-secret-123456", "openai_enabled": True}

    client = TestClient(_app(monkeypatch, r), headers=HDR)
    body = client.get("/api/settings/all").json()
    assert body["sections"]["audio"]["input_device"] == "Mic"
    assert body["sections"]["thirdparty"]["openai_key"] == "********" and body["secrets_masked"] is True
    assert "sk-secret" not in json.dumps(body)


def test_patch_llms_never_wipes_project_cli_tiers(monkeypatch):
    import distr.core.settings as core_settings
    from distr.gui.web.routes import api_audit

    raw = {"project_cli_low_backend": "cursor", "project_cli_low_model": "auto",
           "project_cli_high_backend": "claude", "project_cli_high_model": "opus",
           "project_cli_medium_model_provider": "openrouter", "conversational_llm_model": "a"}
    monkeypatch.setattr(core_settings, "load_settings_from_db", lambda: dict(raw))
    monkeypatch.setattr(core_settings, "save_settings_to_db", lambda s: (raw.clear(), raw.update(s)))
    r = APIRouter()

    @r.get("/llms")
    async def get_llms():
        return {"conversational_model": raw["conversational_llm_model"], "project_cli_high_backend": "codex"}

    @r.post("/llms")
    async def post_llms(request: Request):
        data = await request.json()
        # Simulate a buggy full-save that clobbers tiers and drops extra keys.
        raw["conversational_llm_model"] = data["conversational_model"]
        raw["project_cli_high_backend"] = data["project_cli_high_backend"]
        raw["project_cli_low_model"] = ""
        raw.pop("project_cli_medium_model_provider", None)
        return {"success": True}

    client = TestClient(_app(monkeypatch, r), headers=HDR)
    res = client.patch("/api/settings/llms", json={"conversational_model": "b"})
    assert res.status_code == 200, res.text
    assert raw["conversational_llm_model"] == "b"
    assert raw["project_cli_high_backend"] == "claude" and raw["project_cli_low_model"] == "auto"
    assert raw["project_cli_medium_model_provider"] == "openrouter"
    assert set(res.json()["project_cli_restored"]) == {"project_cli_high_backend", "project_cli_low_model",
                                                       "project_cli_medium_model_provider"}
    blank = client.patch("/api/settings/llms", json={"project_cli_low_backend": ""})
    assert blank.status_code == 422
    assert client.patch("/api/settings/llms", json={"bogus": 1}).status_code == 400
    # An explicit tier change is honoured.
    ok = client.patch("/api/settings/llms", json={"project_cli_high_backend": "cursor"})
    assert ok.status_code == 200 and raw["project_cli_high_backend"] == "cursor"


# ------------------------------------------------------------------ 6. kanban retirement
def test_kanban_addresses_return_410(monkeypatch):
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", TOKEN)
    from distr.gui.web.server import create_app

    client = TestClient(create_app(), follow_redirects=False)
    for path, replacement in [("/api/kanban/tickets/5", "/api/tickets/tickets/5"), ("/api/kanban", "/api/tickets")]:
        r = client.get(path, headers=HDR)
        assert r.status_code == 410 and r.json()["replacement"] == replacement
    page = client.get("/kanban")
    assert page.status_code == 410 and page.json()["replacement"] == "/development/"
    # Unrelated paths that merely start with "kanban" are untouched.
    assert client.get("/api/kanbanx", headers=HDR).status_code != 410
    # /tickets stays a compatibility redirect (out of scope).
    assert client.get("/tickets/").status_code == 302
