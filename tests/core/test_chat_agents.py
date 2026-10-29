from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.core.chat import ChatService, cleanup_chat_dependencies
from distr.core.chat_agents import agent_identity, agent_instructions
from distr.core.db import AgentConversation, Chat, ChatAgent, Settings, get_session


def test_persistent_agent_has_multiple_conversations_and_does_not_replace_primary():
    with get_session() as session:
        settings = session.query(Settings).first()
        if not settings:
            settings = Settings()
            session.add(settings)
        settings.agent_current_chat_id = 919191
        settings.last_chat_id = 919191
    root, greeting = ChatService.create_new_chat(agent_name="Sarah", agent_role="Review support tickets", skin_name="oracle", tts_provider="kokoro", tts_voice="af_sky", activate=False)
    assert greeting is None
    with get_session() as session:
        identity = agent_identity(session, root)
        assert identity["agent_name"] == "Sarah"
        assert identity["main_conversation_id"] == root
        profile = session.get(ChatAgent, identity["agent_id"])
        assert (profile.skin_name, profile.voice_provider, profile.voice_model) == ("oracle", "kokoro", "af_sky")
        second = Chat(title="Another topic")
        session.add(second); session.flush()
        session.add(AgentConversation(agent_id=profile.id, chat_id=second.id)); session.flush()
        assert agent_identity(session, second.id)["agent_id"] == profile.id
        settings = session.query(Settings).first()
        assert settings.agent_current_chat_id == settings.last_chat_id == 919191
    assert "Review support tickets" in agent_instructions(root)
    with get_session() as session:
        cleanup_chat_dependencies(session, root)
        assert agent_identity(session, root) == {}
        assert session.get(ChatAgent, identity["agent_id"]).name == "Sarah"


def test_agent_creation_rolls_back_conversation_if_identity_write_fails(monkeypatch):
    from distr.core import chat_agents

    def fail(*args):
        raise RuntimeError("storage failed")
    monkeypatch.setattr(chat_agents, "create_agent_profile", fail)
    with pytest.raises(RuntimeError, match="storage failed"):
        ChatService.create_new_chat(title="Rollback agent fixture", agent_name="Rollback", activate=False)
    with get_session() as session:
        assert session.query(Chat).filter(Chat.title == "Rollback agent fixture").first() is None


def test_agent_api_preserves_global_settings_and_never_sends_greeting(monkeypatch):
    from distr.gui.web.routes import chat as routes
    from distr.core import signals
    from distr.core import settings

    monkeypatch.setattr(routes, "load_settings_from_db", lambda: {"selected_oracle": "oracle"})
    monkeypatch.setattr(routes, "validate_chat_skin", lambda value: value)
    save = Mock()
    monkeypatch.setattr(settings, "save_settings_to_db", save)
    signal_manager = Mock()
    monkeypatch.setattr(signals, "signal_manager", signal_manager)
    app = FastAPI()
    app.include_router(routes.create_routes(Path(__file__).parents[2] / "distr/gui/web/templates"), prefix="/api")
    client = TestClient(app)
    payload = {"agent_name": "  Alex  ", "agent_role": "  Triage incoming requests  ", "skin_name": "oracle", "provider": "ollama", "model_name": "local-test", "voice_provider": "kokoro", "voice_model": "af_sky"}
    response = client.post("/api/chats", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["agent_name"] == "Alex"
    assert result["agent_role"] == "Triage incoming requests"
    assert result["starting_question"] is None
    saved = client.get(f"/api/chats/{result['id']}").json()
    assert saved["agent_id"] == result["agent_id"]
    assert saved["messages"] == []
    rows = client.get("/api/chats").json()["chats"]
    assert next(row for row in rows if row["id"] == result["id"])["agent_name"] == "Alex"
    assert client.post("/api/chats", json={**payload, "agent_name": "   "}).status_code == 422
    assert client.post("/api/chats", json={**payload, "agent_name": "x" * 81}).status_code == 422
    save.assert_not_called()
    signal_manager.web_create_chat_emits_requested.emit.assert_not_called()
    signal_manager.web_load_chat_in_agent_requested.emit.assert_not_called()


def test_role_reaches_structured_runtime_context():
    from distr.core.turn_runtime.context import build_development_context

    root, _ = ChatService.create_new_chat(agent_name="Reviewer", agent_role="Check accessibility", activate=False)
    context = build_development_context(root, "Review this", {}, project_folder="", autonomy_level="plan")
    assert "Your agent name is Reviewer" in context[0]["content"]
    assert "Check accessibility" in context[0]["content"]
