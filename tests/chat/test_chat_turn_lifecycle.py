from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base, Chat, ChatTurnEvent
from distr.core import chat_turns
from distr.gui.web.routes import chat as chat_routes


def _factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


@contextlib.contextmanager
def _ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _chat(factory) -> tuple[int, int]:
    with factory() as session:
        root = Chat(title="Lifecycle", provider="Ollama", model_name="ornith:test")
        session.add(root)
        session.commit()
        turn = Chat(parent_id=root.id, input="Please inspect this", response="")
        session.add(turn)
        session.commit()
        root.params = f'{{"active_turn_chat_row_id": {turn.id}}}'
        session.commit()
        return int(root.id), int(turn.id)


def test_schema_and_ordered_start_to_terminal_update(monkeypatch):
    engine, factory = _factory()
    assert "chat_turn_events" in inspect(engine).get_table_names()
    root_id, turn_id = _chat(factory)
    monkeypatch.setattr(chat_turns, "get_session", lambda: _ctx(factory))
    monkeypatch.setattr(chat_turns, "_broadcast", lambda payload: None)

    started = chat_turns.ensure_turn_started(root_id, turn_id)
    event_id, created_ack, _ = chat_turns.start_tool(root_id, "file_operations")
    assert started and started["sequence"] == 1
    assert created_ack is True
    assert event_id

    completed = chat_turns.finish_tool(
        event_id,
        success=True,
        summary="Inspected the files",
        detail="No issue found",
    )
    assert completed["event_id"] == event_id
    assert completed["sequence"] == 3
    assert completed["event_type"] == "tool_completed"

    second_id, second_ack, _ = chat_turns.start_tool(root_id, "browser_search")
    assert second_id and second_ack is False
    chat_turns.finish_tool(second_id, success=False, summary="Provider failed")
    chat_turns.begin_synthesis(root_id)

    active = chat_turns.get_turns(root_id)["active_turn"]
    assert active["turn_id"] == turn_id
    assert [event["sequence"] for event in active["events"]] == list(
        range(1, len(active["events"]) + 1)
    )
    assert len([e for e in active["events"] if e["event_type"] == "acknowledgment"]) == 1

    chat_turns.complete_turn(
        root_id,
        display_text="The files are ready.",
        speech_text="The files are ready.",
    )
    state = chat_turns.get_turns(root_id)
    assert state["active_turn"] is None
    assert state["turns"][0]["status"] == "completed"


def test_redaction_and_bounded_metadata(monkeypatch):
    _, factory = _factory()
    root_id, turn_id = _chat(factory)
    monkeypatch.setattr(chat_turns, "get_session", lambda: _ctx(factory))
    monkeypatch.setattr(chat_turns, "_broadcast", lambda payload: None)

    event = chat_turns.create_event(
        root_id,
        "tool_started",
        turn_id=turn_id,
        title="Calling API",
        detail="Authorization: Bearer abcdefghijklmnop /Users/paul/private/file.txt",
        metadata={
            "api_key": "sk-super-secret-value",
            "arguments": {"password": "hunter2"},
            "safe": "/tmp/output.json",
        },
    )
    serialized = str(event)
    assert "abcdefghijklmnop" not in serialized
    assert "sk-super-secret-value" not in serialized
    assert "hunter2" not in serialized
    assert "/Users/paul" not in serialized
    assert "/tmp/output.json" not in serialized
    assert "[redacted]" in serialized


def test_steer_endpoint_persists_guidance_without_cancelling(monkeypatch):
    _, factory = _factory()
    root_id, turn_id = _chat(factory)
    monkeypatch.setattr(chat_turns, "get_session", lambda: _ctx(factory))
    monkeypatch.setattr(chat_turns, "_broadcast", lambda payload: None)
    monkeypatch.setattr(chat_routes, "get_session", lambda: _ctx(factory))
    chat_turns.ensure_turn_started(root_id, turn_id)

    app = FastAPI()
    app.include_router(chat_routes.create_routes(Path(__file__).parent), prefix="/api")
    response = TestClient(app).post(
        f"/api/chats/{root_id}/turns/{turn_id}/steer",
        json={"message": "Keep the final answer concise."},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["event"]["event_type"] == "turn_steered"
    assert payload["event"]["status"] == "completed"
    pending = chat_turns.pending_steering(root_id, turn_id)
    assert [item["summary"] for item in pending] == ["Keep the final answer concise."]


def test_get_chat_turns_are_durable_not_params_json(monkeypatch):
    _, factory = _factory()
    root_id, turn_id = _chat(factory)
    monkeypatch.setattr(chat_turns, "get_session", lambda: _ctx(factory))
    monkeypatch.setattr(chat_turns, "_broadcast", lambda payload: None)
    chat_turns.ensure_turn_started(root_id, turn_id)
    event_id, _, _ = chat_turns.start_tool(root_id, "execute_code")

    with factory() as session:
        assert session.query(ChatTurnEvent).count() == 3
        root = session.get(Chat, root_id)
        assert "tool_events" not in (root.params or "")
    assert chat_turns.get_turns(root_id)["active_turn"]["last_sequence"] == 3
    assert event_id


def test_ten_tool_rounds_share_one_ack_and_one_terminal_state(monkeypatch):
    _, factory = _factory()
    root_id, turn_id = _chat(factory)
    monkeypatch.setattr(chat_turns, "get_session", lambda: _ctx(factory))
    monkeypatch.setattr(chat_turns, "_broadcast", lambda payload: None)

    event_ids = []
    for index in range(10):
        event_id, _, _ = chat_turns.start_tool(root_id, f"tool_{index}")
        event_ids.append(event_id)
        chat_turns.finish_tool(
            event_id,
            success=True,
            summary=f"Round {index + 1} complete",
        )

    before_terminal = chat_turns.get_turns(root_id)["active_turn"]
    assert len({event_id for event_id in event_ids if event_id}) == 10
    assert len([e for e in before_terminal["events"] if e["event_type"] == "acknowledgment"]) == 1
    assert len([e for e in before_terminal["events"] if e["event_type"] == "tool_completed"]) == 10
    assert [e["sequence"] for e in before_terminal["events"]] == list(range(1, 13))

    failed = chat_turns.terminal_turn(root_id, "turn_failed", summary="The provider timed out")
    late_completion = chat_turns.complete_turn(root_id, turn_id=turn_id, display_text="Late answer")
    assert failed["event_type"] == "turn_failed"
    assert late_completion["event_id"] == failed["event_id"]
    assert chat_turns.get_turns(root_id)["active_turn"] is None


def test_pending_steering_is_injected_once_at_safe_boundary(monkeypatch):
    _, factory = _factory()
    root_id, turn_id = _chat(factory)
    monkeypatch.setattr(chat_turns, "get_session", lambda: _ctx(factory))
    monkeypatch.setattr(chat_turns, "_broadcast", lambda payload: None)
    chat_turns.ensure_turn_started(root_id, turn_id)
    chat_turns.steer_turn(root_id, turn_id, "Focus on the regression and keep the answer concise.")

    service = type("Service", (), {"_messages": []})()
    first = chat_turns.apply_pending_steering_to_messages(service, root_id)
    second = chat_turns.apply_pending_steering_to_messages(service, root_id)

    assert "Focus on the regression" in first
    assert second == ""
    assert len(service._messages) == 1
    assert chat_turns.pending_steering(root_id, turn_id) == []
