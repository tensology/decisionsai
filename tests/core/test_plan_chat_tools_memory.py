"""Chat build part 2: tool calls (N1), memory (N2), server default route. Temp DBs, fake values only."""
import json

import pytest

from distr.core.db import Chat
from distr.core.db.workflow import PlanItem, PlanItemRevision
from distr.core.planning import chat_tools, chats as ch, conversation as c, plan_memory, service
from tests.core.test_plan_conversation import respond, send, store  # noqa: F401  (fixture)

FAKE_KEY = "sk-FAKEFAKEFAKEFAKE00001234"
WIRE = 'screen "Home" id=home route=/\n  #| The landing page.\n  heading "Welcome"\n# decisions-flow: {"v":1,"nodes":{"home":{"note":"keep"}}}'


def _item(store, item_id=1):
    with store[0]() as db:
        return db.get(PlanItem, item_id)


def _revisions(store, item_id):
    with store[0]() as db:
        return [(r.revision, r.source) for r in db.query(PlanItemRevision).filter_by(item_id=item_id).order_by(PlanItemRevision.revision)]


def test_update_tool_writes_ai_revision_and_card_then_undo_restores_as_new_revision(store, monkeypatch):
    respond(monkeypatch, {"reply": "Updated", "edits": [{"tool": "update_document", "id": 1, "item_type": "prd", "title": "Requirements",
                                                          "content": "New", "content_format": "markdown", "expected_revision": 1}]})
    result = send()
    card = result["messages"][-1]["tool_calls"][0]
    assert card == {"tool": "update_document", "id": 1, "item_type": "prd", "title": "Requirements", "status": "applied",
                    "from_revision": 1, "to_revision": 2, "warnings": []}
    assert _revisions(store, 1) == [(1, None), (2, "ai")] or _revisions(store, 1)[-1] == (2, "ai")
    restored = chat_tools.restore_revision(1, 1, revision=1, expected_revision=2)
    assert restored["content"] == "Old" and _item(store).content == "Old"
    assert _revisions(store, 1)[-1] == (3, "restore")                # history is never rewritten
    with pytest.raises(service.PlanRevisionConflict):
        chat_tools.restore_revision(1, 1, revision=1, expected_revision=2)


def test_create_tool_and_undo_archives_only_ai_created_items(store, monkeypatch):
    respond(monkeypatch, {"reply": "Drew it", "edits": [{"tool": "create_interface", "item_type": "wireframe", "title": "Home",
                                                          "content": WIRE, "content_format": "wire"}]})
    card = send()["messages"][-1]["tool_calls"][0]
    assert card["tool"] == "create_interface" and card["from_revision"] == 0 and card["to_revision"] == 1
    created = _item(store, card["id"])
    assert "#| The landing page." in created.content and created.content.rstrip().endswith('"keep"}}}')
    chat_tools.restore_revision(1, card["id"], revision=0, expected_revision=1)
    assert _item(store, card["id"]).status == "archived"
    with pytest.raises(service.PlanItemTypeForbidden):                 # a user's item can't be archived this way
        chat_tools.restore_revision(1, 1, revision=0, expected_revision=1)


def test_wrong_tool_name_is_rejected_and_nothing_written(store, monkeypatch):
    respond(monkeypatch, {"reply": "x", "edits": [{"tool": "create_erd", "id": 1, "item_type": "prd", "title": "R",
                                                    "content": "Bad", "content_format": "markdown", "expected_revision": 1}]})
    assert send()["status"] == "error" and _item(store).content == "Old"


def test_dirty_draft_is_never_overwritten_and_card_carries_proposal(store, monkeypatch):
    respond(monkeypatch, {"reply": "Changed", "edits": [{"tool": "update_document", "id": 1, "item_type": "prd", "title": "Requirements",
                                                          "content": "AI version", "content_format": "markdown", "expected_revision": 1}]})
    result = send(drafts=[{"id": 1, "base_revision": 1}])
    card = result["messages"][-1]["tool_calls"][0]
    assert card["status"] == "conflict" and card["proposed_content"] == "AI version"
    assert _item(store).content == "Old" and len(_revisions(store, 1)) == 1
    assert "unsaved changes" in result["messages"][-1]["content"]


def test_memory_is_opt_in_redacted_project_scoped_and_forgettable(store, monkeypatch):
    calls = respond(monkeypatch, {"reply": "Checkout uses a single page", "edits": []})
    c.send_message(1, message=f"Checkout flow should be one page, key {FAKE_KEY}", provider="openai", model_name="chosen")
    first = c.get_conversation(1)["chat_id"]
    assert plan_memory.memory_view(1, first)["count"] == 0               # off by default: nothing stored
    plan_memory.set_enabled(1, first, True)
    c.send_message(1, message=f"Checkout flow must email a@example.com, key {FAKE_KEY}", provider="openai", model_name="chosen", chat_id=first)
    view = plan_memory.memory_view(1, first)
    assert view["enabled"] and view["count"] == 1
    text = view["records"][0]["text"]
    assert FAKE_KEY not in text and "a@example.com" not in text and "[REDACTED:api-key]" in text and "Checkout flow" in text
    second = ch.create_chat(1)["chat_id"]
    c.send_message(1, message="What did we decide about checkout flow?", provider="openai", model_name="chosen", chat_id=second)
    assert "Earlier discussion" not in calls[-1][2][0]["content"]          # memory off in chat 2: no retrieval
    plan_memory.set_enabled(1, second, True)
    out = c.send_message(1, message="What did we decide about checkout flow?", provider="openai", model_name="chosen", chat_id=second)
    system = calls[-1][2][0]["content"]
    assert "Earlier discussion from other chats" in system and "[REDACTED:api-key]" in system and FAKE_KEY not in json.dumps(calls[-1])
    assert out["messages"][-1]["memory_used"] == 1
    ch.archive_chat(1, first)                                            # archived chats are not recalled
    assert plan_memory.recall(1, second, "checkout flow") == []
    ch.restore_chat(1, first)
    assert plan_memory.forget(1, first)["removed"] == 1 and plan_memory.recall(1, second, "checkout flow") == []
    with pytest.raises(ValueError):
        plan_memory.forget(1, first, scope="everything")


def test_secret_heavy_record_is_skipped():
    assert plan_memory.redact_record("OPENAI_API_KEY=sk-FAKE000000000000000000") is None
    assert plan_memory.redact_record("card 4111 1111 1111 1111 for the shop checkout page layout review") is not None
    assert "4111" not in plan_memory.redact_record("card 4111 1111 1111 1111 for the shop checkout page layout review")


def test_default_route_reads_settings_and_rejects_auto(store, monkeypatch):
    assert ch.default_route() == ("ollama", "default")
    monkeypatch.setattr(c, "load_settings_from_db", lambda: {"conversational_llm_provider": "openrouter", "conversational_llm_model": "auto"})
    assert ch.default_route() == (None, None)
    assert c.get_conversation(1)["default_route"] == {"provider": None, "model_name": None}


def test_http_restore_memory_routes(http, monkeypatch):
    client, base = http
    edits = []
    def stream(provider, model, messages, settings):
        yield json.dumps({"reply": "Updated", "edits": edits})
    monkeypatch.setattr(c.llm_factory, "create_stream", stream)
    made = client.post(base + "/messages", json={"message": "Make the brief", "provider": "ollama", "model_name": "m"}).json()
    assert client.post(base + "/items", json={"item_type": "prd", "title": "Requirements", "content": "# PRD\n"}).status_code == 200
    item = next(i for i in client.get(base).json()["items"])
    edits.append({"tool": "update_document" if item["content_format"] == "markdown" else "update_interface", "id": item["id"],
                  "item_type": item["item_type"], "title": item["title"], "content": item["content"] + "\nAI line\n",
                  "content_format": item["content_format"], "expected_revision": item["revision_count"]})
    turn = client.post(base + "/messages", json={"message": "Add a line", "provider": "ollama", "model_name": "m",
                                                 "chat_id": made["chat_id"], "drafts": []}).json()
    card = turn["messages"][-1]["tool_calls"][0]
    assert card["status"] == "applied" and card["to_revision"] == card["from_revision"] + 1
    url = base + f"/plan-items/{item['id']}/restore"
    assert client.post(url, json={"revision": card["from_revision"], "expected_revision": 99}).status_code == 409
    undone = client.post(url, json={"revision": card["from_revision"], "expected_revision": card["to_revision"]})
    assert undone.status_code == 200 and "AI line" not in undone.json()["content"]
    assert client.post(url, json={"revision": 0, "expected_revision": card["to_revision"] + 1}).status_code == 403
    chat_id = made["chat_id"]
    assert client.put(base + f"/chats/{chat_id}/memory", json={"enabled": True}).json()["enabled"] is True
    assert client.put(base + f"/chats/{chat_id}/memory", json={}).status_code == 422
    assert client.get(base + f"/chats/{chat_id}/memory").json()["count"] == 0
    assert client.delete(base + f"/chats/{chat_id}/memory?scope=project").json()["removed"] == 0
    assert client.delete(base + f"/chats/{chat_id}/memory?scope=all").status_code == 409


from tests.core.test_plan_chats import http  # noqa: E402,F401  (fixture: real routes, temp project DB)
from tests.core.test_planning_workspace import plan_workspace_db  # noqa: E402,F401
