from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.core.workflow.development_threads import ensure_development_thread
from distr.gui.web.routes.chat import create_routes


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_routes(Path.cwd()), prefix="/api")
    return TestClient(app)


def test_development_thread_cannot_be_loaded_into_conversational_agent():
    chat_id = ensure_development_thread(title="Isolated Development task")

    response = _client().post(f"/api/chats/{chat_id}/load-in-agent")

    assert response.status_code == 409
    assert response.json()["detail"] == "Development tasks cannot be loaded into the Chat agent."


def test_development_thread_rejects_conversational_agent_messages():
    chat_id = ensure_development_thread(title="Isolated Development task")

    response = _client().post(
        f"/api/chats/{chat_id}/send-to-agent",
        json={"message": "This belongs in Chat", "speak": False},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Development tasks cannot receive Chat agent messages."


def test_development_thread_rejects_conversational_compaction():
    chat_id = ensure_development_thread(title="Isolated Development task")

    response = _client().post(f"/api/chats/{chat_id}/compact", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "Development tasks must use the Development lifecycle API."


def test_development_thread_rejects_conversational_fork():
    chat_id = ensure_development_thread(title="Isolated Development task")

    response = _client().post(f"/api/chats/{chat_id}/fork", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "Development tasks must use the Development lifecycle API."


def test_development_thread_rejects_conversational_cancel():
    chat_id = ensure_development_thread(title="Isolated Development task")

    response = _client().post(f"/api/chats/{chat_id}/cancel")

    assert response.status_code == 409
    assert response.json()["detail"] == "Development tasks must use the Development lifecycle API."


def test_development_thread_rejects_conversational_delete():
    chat_id = ensure_development_thread(title="Isolated Development task")

    response = _client().delete(f"/api/chats/{chat_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == "Development tasks must use the Development lifecycle API."


@pytest.mark.parametrize(
    ("method", "suffix", "json_body"),
    [
        ("get", "/header-stats", None),
        ("post", "/refresh-title", None),
        ("post", "/turns/1/steer", {"message": "Change direction"}),
    ],
)
def test_development_thread_rejects_remaining_chat_only_routes(method, suffix, json_body):
    chat_id = ensure_development_thread(title="No Chat lifecycle leakage")

    response = _client().request(method, f"/api/chats/{chat_id}{suffix}", json=json_body)

    assert response.status_code == 409
    assert response.json()["detail"] == "Development tasks must use the Development lifecycle API."


def test_authoritative_work_item_blocks_chat_agent_when_legacy_projection_is_missing():
    from distr.core.db import Chat, get_session

    chat_id = ensure_development_thread(title="Authoritative Development task")
    with get_session() as db:
        root = db.get(Chat, chat_id)
        root.params = "{}"
        db.commit()

    response = _client().post(f"/api/chats/{chat_id}/load-in-agent")

    assert response.status_code == 409
    assert response.json()["detail"] == "Development tasks cannot be loaded into the Chat agent."


def test_chat_service_rejects_development_as_current_conversation():
    from distr.core.chat import ChatService

    chat_id = ensure_development_thread(title="Not a conversational chat")

    try:
        ChatService.set_current_chat_id(chat_id)
    except ValueError as error:
        assert str(error) == "Development tasks cannot become the current Chat conversation."
    else:
        raise AssertionError("Development current-chat assignment was accepted")


def test_chat_service_ignores_a_contaminated_development_pointer():
    from distr.core.chat import ChatService
    from distr.core.db import Settings, get_session

    chat_id = ensure_development_thread(title="Contaminated pointer")
    with get_session() as db:
        settings = db.query(Settings).first()
        settings.agent_current_chat_id = chat_id
        settings.last_chat_id = chat_id
        db.commit()

    assert ChatService.get_current_chat_id() is None


def test_chat_detail_requires_the_matching_surface_contract():
    chat_id = ensure_development_thread(title="Development detail")
    client = _client()

    conversational = client.get(f"/api/chats/{chat_id}")
    development = client.get(f"/api/chats/{chat_id}?surface=development")

    assert conversational.status_code == 409
    assert conversational.json()["detail"] == "This thread belongs to Development, not Chat."
    assert development.status_code == 200
    assert isinstance(development.json()["development"], dict)
