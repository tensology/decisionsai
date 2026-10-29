"""Thread identity and read markers persist independently of display/agent state."""
import json
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.core.db import Chat, get_session
from distr.core.chat_sidebar import chat_skin, set_chat_skin, sidebar_state, restore_chat_skin


def test_read_marker_preserves_metadata_and_only_clears_seen_replies():
    with get_session() as session:
        chat = Chat(title="Sidebar read test", additional_context=json.dumps({"title_auto": {"manual": True}}))
        session.add(chat); session.flush()
        first = Chat(parent_id=chat.id, input="First question", response="First reply")
        session.add(first); session.flush()
        initial = sidebar_state(session, chat, {"selected_oracle": "0.gif"})
        assert initial["unread"] is False
        assert initial["skin_name"] == "oracle"
        second = Chat(parent_id=chat.id, input="New intent", response="Latest reply")
        session.add(second); session.flush()
        hidden = Chat(parent_id=chat.id, input="Hidden", response="Hidden reply", is_hidden=True)
        session.add(hidden); session.flush()
        state = sidebar_state(session, chat, {})
        assert state["unread"] is True
        assert state["latest_message"] == "Latest reply"
        assert sidebar_state(session, chat, {}, mark_read=first.id)["unread"] is True
        assert sidebar_state(session, chat, {}, mark_read=second.id)["unread"] is False
        set_chat_skin(chat, "character-a")
        assert json.loads(chat.additional_context)["title_auto"]["manual"] is True
        session.commit()
        session.refresh(chat)
        assert chat_skin(chat) == "character-a"
        user_only = Chat(parent_id=chat.id, input="Next question")
        session.add(user_only); session.flush()
        assert sidebar_state(session, chat, {})["latest_message"] == "Next question"
        assert sidebar_state(session, chat, {})["unread"] is False


def test_skin_restoration_uses_saved_thread_and_rejects_unavailable_skin(monkeypatch):
    from distr.core import chat_sidebar
    from distr.core.services import settings_service
    from distr.core import settings
    monkeypatch.setattr(settings, "load_settings_from_db", lambda: {"selected_oracle": "other"})
    monkeypatch.setattr(chat_sidebar, "validate_chat_skin", lambda skin: skin)
    update = Mock()
    monkeypatch.setattr(settings_service, "update_oracle_skin", update)
    with get_session() as session:
        chat = Chat(title="Skin test")
        set_chat_skin(chat, "saved-character")
        session.add(chat); session.commit(); chat_id = chat.id
    restore_chat_skin(chat_id)
    update.assert_called_once_with("saved-character", persist_chat=False)
    update.reset_mock()
    def available(skin):
        if skin != 'oracle':
            raise ValueError('missing')
        return skin
    monkeypatch.setattr(chat_sidebar, "validate_chat_skin", available)
    restore_chat_skin(chat_id)
    update.assert_called_once_with('oracle', persist_chat=False)
    with get_session() as session:
        assert chat_skin(session.get(Chat, chat_id)) == 'saved-character'


def test_new_chat_captures_own_skin_without_changing_other_threads():
    from distr.core.chat import ChatService
    first, _ = ChatService.create_new_chat(title='First skin', skin_name='character-a', activate=False)
    second, _ = ChatService.create_new_chat(title='Second skin', skin_name='character-b', activate=False)
    with get_session() as session:
        assert chat_skin(session.get(Chat, first)) == 'character-a'
        assert chat_skin(session.get(Chat, second)) == 'character-b'


def test_chat_api_lists_identity_marks_read_and_does_not_restore_skin_on_view(monkeypatch):
    from distr.gui.web.routes import chat as routes
    from distr.core import chat_sidebar
    from distr.core.signals import signal_manager
    monkeypatch.setattr(routes, "load_settings_from_db", lambda: {"selected_oracle": "oracle"})
    monkeypatch.setattr(routes, "_voice_model_to_display_name", lambda *args: "Hayley Williams")
    monkeypatch.setattr(chat_sidebar, "validate_chat_skin", lambda skin: skin)
    monkeypatch.setattr(routes, "validate_chat_skin", lambda skin: skin)
    with get_session() as session:
        chat = Chat(title="API sidebar test", voice_provider="fish", voice_model="hayley", provider="OpenAI", model_name="test")
        set_chat_skin(chat, "saved-character")
        session.add(chat); session.commit(); chat_id = chat.id
    app = FastAPI(); app.include_router(routes.create_routes(Path(__file__).parents[2] / "distr/gui/web/templates"), prefix="/api")
    client = TestClient(app)
    rows = client.get('/api/chats').json()['chats']
    row = next(r for r in rows if r['id'] == chat_id)
    assert (row['voice_name'], row['skin_name'], row['latest_message'], row['unread']) == ('Hayley Williams', 'saved-character', '', False)
    with get_session() as session:
        reply = Chat(parent_id=chat_id, input="Question", response="A new reply")
        session.add(reply); session.commit(); reply_id = reply.id
    row = next(r for r in client.get('/api/chats').json()['chats'] if r['id'] == chat_id)
    assert row['unread'] is True
    signal = Mock()
    monkeypatch.setattr(signal_manager, 'web_load_chat_in_agent_requested', signal, raising=False)
    viewed = client.get(f'/api/chats/{chat_id}')
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()['skin_name'] == 'saved-character'
    signal.emit.assert_not_called()
    updated = client.patch(f'/api/chats/{chat_id}', json={'skin_name': 'other-character'})
    assert updated.status_code == 200, updated.text
    assert updated.json()['skin_name'] == 'other-character'
    with get_session() as session:
        assert session.get(Chat, chat_id).title == 'API sidebar test'
    assert client.post(f'/api/chats/{chat_id}/read', json={'reply_id': reply_id - 1}).json()['unread'] is True
    assert client.post(f'/api/chats/{chat_id}/read', json={'reply_id': reply_id}).json()['unread'] is False
    assert client.post(f'/api/chats/{chat_id}/read', json={'reply_id': 'bad'}).status_code == 422
    monkeypatch.setattr(routes, 'validate_chat_skin', Mock(side_effect=ValueError('unavailable')))
    assert client.patch(f'/api/chats/{chat_id}', json={'skin_name': '../missing'}).status_code == 422
    assert client.get(f'/api/chats/{chat_id}').json()['skin_name'] == 'other-character'


def test_background_reply_notifies_sidebar_subscribers_without_message_content(monkeypatch):
    import asyncio
    from distr.gui.web.routes import chat as routes

    class Socket:
        async def send_text(self, text):
            self.event = json.loads(text)

    socket = Socket()
    monkeypatch.setattr(routes, '_topic_ws_connections', {'chat_list': {socket}})
    monkeypatch.setattr(routes, '_chat_ws_connections', {})
    asyncio.run(routes.broadcast_chat_event({'chat_id': 999, 'event': 'stream_finished', 'response_text': 'Private reply'}))
    assert socket.event == {'type': 'chat_list_changed', 'chat_id': 999}
