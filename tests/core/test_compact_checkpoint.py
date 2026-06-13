from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

import distr.core.db.orchestrator  # noqa: F401
from distr.core.db import Base, Chat
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def test_get_compact_checkpoint_prompt_reads_root_summary(monkeypatch):
    from distr.core.chat import ChatService, get_compact_checkpoint_prompt

    factory = _factory()
    monkeypatch.setattr("distr.core.chat.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        root = Chat(
            parent_id=None,
            title="Prior chat",
            provider="OpenAI",
            model_name="gpt-4o",
            additional_context=json.dumps(
                {
                    "compact_checkpoint": {
                        "active": True,
                        "summary": "We discussed backend device diagnostics.",
                        "chat_row_id": 1,
                    }
                }
            ),
        )
        session.add(root)
        session.flush()
        chat_id = int(root.id)

    prompt = get_compact_checkpoint_prompt(chat_id)
    assert "Chat compact checkpoint" in prompt
    assert "backend device diagnostics" in prompt

    history = ChatService.get_chat_history(chat_id)
    assert any(
        msg.get("role") == "system" and "backend device diagnostics" in (msg.get("content") or "")
        for msg in history
    )


def test_build_system_message_includes_compact_checkpoint(monkeypatch):
    from distr.core.agent.services.llm.core_mixin import LLMSharedMixin
    from distr.core.chat import get_compact_checkpoint_prompt

    factory = _factory()
    monkeypatch.setattr("distr.core.chat.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        root = Chat(
            parent_id=None,
            title="Forked chat",
            provider="OpenAI",
            model_name="gpt-4o",
            additional_context=json.dumps(
                {
                    "compact_checkpoint": {
                        "active": True,
                        "summary": "User asked about hearing their question.",
                        "chat_row_id": 2,
                    }
                }
            ),
        )
        session.add(root)
        session.flush()
        chat_id = int(root.id)

    class _Svc(LLMSharedMixin):
        def _get_provider_name(self):
            return "OpenAI"

        def _build_system_prompt_template(self, chat_id=None, include_tools_description=True):
            return "BASE PROMPT"

    svc = _Svc()
    system = svc._build_system_message(chat_id=chat_id, include_tools_description=False)
    content = system.get("content") or ""
    assert "BASE PROMPT" in content
    assert get_compact_checkpoint_prompt(chat_id) in content


def test_force_reload_current_chat_invalidates_cached_history(monkeypatch):
    from distr.core.chat_manager import ChatManagerCore

    factory = _factory()
    monkeypatch.setattr("distr.core.chat_manager.get_session", lambda: _session_ctx(factory))

    with _session_ctx(factory) as session:
        root = Chat(
            parent_id=None,
            title="Active chat",
            provider="OpenAI",
            model_name="gpt-4o",
        )
        session.add(root)
        session.flush()
        chat_id = int(root.id)

    calls = []

    def fake_get_chat_history(_chat_id):
        calls.append(_chat_id)
        return [{"role": "user", "content": f"fresh {len(calls)}"}]

    monkeypatch.setattr(
        "distr.core.chat_manager.ChatService.get_chat_history",
        fake_get_chat_history,
    )

    manager = ChatManagerCore.__new__(ChatManagerCore)
    manager._listeners = {}
    manager.current_model = "gpt-4o"
    manager.current_provider = "OpenAI"
    manager.chat_histories = {chat_id: [{"role": "user", "content": "stale"}]}
    manager._current_chat_id = chat_id
    manager._updating_chat = False
    manager.rag = None
    manager.agent_prompt = "BASE"

    manager.set_current_chat(chat_id, force_reload=True)

    assert calls == [chat_id]
    assert manager.chat_histories[chat_id][-1]["content"] == "fresh 1"


def test_current_chat_command_forces_history_reload(monkeypatch):
    from distr.core.agent.command_handler import _cmd_current_chat_changed

    class _Logger:
        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

    class _ChatManager:
        def __init__(self):
            self.calls = []

        def set_current_chat(self, chat_id, force_reload=False):
            self.calls.append((chat_id, force_reload))

    class _LLM:
        def __init__(self):
            self.changed = []

        def on_chat_changed(self, chat_id):
            self.changed.append(chat_id)

        def set_speaker_enabled(self, enabled):
            self.speaker_enabled = enabled

    @contextlib.contextmanager
    def broken_session():
        raise RuntimeError("db intentionally unavailable")
        yield

    monkeypatch.setattr("distr.core.db.get_session", broken_session)

    chat_manager = _ChatManager()
    llm = _LLM()
    session = SimpleNamespace(
        logger=_Logger(),
        config={"llm": {"engine": "ollama", "model_name": "llama3.2"}},
        settings={"voice_enabled": True},
        chat_manager=chat_manager,
        llm_service=llm,
        _hot_swap_llm_service=lambda *args, **kwargs: None,
        _hot_swap_tts_service=lambda *args, **kwargs: None,
    )

    _cmd_current_chat_changed(session, {"chat_id": 42})

    assert chat_manager.calls == [(42, True)]
    assert llm.changed == [42]
