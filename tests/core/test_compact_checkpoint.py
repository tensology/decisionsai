from __future__ import annotations

import contextlib
import json

import distr.core.db.hermes  # noqa: F401
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
