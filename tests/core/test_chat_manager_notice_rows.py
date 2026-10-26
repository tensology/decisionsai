"""Regression: unbound assistant text must never overwrite a completed turn.

Bug (chat id 1976): a proactive initiative notice was written through
``ChatManagerCore.add_assistant_message`` and landed on the user's just-answered
turn row, replacing the real "Available Projects" answer with
"I noticed something I can help with...". Proactive notes must become their own
row instead.
"""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import distr.core.db.orchestrator  # noqa: F401  (registers orchestrator tables)
from distr.core.db import Base, Chat


@pytest.fixture(autouse=True)
def _clear_turn_binding():
    """The live turn contextvar leaks between tests; keep these cases isolated."""
    from distr.core.chat_manager import bind_active_chat_turn

    bind_active_chat_turn(0, None)
    yield
    bind_active_chat_turn(0, None)


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


def _patch_db(monkeypatch, factory):
    # chat_manager.add_assistant_message calls get_session() directly (no "with"),
    # so give it a real session; ChatService/audit use the context manager form.
    monkeypatch.setattr("distr.core.chat_manager.get_session", lambda: factory())
    monkeypatch.setattr("distr.core.chat.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))


def _seed_answered_turn(factory):
    with _session_ctx(factory) as session:
        root = Chat(parent_id=None, title="Root", provider="Ollama", model_name="m")
        session.add(root)
        session.flush()
        turn = Chat(
            parent_id=root.id,
            title=None,
            input="What projects are we working on?",
            response="Available Projects: 11 live boards.",
            provider="Ollama",
            model_name="m",
        )
        session.add(turn)
        session.flush()
        return int(root.id), int(turn.id)


def test_unbound_assistant_message_appends_row_without_clobbering(monkeypatch):
    from distr.core.chat_manager import ChatManagerCore

    factory = _factory()
    _patch_db(monkeypatch, factory)
    monkeypatch.setattr(
        "distr.core.chat_title_auto.schedule_chat_title_refresh",
        lambda *a, **k: None,
    )
    root_id, turn_id = _seed_answered_turn(factory)

    manager = ChatManagerCore()
    manager.add_assistant_message(
        root_id,
        "I noticed something I can help with: Player1Sport has 76 items.",
        is_final=False,
    )

    with _session_ctx(factory) as session:
        rows = (
            session.query(Chat)
            .filter(Chat.parent_id == root_id)
            .order_by(Chat.id)
            .all()
        )
        assert len(rows) == 2
        # The completed answer is untouched.
        assert int(rows[0].id) == turn_id
        assert rows[0].response == "Available Projects: 11 live boards."
        # The notice became its own assistant-only row.
        assert rows[1].input is None
        assert "I noticed something" in (rows[1].response or "")


def test_bound_turn_still_updates_in_place(monkeypatch):
    """A turn bound via turn_id keeps overwriting its own row (streaming answer)."""
    from distr.core.chat_manager import ChatManagerCore

    factory = _factory()
    _patch_db(monkeypatch, factory)
    monkeypatch.setattr(
        "distr.core.chat_title_auto.schedule_chat_title_refresh",
        lambda *a, **k: None,
    )
    root_id, turn_id = _seed_answered_turn(factory)

    manager = ChatManagerCore()
    manager.add_assistant_message(
        root_id,
        "The answer, rewritten by the live turn.",
        is_final=False,
        turn_id=turn_id,
    )

    with _session_ctx(factory) as session:
        rows = session.query(Chat).filter(Chat.parent_id == root_id).all()
        assert len(rows) == 1
        assert rows[0].response == "The answer, rewritten by the live turn."


def test_add_assistant_notice_always_creates_own_row(monkeypatch):
    from distr.core.chat_manager import ChatManagerCore

    factory = _factory()
    _patch_db(monkeypatch, factory)
    root_id, _turn_id = _seed_answered_turn(factory)

    manager = ChatManagerCore()
    row_id = manager.add_assistant_notice(root_id, "Board update happened.")

    assert row_id is not None
    with _session_ctx(factory) as session:
        notice = session.get(Chat, int(row_id))
        assert notice.input is None
        assert notice.response == "Board update happened."
