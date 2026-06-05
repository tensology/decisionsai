"""Tests for ticket lane moves posting notices back to source chat."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket


def _memory_ctx(monkeypatch, *patch_targets: str):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    sess_holder = {"s": SessionLocal()}

    class CM:
        def __enter__(self):
            return sess_holder["s"]

        def __exit__(self, *args):
            sess_holder["s"].commit()

    for target in patch_targets:
        monkeypatch.setattr(target, lambda: CM())
    return sess_holder["s"]


def test_append_assistant_notice_creates_child_row(monkeypatch):
    s = _memory_ctx(monkeypatch, "distr.core.chat.get_session")
    root = Chat(
        parent_id=None,
        title="Root",
        provider="Ollama",
        model_name="m",
    )
    s.add(root)
    s.commit()

    from distr.core.chat import ChatService

    assert ChatService.append_assistant_notice(root.id, "Board update happened.")

    rows = s.query(Chat).filter(Chat.parent_id == root.id).all()
    assert len(rows) == 1
    assert rows[0].input is None
    assert "Board update happened." in (rows[0].response or "")


def test_notify_source_chat_ticket_moved_calls_persist_and_signals(monkeypatch):
    s = _memory_ctx(monkeypatch, "distr.core.db.get_session")

    board = KanbanBoard(name="B1")
    s.add(board)
    s.flush()
    lane = KanbanLane(board_id=board.id, name="L1", position=0)
    s.add(lane)
    s.flush()
    chat_root = Chat(parent_id=None, title="Chat", provider="Ollama", model_name="x")
    s.add(chat_root)
    s.flush()
    ticket = KanbanTicket(
        lane_id=lane.id,
        title="Fix it",
        description="d",
        priority="medium",
        position=0,
        source_chat_id=chat_root.id,
    )
    s.add(ticket)
    s.commit()

    persisted = []

    def fake_append(cid, msg, hidden=False):
        persisted.append((cid, msg))
        return True

    monkeypatch.setattr(
        "distr.core.chat.ChatService.append_assistant_notice",
        fake_append,
    )
    emit_mock = MagicMock()
    upd_mock = MagicMock()
    monkeypatch.setattr(
        "distr.core.signals.signal_manager",
        SimpleNamespace(
            chat_message_added=SimpleNamespace(emit=emit_mock),
            chat_updated=SimpleNamespace(emit=upd_mock),
        ),
    )

    from distr.core.kanban.ticket_chat_notify import notify_source_chat_ticket_moved

    notify_source_chat_ticket_moved(
        ticket.id,
        board_name="B1",
        to_lane_name="Done",
        reason="workflow_completed",
    )

    assert len(persisted) == 1
    assert persisted[0][0] == chat_root.id
    assert "advanced to lane \"Done\"" in persisted[0][1]
    assert str(ticket.id) in persisted[0][1]
    emit_mock.assert_called()
    upd_mock.assert_called()
