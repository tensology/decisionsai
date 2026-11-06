"""Multi-chat per workspace (N3 Option A), clear/compact, image attachments (item 11). Temp DBs, fake values."""
import io
import json

import pytest

from distr.core.db import Chat
from distr.core.planning import chats as ch, conversation as c
from tests.core.test_plan_conversation import respond, send, store  # noqa: F401  (fixture)


def _reply(monkeypatch, text="ok"):
    return respond(monkeypatch, {"reply": text, "edits": []})


def test_legacy_chat_is_chat_one_and_chats_are_isolated(store, monkeypatch):
    calls = _reply(monkeypatch, "first")
    send()                                            # legacy single-chat path, unchanged
    legacy = c.get_conversation(1)
    assert len(legacy["chats"]) == 1 and legacy["chat"]["id"] == legacy["chat_id"]
    created = ch.create_chat(1)
    second = created["chat_id"]
    assert [row["id"] for row in created["chats"]] == [legacy["chat_id"], second]
    assert created["chats"][1]["title"] == "Chat 2" and created["chats"][1]["provider"] == "ollama"   # server default route (settings), part 2
    assert c.get_conversation(1)["chat_id"] == second                        # newest live chat is the default
    assert c.get_conversation(1, second)["messages"] == []
    c.send_message(1, message="Second chat", provider="openai", model_name="chosen", chat_id=second)
    assert [m["content"] for m in c.get_conversation(1, legacy["chat_id"])["messages"]] == ["Improve requirements", "first"]
    assert [m["content"] for m in c.get_conversation(1, second)["messages"]][0] == "Second chat"
    # History sent for chat 2 never contains chat 1's turns.
    assert "Improve requirements" not in json.dumps(calls[-1][2][1:-1])
    with pytest.raises(LookupError):
        c.get_conversation(2, second)                                         # another workspace's chat


def test_rename_archive_restore_with_revision_guard(store, monkeypatch):
    _reply(monkeypatch)
    send()
    chat_id = c.get_conversation(1)["chat_id"]
    row = ch.rename_chat(1, chat_id, title="  Pricing   flow ", expected_revision=0)["chats"][0]
    assert row["title"] == "Pricing flow" and row["revision"] == 1
    with pytest.raises(ValueError):
        ch.rename_chat(1, chat_id, title="Stale", expected_revision=0)
    with pytest.raises(ValueError):
        ch.rename_chat(1, chat_id, title=" ", expected_revision=1)
    ch.archive_chat(1, chat_id, expected_revision=1)
    with store[0]() as db:
        assert db.get(Chat, chat_id).is_archived and db.query(Chat).filter_by(parent_id=chat_id).count() == 1
    with pytest.raises(ValueError, match="archived"):
        c.send_message(1, message="x", provider="openai", model_name="chosen", chat_id=chat_id)
    # No live chat: an unaddressed message starts a fresh chat instead of reviving the archived one.
    fresh = c.send_message(1, message="new start", provider="openai", model_name="chosen")
    assert fresh["chat_id"] != chat_id and fresh["messages"][0]["content"] == "new start"
    restored = ch.restore_chat(1, chat_id, expected_revision=2)
    assert not next(r for r in restored["chats"] if r["id"] == chat_id)["archived"]
    assert len(c.get_conversation(1, chat_id)["messages"]) == 2              # no data loss


def test_clear_archives_and_starts_fresh_and_is_undoable(store, monkeypatch):
    _reply(monkeypatch)
    send()
    old = c.get_conversation(1)["chat"]
    result = ch.clear_chat(1, old["id"], expected_revision=old["revision"])
    assert result["archived_chat_id"] == old["id"] and result["chat_id"] != old["id"]
    assert c.get_conversation(1)["chat_id"] == result["chat_id"] and c.get_conversation(1)["messages"] == []
    undo = ch.restore_chat(1, old["id"])
    assert len(c.get_conversation(1, old["id"])["messages"]) == 2 and len(undo["chats"]) == 2


def test_compact_summarises_and_history_uses_summary(store, monkeypatch):
    calls = _reply(monkeypatch, "Noted the checkout flow")
    send()
    chat = c.get_conversation(1)["chat"]
    ch.compact_chat(1, chat["id"], expected_revision=chat["revision"])
    convo = c.get_conversation(1)
    assert all(m.get("compacted") for m in convo["messages"]) and "You: Improve requirements" in convo["chat"]["summary"]
    with pytest.raises(ValueError, match="Nothing to compact"):
        ch.compact_chat(1, chat["id"])
    send()
    system, history = calls[-1][2][0]["content"], calls[-1][2][1:-1]
    assert "Earlier conversation summary" in system and "Noted the checkout flow" in system
    assert history == []                                                   # compacted turns are not replayed


def test_auto_compact_keeps_recent_turns(store, monkeypatch):
    _reply(monkeypatch, "r" * 50)
    for _ in range(6):
        send()
    root = c.get_conversation(1)["chat_id"]
    with store[0]() as db:
        last = db.query(Chat).filter_by(parent_id=root).order_by(Chat.id.desc()).first().id
    text = ch.auto_compact(1, root, before_turn_id=last + 1, limit=10)
    assert text and text.count("- You:") == 2                              # 6 turns, newest 4 kept verbatim
    assert sum(1 for m in c.get_conversation(1)["messages"] if not m.get("compacted")) == 8


def test_one_running_turn_per_workspace(store, monkeypatch):
    _reply(monkeypatch)
    send()
    first = c.get_conversation(1)["chat_id"]
    second = ch.create_chat(1)["chat_id"]
    with store[0]() as db:
        root = db.get(Chat, first)
        root.params = json.dumps({**json.loads(root.params), "plan_status": "running", "plan_owner": {"pid": 1}})
        db.commit()
    monkeypatch.setattr(c, "owner_is_gone", lambda owner: False)
    monkeypatch.setattr("distr.core.planning.ownership.owner_is_gone", lambda owner: False)
    with pytest.raises(ValueError, match="already running"):
        c.send_message(1, message="x", provider="openai", model_name="chosen", chat_id=second)
    with pytest.raises(ValueError, match="running turn"):
        ch.archive_chat(1, first)


def test_user_messages_carry_their_image_ids(store, monkeypatch):
    _reply(monkeypatch)
    store[1].list_assets = lambda wid: [{"id": 7, "name": "shot.png", "mime_type": "image/png", "url": "/x"}]
    send(attachments=[7])
    user = c.get_conversation(1)["messages"][0]
    assert user["attachment_ids"] == [7]


# ---- item 11: image validation (real assets module, temp upload root) -------------------------------
def _png():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_signature_and_size_rules():
    from distr.core.planning import assets
    assert assets._image_signature_ok(".png", _png())
    assert not assets._image_signature_ok(".png", b"GIF89a....")
    assert assets._image_signature_ok(".webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert not assets._image_signature_ok(".jpg", b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="not a PNG"):
        assets.add_asset(1, name="fake.png", data=b"<html>not an image</html>")
    with pytest.raises(ValueError, match="10 MB"):
        assets.add_asset(1, name="huge.png", data=b"\x89PNG\r\n\x1a\n" + b"\0" * (assets.MAX_IMAGE_BYTES + 1))


@pytest.fixture
def http(monkeypatch, tmp_path, request):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.planning import assets, service
    from distr.gui.web.routes.development.planning import register_routes
    workspace_db = request.getfixturevalue("plan_workspace_db")
    monkeypatch.setattr(c, "get_session", service.get_session)
    monkeypatch.setattr(assets, "get_session", service.get_session)
    monkeypatch.setattr(assets, "UPLOAD_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(c, "load_settings_from_db", lambda: {})
    monkeypatch.setattr(c.runtime, "create_stream", lambda provider, model, messages, settings, **scope:
                        c.llm_factory.create_stream(provider, model, messages, settings))
    plan = service.ensure_workspace(board_key="decisions:chat1", board_provider="decisions",
                                    board_name="Chat plan", project_id=workspace_db["project_id"])
    app = FastAPI()
    router = APIRouter(prefix="/api")
    register_routes(router, None)
    app.include_router(router)
    with TestClient(app) as client:
        yield client, f"/api/workflows/studio/plan-workspaces/{plan['id']}"


from tests.core.test_planning_workspace import plan_workspace_db  # noqa: E402,F401


def test_http_multichat_flow(http, monkeypatch):
    client, base = http
    calls = []
    def stream(provider, model, messages, settings):
        calls.append(messages)
        yield json.dumps({"reply": "seen", "edits": []})
    monkeypatch.setattr(c.llm_factory, "create_stream", stream)
    image = client.post(base + "/attachments", files={"file": ("screen.png", _png(), "image/png")}).json()
    bad = client.post(base + "/attachments", files={"file": ("evil.png", b"MZ not an image", "image/png")})
    assert bad.status_code == 409
    first = client.post(base + "/messages", json={"message": "Look at this", "provider": "ollama", "model_name": "m",
                                                  "attachments": [image["id"]]}).json()
    assert first["messages"][0]["attachment_ids"] == [image["id"]]
    assert f"Image {image['id']} (screen.png)" in json.dumps(calls[0])
    assert "never invent image numbers" in calls[0][0]["content"]
    made = client.post(base + "/chats", json={"title": "Second"}).json()
    assert [row["title"] for row in made["chats"]][1] == "Second"
    second = made["chat_id"]
    assert client.get(base + f"/conversation?chat_id={second}").json()["messages"] == []
    assert client.get(base + f"/conversation?chat_id={first['chat_id']}").json()["chat_id"] == first["chat_id"]
    assert client.patch(base + f"/chats/{second}", json={"title": "Renamed", "expected_revision": 5}).status_code == 409
    assert client.patch(base + f"/chats/{second}", json={"title": "Renamed", "expected_revision": 0}).status_code == 200
    cleared = client.post(base + f"/chats/{first['chat_id']}/clear", json={}).json()
    assert cleared["archived_chat_id"] == first["chat_id"]
    assert client.post(base + f"/chats/{first['chat_id']}/restore", json={}).status_code == 200
    assert client.post(base + f"/chats/{first['chat_id']}/compact", json={}).status_code == 200
    assert client.post(base + f"/chats/{first['chat_id']}/explode", json={}).status_code == 404
    assert client.get(base + "/conversation?chat_id=999999").status_code == 404
    listed = client.get(base + "/chats").json()["chats"]
    assert len(listed) == 3 and all("archived" in row for row in listed)
