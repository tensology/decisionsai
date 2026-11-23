"""Agent conversations are only deleted when that exact conversation is confirmed.

Ticket, board and Development-thread cascades only ever remove Development threads.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat, get_session as global_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import DevelopmentWorkItem
from distr.gui.web.routes import kanban
from distr.gui.web.routes.chat import create_routes as chat_routes


def _chat_client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_routes(Path.cwd()), prefix="/api")
    return TestClient(app)


def _agent_conversation(session_factory=global_session, title="Halley") -> int:
    with session_factory() as s:
        chat = Chat(title=title, input="hi", response="hello")
        s.add(chat)
        s.commit()
        return int(chat.id)


def _exists(chat_id: int, session_factory=global_session) -> bool:
    with session_factory() as s:
        return s.get(Chat, int(chat_id)) is not None


def test_agent_conversation_delete_without_confirmation_is_rejected():
    chat_id = _agent_conversation()
    client = _chat_client()
    for url in (f"/api/agent-conversations/{chat_id}", f"/api/chats/{chat_id}"):
        response = client.delete(url)
        assert response.status_code == 409
        assert f"confirm={chat_id}" in response.json()["detail"]
    assert _exists(chat_id)


def test_agent_conversation_delete_with_wrong_confirmation_is_rejected():
    chat_id = _agent_conversation()
    other = _agent_conversation(title="Other")
    response = _chat_client().delete(f"/api/agent-conversations/{chat_id}?confirm={other}")
    assert response.status_code == 409
    assert _exists(chat_id) and _exists(other)


def test_agent_conversation_delete_with_matching_confirmation_succeeds():
    chat_id = _agent_conversation()
    response = _chat_client().delete(f"/api/agent-conversations/{chat_id}?confirm={chat_id}")
    assert response.status_code == 200, response.text
    assert not _exists(chat_id)


def test_development_thread_delete_refuses_agent_conversations():
    from distr.core.workflow.development_control import delete_thread

    chat_id = _agent_conversation()
    with pytest.raises(LookupError):
        delete_thread(chat_id, delete_linked_ticket=True)
    assert _exists(chat_id)


@pytest.fixture()
def board_api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'safeguard.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(kanban, "get_session", get_session)
    monkeypatch.setattr("distr.core.workspace_memory.lifecycle.hook_remove_workspace", lambda *_a, **_k: None)
    monkeypatch.setattr("distr.core.chat.remove_chat_transcript_audit_events", lambda *_a, **_k: 0)
    app = FastAPI()
    app.include_router(kanban.create_routes(), prefix="/api")
    return TestClient(app), get_session


def _seed_board(get_session):
    """A project-less board with one ticket linked to an agent conversation and one to a Development thread."""
    with get_session() as s:
        agent_chat = Chat(title="Halley", input="hi", response="hello")
        dev_chat = Chat(title="Dev task")
        s.add_all([agent_chat, dev_chat])
        s.flush()
        s.add(DevelopmentWorkItem(chat_id=int(dev_chat.id), identity_key=f"chat:{int(dev_chat.id)}", source_type="prompt"))
        board = KanbanBoard(name="Test board")
        s.add(board)
        s.flush()
        lane = KanbanLane(board_id=board.id, name="To Do", position=0)
        s.add(lane)
        s.flush()
        agent_ticket = KanbanTicket(lane_id=lane.id, title="Agent-linked", source_chat_id=int(agent_chat.id))
        dev_ticket = KanbanTicket(lane_id=lane.id, title="Dev-linked", source_chat_id=int(dev_chat.id))
        s.add_all([agent_ticket, dev_ticket])
        s.flush()
        return int(board.id), int(agent_chat.id), int(dev_chat.id), int(agent_ticket.id), int(dev_ticket.id)


def test_ticket_delete_with_delete_thread_skips_agent_conversations(board_api):
    client, get_session = board_api
    _board, agent_chat, dev_chat, agent_ticket, dev_ticket = _seed_board(get_session)

    response = client.delete(f"/api/tickets/tickets/{agent_ticket}?delete_thread=true")
    assert response.status_code == 200, response.text
    assert response.json()["deleted_thread_id"] is None
    assert _exists(agent_chat, get_session)

    response = client.delete(f"/api/tickets/tickets/{dev_ticket}?delete_thread=true")
    assert response.status_code == 200, response.text
    assert response.json()["deleted_thread_id"] == dev_chat
    assert not _exists(dev_chat, get_session)


def test_board_delete_cascade_skips_agent_conversations(board_api):
    client, get_session = board_api
    board_id, agent_chat, dev_chat, _a, _d = _seed_board(get_session)

    response = client.delete(f"/api/tickets/boards/{board_id}")
    assert response.status_code == 200, response.text
    assert _exists(agent_chat, get_session)
    assert not _exists(dev_chat, get_session)
