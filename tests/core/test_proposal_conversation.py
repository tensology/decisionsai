import json

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector
from distr.core.db import Chat, TelegramGroupMessage, WhatsAppMessage
from distr.core.incoming import proposal_conversation, proposals
from distr.core.workflow.development_threads import ensure_development_thread
from distr.gui.web.routes.development.incoming import register_routes
from tests.core.test_proposal_continuity_integration import intake  # noqa: F401


@pytest.mark.parametrize("source", ["whatsapp", "telegram", "gmail", "mailshot"])
def test_proposal_source_reads_the_exact_thread_and_original_message(intake, monkeypatch, source):
    session, original, _ = intake
    thread = "original-thread"
    anchor = "10" if source == "telegram" else "original-message"
    original = {**original, "source": source, "source_thread_id": thread, "message_ids": [anchor]}
    pending = [original]
    monkeypatch.setattr(proposals, "list_incoming_proposals", lambda **kwargs: {"proposals": pending})
    monkeypatch.setattr(proposal_conversation, "get_session", session)
    if source == "whatsapp":
        with session() as db:
            db.add_all([
                WhatsAppMessage(message_id=anchor, jid=thread, text="Original request", whatsapp_timestamp=1),
                WhatsAppMessage(message_id="wrong-message", jid="wrong", text="Wrong conversation", whatsapp_timestamp=2),
                *(WhatsAppMessage(message_id=f"later-{i}", jid=thread, text="Later update",
                                  whatsapp_timestamp=i + 3) for i in range(65)),
            ])
            db.commit()
    elif source == "telegram":
        with session() as db:
            db.add_all([
                TelegramGroupMessage(telegram_message_id=10, chat_id=thread, text="Original request"),
                TelegramGroupMessage(telegram_message_id=10, chat_id="wrong", text="Wrong conversation"),
                TelegramGroupMessage(telegram_message_id=11, chat_id=thread, text="Later update"),
            ])
            db.commit()
    elif source == "gmail":
        monkeypatch.setattr(GoogleWorkspaceConnector, "__init__", lambda self: None)
        monkeypatch.setattr(GoogleWorkspaceConnector, "get_email_thread", lambda self, value: [
            {"id": anchor, "threadId": "wrong", "body": "Wrong conversation"},
            {"id": anchor, "threadId": value, "body": "Original request"},
            {"id": "later", "threadId": value, "body": "Later update"},
        ])
    else:
        class Mailshot:
            def get(self, path):
                assert path == f"mail/messages/{anchor}"
                return {"email": {"id": anchor, "thread_id": thread, "body": "Original request"}}

        monkeypatch.setattr("distr.core.tensology_client.configured_tensology_client", lambda **kwargs: Mailshot())

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    endpoint = "/api/workflows/studio/incoming/proposals/source"
    key = proposals.proposal_review_key(original)
    result = client.get(endpoint, params={"review_key": key})
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    data = result.json()
    assert (data["source"], data["thread_id"], data["anchor_id"]) == (source, thread, anchor)
    assert {item["source_thread_id"] for item in data["items"]} == {thread}
    assert {item["text"] for item in data["items"]} == ({"Original request"} if source == "mailshot"
                                                       else {"Original request", "Later update"})
    assert len(data["items"]) <= 40
    assert len([item for item in data["items"] if item["source_message_id"] == anchor]) == 1
    if source == "whatsapp":
        with session() as db:
            db.query(WhatsAppMessage).filter_by(message_id=anchor).one().jid = "wrong"
            db.commit()
        assert client.get(endpoint, params={"review_key": key}).status_code == 404
    pending[:] = [{**original, "message_ids": ["newer"]}]
    assert client.get(endpoint, params={"review_key": key}).status_code == 404


def test_submitted_source_requires_the_linked_thread_and_uses_its_handoff(intake, monkeypatch):
    from distr.gui.web.routes import chat as chat_routes
    from tests.core.test_chat_development_surface_isolation import _client

    session, original, projects = intake
    monkeypatch.setattr(chat_routes, "get_session", session)
    newer = {**original, "message_ids": ["newer"]}
    original_key = proposals.proposal_review_key(original)
    newer_key = proposals.proposal_review_key(newer)
    monkeypatch.setattr(proposals, "list_incoming_proposals", lambda **kwargs: {"proposals": [newer]})
    chat_id = ensure_development_thread(
        title=original["title"], project_id=projects[0], source_type="incoming_proposal",
        source_ref=proposals._proposal_handoff_key(original),
    )
    proposals._record_proposal_handoff(chat_id, original, project_id=projects[0], started=True)
    with session() as db:
        chat = db.get(Chat, chat_id)
        assert proposal_conversation.submitted_proposal_source_key(chat) == original_key
        params = json.loads(chat.params)
        params["incoming_reply_context"] = newer
        chat.params = json.dumps(params)
        db.commit()
        assert proposal_conversation.submitted_proposal_source_key(Chat(params=chat.params, parent_id=chat_id)) is None
        params["incoming_proposal_handoff"]["start_pending"] = True
        assert proposal_conversation.submitted_proposal_source_key(Chat(
            params=json.dumps(params), project_id=projects[0],
        )) is None
    monkeypatch.setattr(GoogleWorkspaceConnector, "__init__", lambda self: None)
    checked = []

    def get_thread(self, thread):
        checked.append(thread)
        return [
            {"id": "message-a", "threadId": thread, "body": "Original request"},
            {"id": "newer", "threadId": thread, "body": "New request"},
        ]

    monkeypatch.setattr(GoogleWorkspaceConnector, "get_email_thread", get_thread)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    chat_payload = _client().get(f"/api/chats/{chat_id}?surface=development").json()
    assert chat_payload["proposal_source_review_key"] == original_key
    endpoint = "/api/workflows/studio/incoming/proposals/source"
    assert client.get(endpoint, params={"review_key": original_key}).status_code == 404
    assert client.get(endpoint, params={"review_key": original_key, "chat_id": chat_id + 1}).status_code == 404
    assert client.get(endpoint, params={"review_key": newer_key, "chat_id": chat_id}).status_code == 404
    assert checked == []
    response = client.get(endpoint, params={"review_key": original_key, "chat_id": chat_id})
    assert response.status_code == 200
    assert response.json()["anchor_id"] == "message-a"
    assert checked == ["source-a"]
    with session() as db:
        db.get(Chat, chat_id).project_id = projects[1]
        db.commit()
    assert client.get(endpoint, params={"review_key": original_key, "chat_id": chat_id}).status_code == 404
    assert checked == ["source-a"]


def test_merged_source_selection_follows_each_retained_origin_before_and_after_handoff(intake, monkeypatch):
    session, original, projects = intake
    first = {**original, "source": "telegram", "source_thread_id": "first", "message_ids": ["10"],
             "participant": "First group"}
    second = {**first, "id": "local:telegram:second", "source_thread_id": "second", "message_ids": ["11"],
              "participant": "Second group"}
    merged = proposals._combine_frac_proposals(first, second)
    merged["source_scope"].append(dict(merged["source_scope"][0]))
    merged["evidence"] = [{"id": "10", "original_source": {
        "channel": "telegram", "source_thread_id": "second", "source_message_id": "10",
    }}]
    monkeypatch.setattr(proposals, "list_incoming_proposals", lambda **kwargs: {"proposals": [merged]})
    monkeypatch.setattr(proposal_conversation, "get_session", session)
    with session() as db:
        db.add_all([
            TelegramGroupMessage(telegram_message_id=10, chat_id="first", text="First original"),
            TelegramGroupMessage(telegram_message_id=10, chat_id="second", text="Second context"),
            TelegramGroupMessage(telegram_message_id=11, chat_id="second", text="Second original"),
        ])
        db.commit()

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    endpoint = "/workflows/studio/incoming/proposals/source"
    query = {"review_key": proposals.proposal_review_key(merged)}
    for submitted in (False, True):
        if submitted:
            chat_id = ensure_development_thread(
                title=merged["title"], project_id=projects[0], source_type="incoming_proposal",
                source_ref=proposals._proposal_handoff_key(merged),
            )
            proposals._record_proposal_handoff(chat_id, merged, project_id=projects[0], started=True)
            query["chat_id"] = chat_id
        choices = client.get(endpoint, params=query).json()["choices"]
        assert [row["label"] for row in choices] == ["Telegram · First group", "Telegram · Second group"]
        for choice, anchor in zip(choices, ("10", "11")):
            response = client.get(endpoint, params={**query, "source_key": choice["key"]})
            assert response.status_code == 200
            payload = response.json()
            assert (payload["thread_id"], payload["anchor_id"], payload["source_key"]) == (
                choice["thread_id"], anchor, choice["key"],
            )
            assert {row["source_thread_id"] for row in payload["items"]} == {choice["thread_id"]}
        assert client.get(endpoint, params={**query, "source_key": "f" * 32}).status_code == 404
    for ref in merged["source_scope"]:
        ref["participant"] = "Same group name"
    assert [row["label"] for row in proposal_conversation.read_proposal_conversation(merged)["choices"]] == [
        "Telegram · Same group name (1 of 2)", "Telegram · Same group name (2 of 2)",
    ]


def test_legacy_merge_uses_only_original_evidence_when_explicit_scope_is_missing():
    proposal = {"merged_from": ["a", "b"], "source": "gmail", "source_thread_id": "flattened", "message_ids": ["one"],
                "evidence": [
                    {"original_source": {"channel": "telegram", "source_thread_id": "chat", "source_message_id": "10"}},
                    {"original_source": {"channel": "telegram", "source_thread_id": "context", "source_message_id": "11"},
                     "context_only": True},
                ]}
    assert proposal_conversation.read_proposal_conversation(proposal)["choices"][0]["thread_id"] == "chat"
    proposal["evidence"].clear()
    with pytest.raises(LookupError, match="no original conversation"):
        proposal_conversation.read_proposal_conversation(proposal)


def test_gmail_thread_parser_requests_full_thread_and_filters_foreign_rows(monkeypatch):
    connector = object.__new__(GoogleWorkspaceConnector)
    monkeypatch.setattr(connector, "_ensure_valid_token", lambda: True)
    seen = []

    def fetch(method, url, **options):
        seen.append((method, url, options))
        return {"id": "thread", "messages": [
            {"id": "one", "threadId": "thread", "payload": {"headers": [{"name": "Subject", "value": "Archived email"}]}},
            {"id": "same-id-in-other-thread", "threadId": "wrong"},
        ]}

    monkeypatch.setattr(connector, "_make_request", fetch)
    assert [item["subject"] for item in connector.get_email_thread("thread")] == ["Archived email"]
    assert seen == [("GET", "https://gmail.googleapis.com/gmail/v1/users/me/threads/thread",
                     {"params": {"format": "full"}, "timeout": 20})]
