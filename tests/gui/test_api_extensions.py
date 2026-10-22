"""Tests for the 22 Oct 2026 API gap fixes (auth guards, turns, messages, settings, skills)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

TOKEN = "test-internal-token-0123456789abcdef0123456789"
HDR = {"X-DecisionsAI-Internal-Token": TOKEN}


# ---------------------------------------------------------------- DB fixture
@pytest.fixture()
def db(monkeypatch):
    from distr.core import db as dbmod
    from distr.core.db import Chat, ChatTurnEvent

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    dbmod.Base.metadata.create_all(engine, tables=[Chat.__table__, ChatTurnEvent.__table__])
    factory = sessionmaker(bind=engine)

    def _get_session():
        return factory()

    monkeypatch.setattr(dbmod, "get_session", _get_session)
    import distr.core.chat_turns as ct
    monkeypatch.setattr(ct, "get_session", _get_session, raising=False)
    return factory


def _seed(factory, *, responses=("hi there", None), active=None):
    from distr.core.db import Chat

    now = datetime(2026, 10, 22, 12, 0, 0)
    with factory() as s:
        root = Chat(title="Root", input="", response="", created_date=now, modified_date=now)
        s.add(root)
        s.commit()
        ids = []
        for i, resp in enumerate(responses):
            row = Chat(parent_id=root.id, input=f"q{i}", response=resp,
                       created_date=now + timedelta(minutes=i + 1), modified_date=now)
            s.add(row)
            s.commit()
            ids.append(row.id)
        if active is not None:
            root.params = json.dumps({"active_turn_chat_row_id": ids[active]})
            s.commit()
        return root.id, ids


def _ext_client(monkeypatch, current_chat=None):
    from distr.gui.web.routes import api_extensions as ext
    from distr.core.chat import ChatService

    monkeypatch.setattr(ChatService, "get_current_chat_id", staticmethod(lambda: current_chat))
    emitted = []
    monkeypatch.setattr(ext, "_reload_oracle_if_current",
                        lambda root_id: (emitted.append(root_id) or True) if current_chat == root_id else False)
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(ext.create_router(), prefix="/api")
    return TestClient(app, headers=HDR), emitted


# ---------------------------------------------------------------- 1. security
@pytest.fixture()
def full_app(monkeypatch):
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", TOKEN)
    from distr.gui.web.server import create_app

    return create_app()


def test_board_api_writes_require_internal_token(full_app):
    client = TestClient(full_app)
    r = client.post("/board/api/columns", json={"name": "x"})
    assert r.status_code == 401
    # GET stays open (read-only legacy board view).
    assert client.get("/board/api/board").status_code != 401
    # With the token the request reaches the route (it may fail for other reasons, but not auth).
    # No project bound in tests -> the route's own "Project ID not specified" error
    # (wrapped as 500 by the legacy handler): auth passed, nothing was written.
    r2 = client.post("/board/api/columns", json={"name": "x"}, headers=HDR)
    assert r2.status_code in (400, 500) and "Project ID" in r2.text


def test_board_page_injects_xhr_token_shim():
    from distr.gui.web.routes.board import _inject_internal_token_shim

    html = _inject_internal_token_shim("<html><head></head><body></body></html>", TOKEN)
    assert "X-DecisionsAI-Internal-Token" in html and TOKEN in html
    assert html.index("<script>") < html.index("</head>")
    assert _inject_internal_token_shim("<head></head>", "") == "<head></head>"


def test_slack_hook_stays_signature_guarded_not_token_guarded(full_app, monkeypatch):
    import distr.gui.web.routes.integrations_hooks as hooks

    monkeypatch.setattr(hooks, "_slack_signing_secret", lambda: "s3cret")
    r = TestClient(full_app).post("/hooks/slack/events", content=b"{}",
                                  headers={"X-Slack-Request-Timestamp": "1", "X-Slack-Signature": "v0=bad"})
    assert r.status_code == 401
    assert "Slack signature" in r.text


def test_speak_spoken_has_own_guard(full_app, monkeypatch):
    from distr.core import read_response as rr

    calls = []
    monkeypatch.setattr(rr, "speak_spoken_text", lambda text: calls.append(text) or False)
    remote = TestClient(full_app, client=("10.0.0.9", 5000))
    assert remote.post("/api/chats/speak-spoken", json={"text": "x"}, headers=HDR).status_code == 403
    local = TestClient(full_app, client=("127.0.0.1", 5000))
    assert local.post("/api/chats/speak-spoken", json={"text": "x"}).status_code == 401
    assert calls == []
    ok = local.post("/api/chats/speak-spoken", json={"text": "x"}, headers=HDR)
    assert ok.status_code == 200
    assert calls == ["x"]


def test_chats_current_is_not_captured_by_chat_id_route(full_app, monkeypatch):
    from distr.core.chat import ChatService

    monkeypatch.setattr(ChatService, "get_current_chat_id", staticmethod(lambda: None))
    assert TestClient(full_app).get("/api/chats/current").status_code == 401
    r = TestClient(full_app).get("/api/chats/current", headers=HDR)
    assert r.status_code == 200
    assert r.json()["chat_id"] is None


# ---------------------------------------------------------------- 2. turns
def test_turn_status_states(db):
    from distr.gui.web.routes.api_extensions import turn_status
    from distr.core.db import ChatTurnEvent

    root, (done_id, pending_id) = _seed(db, responses=("hello", None), active=1)
    st = turn_status(root, done_id)
    assert st["status"] == "completed" and st["assistant_text"] == "hello" and st["done"]
    st2 = turn_status(root, pending_id)
    assert st2["status"] == "pending" and not st2["done"] and st2["active"]
    with db() as s:
        s.add(ChatTurnEvent(chat_id=root, turn_id=pending_id, sequence=1, event_id="e1",
                            event_type="turn_failed", status="failed", title="Request failed", summary="boom"))
        s.commit()
    st3 = turn_status(root, pending_id)
    assert st3["status"] == "failed" and st3["error"] == "boom"
    assert turn_status(root, 999999) is None


def test_get_turn_endpoint_and_wait_timeout(db, monkeypatch):
    from distr.gui.web.routes import api_extensions as ext

    monkeypatch.setattr(ext, "POLL_INTERVAL_SECONDS", 0.05)
    root, (done_id, pending_id) = _seed(db, active=1)
    client, _ = _ext_client(monkeypatch)
    assert client.get(f"/api/chats/{root}/turns/{done_id}").json()["assistant_text"] == "hi there"
    r = client.get(f"/api/chats/{root}/turns/{pending_id}?wait=true&timeout=0.2").json()
    assert r["timed_out"] is True and r["status"] == "pending"
    assert client.get(f"/api/chats/{root}/turns/424242").status_code == 404
    listing = client.get(f"/api/chats/{root}/turns?after_row_id={done_id}").json()
    assert [t["turn_id"] for t in listing["turns"]] == [pending_id]


def test_clamp_timeout_caps_at_300():
    from distr.gui.web.routes.api_extensions import clamp_timeout

    assert clamp_timeout(9999) == 300.0
    assert clamp_timeout(-5) == 0.0
    assert clamp_timeout(None) == 60.0


def test_turn_response_fields_resolves_new_row_and_waits_for_reply(db, monkeypatch):
    from distr.gui.web.routes import api_extensions as ext
    from distr.core.db import Chat

    monkeypatch.setattr(ext, "POLL_INTERVAL_SECONDS", 0.05)
    root, ids = _seed(db, responses=("old",))
    baseline = ext.max_thread_row_id(root)

    async def scenario():
        async def agent():
            await asyncio.sleep(0.1)
            with db() as s:
                row = Chat(parent_id=root, input="ping", response=None,
                           created_date=datetime(2026, 10, 22, 13, 0), modified_date=datetime(2026, 10, 22, 13, 0))
                s.add(row); s.commit()
                rid = row.id
            await asyncio.sleep(0.1)
            with db() as s:
                s.get(Chat, rid).response = "pong"; s.commit()
        task = asyncio.create_task(agent())
        out = await ext.turn_response_fields(root, turn_id=None, after_row_id=baseline, message="ping", wait=True, timeout=5)
        await task
        return out

    out = asyncio.run(scenario())
    assert out["turn_id"] > baseline
    assert out["turn_status"]["assistant_text"] == "pong"
    assert out["turn_status"]["done"] is True and out["turn_status"]["timed_out"] is False


def test_turn_response_fields_without_wait_does_not_block(db):
    import time
    from distr.gui.web.routes import api_extensions as ext

    root, _ = _seed(db, responses=("old",))
    t0 = time.monotonic()
    out = asyncio.run(ext.turn_response_fields(root, turn_id=None, after_row_id=ext.max_thread_row_id(root), message="x"))
    assert time.monotonic() - t0 < 1.0
    assert out["turn_id"] is None and out["turn_pending"] is True


def test_latest_turn_summary_for_ws_payload(db, monkeypatch):
    import distr.gui.web.routes.chat as chat_routes

    monkeypatch.setattr(chat_routes, "get_session", db)
    root, ids = _seed(db, responses=("a", "final answer"))
    summary = chat_routes._latest_turn_summary(root)
    assert summary["turn_id"] == ids[-1]
    assert summary["assistant_text"] == "final answer" and summary["final"] is True


# ---------------------------------------------------------------- 3. messages
def test_message_get_patch_delete_truncate(db, monkeypatch):
    from distr.core.db import Chat, ChatTurnEvent

    root, ids = _seed(db, responses=("r0", "r1", "r2", "r3"))
    with db() as s:
        s.add(ChatTurnEvent(chat_id=root, turn_id=ids[1], sequence=1, event_id="x1",
                            event_type="turn_completed", status="completed", title="t", summary=""))
        s.commit()
    client, reloaded = _ext_client(monkeypatch, current_chat=root)

    assert client.get(f"/api/chats/{root}/messages/{ids[0]}").json()["response"] == "r0"
    assert len(client.get(f"/api/chats/{root}/messages").json()["messages"]) == 5

    r = client.patch(f"/api/chats/{root}/messages/{ids[0]}", json={"response": "edited"})
    assert r.status_code == 200 and r.json()["response"] == "edited" and r.json()["oracle_reloaded"] is True
    assert client.patch(f"/api/chats/{root}/messages/{ids[0]}", json={"bogus": 1}).status_code == 422
    assert client.patch(f"/api/chats/{root}/messages/{ids[0]}", json={}).status_code == 400

    r = client.delete(f"/api/chats/{root}/messages/{ids[1]}")
    assert r.status_code == 200 and r.json()["deleted"] == 1
    with db() as s:
        assert s.get(Chat, ids[1]) is None
        assert s.query(ChatTurnEvent).filter(ChatTurnEvent.turn_id == ids[1]).count() == 0

    assert client.delete(f"/api/chats/{root}/messages/{root}").status_code == 409
    assert client.post(f"/api/chats/{root}/messages/{ids[0]}/truncate-after").status_code == 400
    r = client.post(f"/api/chats/{root}/messages/{ids[0]}/truncate-after?confirm=true")
    assert r.json()["deleted"] == 2
    with db() as s:
        assert sorted(c.id for c in s.query(Chat).filter(Chat.parent_id == root)) == [ids[0]]
    assert reloaded == [root, root, root]


def test_message_ops_reject_running_turn_and_foreign_rows(db, monkeypatch):
    root, ids = _seed(db, responses=("r0", None), active=1)
    other_root, other_ids = _seed(db, responses=("z",))
    client, reloaded = _ext_client(monkeypatch, current_chat=None)
    assert client.patch(f"/api/chats/{root}/messages/{ids[1]}", json={"input": "x"}).status_code == 409
    assert client.post(f"/api/chats/{root}/messages/{ids[0]}/truncate-after?confirm=true").status_code == 409
    assert client.get(f"/api/chats/{root}/messages/{other_ids[0]}").status_code == 404
    r = client.patch(f"/api/chats/{root}/messages/{ids[0]}", json={"is_hidden": True})
    assert r.status_code == 200 and r.json()["oracle_reloaded"] is False
    assert reloaded == []


def test_reload_oracle_only_for_current_chat(monkeypatch):
    from distr.gui.web.routes import api_extensions as ext
    from distr.core.chat import ChatService
    import distr.core.signals as signals
    from types import SimpleNamespace

    sent = []
    monkeypatch.setattr(ChatService, "get_current_chat_id", staticmethod(lambda: 7))
    fake = SimpleNamespace(web_load_chat_in_agent_requested=SimpleNamespace(emit=lambda cid: sent.append(cid)))
    monkeypatch.setattr(signals, "signal_manager", fake)
    assert ext._reload_oracle_if_current(8) is False
    assert ext._reload_oracle_if_current(7) is True
    assert sent == [7]


# ---------------------------------------------------------------- 4/5. current + settings
def test_current_chat_endpoint(db, monkeypatch):
    root, _ = _seed(db)
    client, _ = _ext_client(monkeypatch, current_chat=root)
    body = client.get("/api/chats/current").json()
    assert body["chat_id"] == root and body["chat"]["title"] == "Root"


def test_secret_masking():
    from distr.gui.web.routes.api_extensions import mask_secrets

    out = mask_secrets({"openai_key": "sk-live", "dictation_hotkey_key": "D", "web_hotkey_chat_key": "C",
                        "servers": [{"env": {"GITHUB_TOKEN": "ghp"}, "headers": {"Authorization": "Bearer z"}}],
                        "empty_key": "", "ollama_url": "http://x"})
    assert out["openai_key"] == "********" and out["empty_key"] == ""
    assert out["dictation_hotkey_key"] == "D" and out["web_hotkey_chat_key"] == "C"
    assert out["servers"][0]["env"]["GITHUB_TOKEN"] == "********"
    assert out["servers"][0]["headers"]["Authorization"] == "********"
    assert out["ollama_url"] == "http://x"


def _settings_app(monkeypatch, store):
    """Fake section routes + the real extension router, behind the real token middleware semantics."""
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", TOKEN)
    from distr.gui.web.routes import api_extensions as ext
    from distr.gui.web.routes import api_audit

    monkeypatch.setattr(api_audit, "project_cli_snapshot", lambda: {})
    monkeypatch.setattr(api_audit, "restore_project_cli", lambda before, keys: [])
    app = FastAPI()
    r = APIRouter()

    @r.get("/audio")
    async def get_audio():
        return dict(store["audio"])

    @r.post("/audio")
    async def post_audio(request: Request):
        assert request.headers.get("X-DecisionsAI-Internal-Token") == TOKEN
        store["audio"] = await request.json()
        store["posts"] += 1
        return {"success": True}

    @r.get("/thirdparty")
    async def get_tp():
        return {"openai_key": "sk-123456789", "openai_enabled": True}

    app.include_router(ext.create_router(), prefix="/api")
    app.include_router(r, prefix="/api")
    return TestClient(app, client=("127.0.0.1", 1), headers=HDR)


def test_settings_get_all_masks_and_patch_merges(monkeypatch):
    store = {"audio": {"input_device": "Mic", "output_device": "Spk", "remember_audio_settings": False,
                       "locked_output": None, "locked_input": None}, "posts": 0}
    client = _settings_app(monkeypatch, store)
    allv = client.get("/api/settings").json()
    assert allv["sections"]["audio"]["input_device"] == "Mic"
    assert allv["sections"]["thirdparty"]["openai_key"] == "********"
    assert "sk-123456789" not in json.dumps(allv)

    r = client.patch("/api/settings/audio", json={"remember_audio_settings": True})
    assert r.status_code == 200, r.text
    assert store["posts"] == 1
    assert store["audio"]["remember_audio_settings"] is True and store["audio"]["input_device"] == "Mic"

    bad = client.patch("/api/settings/audio", json={"nope": 1})
    assert bad.status_code == 400 and "nope" in bad.text
    assert client.patch("/api/settings/thirdparty", json={"openai_key": "x"}).status_code == 405
    assert client.patch("/api/settings/unknown", json={"a": 1}).status_code == 404
    assert client.patch("/api/settings/audio", json={"remember_audio_settings": "notabool"}).status_code == 422
    assert store["posts"] == 1


def test_settings_schema_lists_sections(monkeypatch):
    from distr.gui.web.routes import api_extensions as ext

    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", TOKEN)
    app = FastAPI(); app.include_router(ext.create_router(), prefix="/api")
    body = TestClient(app, headers=HDR).get("/api/settings/schema").json()["sections"]
    assert body["general"]["writable"] and "voice_provider" in body["general"]["keys"]
    assert body["thirdparty"]["writable"] is False
    assert set(ext.SETTINGS_SECTIONS) == set(body)


# ---------------------------------------------------------------- 6. skills
def test_skill_invoke_rejects_unknown_and_oracle_chats(db, monkeypatch):
    import distr.core.skills.catalog as catalog
    import distr.core.workflow.development_threads as dt

    root, _ = _seed(db)
    client, _ = _ext_client(monkeypatch)
    monkeypatch.setattr(catalog, "filter_known_skill_ids", lambda ids: [])
    assert client.post("/api/skills/nope/invoke", json={"chat_id": root}).status_code == 404
    monkeypatch.setattr(catalog, "filter_known_skill_ids", lambda ids: list(ids))
    monkeypatch.setattr(dt, "is_development_thread", lambda s, c: False)
    assert client.post("/api/skills/decisions-playwright/invoke", json={"chat_id": root}).status_code == 409


def test_skill_invoke_routes_through_development_messages(db, monkeypatch):
    import distr.core.skills.catalog as catalog
    import distr.core.workflow.development_threads as dt
    from distr.gui.web.routes import api_extensions as ext

    root, _ = _seed(db)
    monkeypatch.setenv("DECISIONSAI_INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setattr(catalog, "filter_known_skill_ids", lambda ids: list(ids))
    monkeypatch.setattr(dt, "is_development_thread", lambda s, c: True)
    seen = {}
    app = FastAPI()
    r = APIRouter()

    @r.post("/workflows/studio/tasks/{chat_id}/messages")
    async def fake(chat_id: int, body: dict, wait: bool = False):
        seen.update(chat_id=chat_id, body=body, wait=wait)
        return {"success": True, "turn_id": 5}

    app.include_router(ext.create_router(), prefix="/api")
    app.include_router(r, prefix="/api")
    res = TestClient(app, headers=HDR).post(f"/api/skills/decisions-playwright/invoke?wait=true&timeout=5", json={"chat_id": root, "message": "go"})
    assert res.status_code == 200 and res.json()["turn_id"] == 5
    assert seen["body"] == {"message": "go", "skill_ids": ["decisions-playwright"]} and seen["wait"] is True


# ---------------------------------------------------------------- 2b. send endpoints
def test_oracle_send_to_agent_returns_turn_id_and_waits_for_reply(db, monkeypatch):
    import distr.core.signals as signals
    import distr.gui.web.routes.chat as chat_routes
    from distr.gui.web.routes import api_extensions as ext
    from distr.core.db import Chat
    from types import SimpleNamespace

    monkeypatch.setattr(ext, "POLL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(chat_routes, "get_session", db)
    monkeypatch.setattr(chat_routes, "is_development_thread", lambda s, c: False)
    root, _ = _seed(db, responses=("old",))

    def fake_emit(chat_id, message, speak, provider, model, options):
        # Simulates the agent process persisting the user row and replying.
        with db() as s:
            row = Chat(parent_id=chat_id, input=message, response="agent reply",
                       created_date=datetime(2026, 10, 22, 14, 0), modified_date=datetime(2026, 10, 22, 14, 0))
            s.add(row); s.commit()

    fake = SimpleNamespace(web_send_to_agent_requested=SimpleNamespace(emit=fake_emit))
    monkeypatch.setattr(signals, "signal_manager", fake)
    app = FastAPI(); app.include_router(chat_routes.create_routes(__import__("pathlib").Path(".")), prefix="/api")
    client = TestClient(app)
    r = client.post(f"/api/chats/{root}/send-to-agent?wait=true&timeout=5", json={"message": "hello agent", "speak": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent"] is True and body["turn_id"]
    assert body["turn_status"]["assistant_text"] == "agent reply" and body["turn_status"]["done"] is True
    # Without wait the response still carries the (already persisted) turn id when available.
    r2 = client.post(f"/api/chats/{root}/send-to-agent", json={"message": "second", "speak": False}).json()
    assert r2["sent"] is True and "turn_id" in r2 and "after_row_id" in r2


def test_development_message_returns_turn_id(db, monkeypatch):
    import distr.core.chat as core_chat
    import distr.core.skills.catalog as catalog
    import distr.core.workflow.development_threads as dt
    import distr.core.workflow.development_harness as harness
    import distr.core.workflow.development_control as control
    import distr.gui.web.workflow_events as wev
    from distr.gui.web.routes.development import threads

    monkeypatch.setattr(core_chat, "get_session", db)
    monkeypatch.setattr(catalog, "filter_known_skill_ids", lambda ids: list(ids))
    monkeypatch.setattr(dt, "development_thread_metadata", lambda chat: {"source_type": "studio"})
    monkeypatch.setattr(core_chat, "is_duplicate_pending_input", lambda *a, **k: False)
    monkeypatch.setattr(core_chat, "record_chat_audit_event", lambda **k: None, raising=False)
    dispatched = []
    monkeypatch.setattr(harness, "dispatch_development_prompt", lambda *a, **k: dispatched.append(a) or {"status": "started"})
    monkeypatch.setattr(control, "resume_thread_time", lambda cid: None)
    monkeypatch.setattr(wev, "increment_workflow_updated", lambda: None)
    root, _ = _seed(db, responses=())
    app = FastAPI(); router = APIRouter(); threads.register_routes(router, None)
    app.include_router(router, prefix="/api")
    r = TestClient(app).post(f"/api/workflows/studio/tasks/{root}/messages", json={"message": "build it"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["started"] is True and isinstance(body["turn_id"], int)
    assert body["turn_status"]["input"] == "build it" and body["turn_status"]["done"] is False
    assert dispatched
