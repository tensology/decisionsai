from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base, Chat
from distr.gui.web.routes import chat as chat_routes


class _Signal:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def emit(self, *args: object) -> None:
        self.calls.append(args)


def _make_factory():
    engine = create_engine(
        "sqlite:///:memory:",
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


def test_send_to_agent_emits_and_echoes_intake_identity(monkeypatch) -> None:
    factory = _make_factory()
    with factory() as session:
        chat = Chat(title="Qualification", provider="Ollama", model_name="test-model")
        session.add(chat)
        session.commit()
        chat_id = chat.id

    monkeypatch.setattr(chat_routes, "get_session", lambda: _session_ctx(factory))
    signal = _Signal()
    import distr.core.signals as signals_module

    monkeypatch.setattr(
        signals_module,
        "signal_manager",
        SimpleNamespace(web_send_to_agent_requested=signal),
    )

    app = FastAPI()
    app.include_router(chat_routes.create_routes(Path(__file__).parent), prefix="/api")
    response = TestClient(app).post(
        f"/api/chats/{chat_id}/send-to-agent",
        json={
            "message": "Summarize the qualification evidence.",
            "speak": False,
            "intake_source_message_id": "qualification:research_only:webprobe",
            "intake_requested_outcome": "A concise evidence-backed answer",
            "intake_metadata": {"scenario": "research_only"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "sent": True,
        "intake_source_message_id": "qualification:research_only:webprobe",
    }
    assert signal.calls == [
        (
            chat_id,
            "Summarize the qualification evidence.",
            False,
            "Ollama",
            "test-model",
            {
                "work_intake": {
                    "source_message_id": "qualification:research_only:webprobe",
                    "requested_outcome": "A concise evidence-backed answer",
                    "metadata": {"scenario": "research_only"},
                }
            },
        )
    ]
