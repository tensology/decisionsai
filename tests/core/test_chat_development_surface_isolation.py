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


def test_plan_root_stays_out_of_chat_history_and_in_the_plan_list():
    """A new_root plan chat is not Chat or Development history, and Plan still lists it."""
    import json
    from types import SimpleNamespace

    from distr.core.db import Chat, get_session
    from distr.core.planning.chats import is_plan_chat, list_chats, new_root
    from distr.gui.oracle.menu import get_recent_chats_for_menu

    assert is_plan_chat(SimpleNamespace(autonomy_level="plan", params="{}"))
    assert is_plan_chat(SimpleNamespace(autonomy_level="full", params=json.dumps({"plan_workspace_id": 4})))
    assert is_plan_chat(SimpleNamespace(autonomy_level="full", params=json.dumps({"source_type": "plan_workspace"})))
    assert not is_plan_chat(SimpleNamespace(autonomy_level="full", params=json.dumps({"source_type": "prompt"})))

    workspace_id = 910000003
    plan_id = regular_id = None
    try:
        with get_session() as db:
            plan = new_root(db, SimpleNamespace(id=workspace_id, project_id=None), title="Plan only history leak")
            regular = Chat(title="Regular history control", autonomy_level="full", is_hidden=False, is_archived=False)
            db.add(regular)
            db.flush()
            plan_id, regular_id = int(plan.id), int(regular.id)
            db.commit()

        client = _client()
        chat_ids = {row["id"] for row in client.get("/api/chats?surface=chat").json()["chats"]}
        development_ids = {row["id"] for row in client.get("/api/chats?surface=development").json()["chats"]}
        assert plan_id not in chat_ids
        assert plan_id not in development_ids
        assert regular_id in chat_ids
        assert any(row["id"] == plan_id for row in list_chats(workspace_id))
        menu_ids = {chat_id for chat_id, _title in get_recent_chats_for_menu(limit=20)}
        assert plan_id not in menu_ids
        assert regular_id in menu_ids
    finally:
        if plan_id is not None or regular_id is not None:
            with get_session() as db:
                db.query(Chat).filter(Chat.id.in_([i for i in (plan_id, regular_id) if i is not None])).delete(
                    synchronize_session=False
                )
                db.commit()


def test_development_chat_list_exposes_shared_timer_state():
    from distr.core.workflow.development_control import resume_thread_time

    chat_id = ensure_development_thread(title="Shared timer state")
    resume_thread_time(chat_id)

    response = _client().get("/api/chats?include_archived=true&surface=development")
    row = next(chat for chat in response.json()["chats"] if chat["id"] == chat_id)

    assert row["development_time_running"] is True
    assert row["development_time_seconds"] >= 0
