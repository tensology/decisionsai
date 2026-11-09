"""Read-aloud for Incoming proposals: spoken summary, oracle note, dedup, errors."""
from contextlib import contextmanager

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core import read_response as rr
from distr.core.chat import ChatService
from distr.core.db import Base, Chat
from distr.gui.web.routes.development import incoming as incoming_routes

READ_PATH = "/workflows/studio/incoming/proposals/read"
SPOKEN = "A short spoken summary of the proposal."


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    return session_scope


def _client(monkeypatch, session_scope, *, current_chat_id=None, generate=None, speak=None):
    monkeypatch.setattr(rr, "generate_spoken_text", generate or (lambda text, settings: (SPOKEN, "ollama", "test-model")))
    monkeypatch.setattr(rr, "speak_spoken_text", speak or (lambda text: True))
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {})
    monkeypatch.setattr("distr.core.db.get_session", session_scope)
    monkeypatch.setattr(ChatService, "get_current_chat_id", lambda: current_chat_id)

    router = APIRouter()
    incoming_routes.register_routes(router, None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _seed_oracle(session_scope, chat_id):
    with session_scope() as session:
        session.add(Chat(id=chat_id, title="Oracle chat", provider="ollama", model_name="gpt-5.6-luna"))


def test_read_speaks_summary_and_writes_one_oracle_note(monkeypatch):
    session_scope = _session_factory()
    _seed_oracle(session_scope, 42)
    client = _client(monkeypatch, session_scope, current_chat_id=42)

    body = {"text": "Fix the DPP sync", "proposal_id": "local:gmail:1", "title": "DPP sync"}
    first = client.post(READ_PATH, json=body)
    assert first.status_code == 200
    payload = first.json()
    assert payload["success"] is True
    assert payload["spoken_text"] == SPOKEN
    assert payload["provider"] == "ollama"
    assert payload["oracle_note_row_id"] is not None

    second = client.post(READ_PATH, json=body)
    assert second.status_code == 200

    with session_scope() as session:
        notes = session.query(Chat).filter(Chat.additional_context.like('%"oracle_note": true%')).all()
        assert len(notes) == 1
        assert "Incoming proposal local:gmail:1" in notes[0].response
        assert notes[0].response.endswith(SPOKEN)


def test_read_without_current_chat_speaks_but_writes_no_note(monkeypatch):
    session_scope = _session_factory()
    client = _client(monkeypatch, session_scope, current_chat_id=None)

    response = client.post(READ_PATH, json={"text": "Something", "proposal_id": "p1", "title": "T"})
    assert response.status_code == 200
    assert response.json()["oracle_note_row_id"] is None
    with session_scope() as session:
        assert session.query(Chat).count() == 0


def test_read_requires_text(monkeypatch):
    session_scope = _session_factory()
    client = _client(monkeypatch, session_scope)
    response = client.post(READ_PATH, json={"text": "   ", "proposal_id": "p1"})
    assert response.status_code == 422
    assert response.json()["success"] is False


def test_read_reports_generation_failure(monkeypatch):
    def boom(text, settings):
        raise RuntimeError("model down")

    session_scope = _session_factory()
    client = _client(monkeypatch, session_scope, generate=boom)
    response = client.post(READ_PATH, json={"text": "Something", "proposal_id": "p1"})
    assert response.status_code == 502
    assert response.json()["success"] is False


def test_model_configuration_error_is_not_reported_as_empty_text(monkeypatch):
    def missing_key(text, settings):
        raise ValueError("OpenAI API key not configured")

    client = _client(monkeypatch, _session_factory(), generate=missing_key)
    response = client.post(READ_PATH, json={"text": "A nonempty proposal"})
    assert response.status_code == 502
    assert response.json()["error"] == "Could not prepare the spoken version."


def test_read_reports_unavailable_speech_without_writing_context(monkeypatch):
    session_scope = _session_factory()
    _seed_oracle(session_scope, 7)
    client = _client(monkeypatch, session_scope, current_chat_id=7, speak=lambda text: False)
    response = client.post(READ_PATH, json={"text": "Something", "proposal_id": "p1", "title": "T"})
    assert response.status_code == 503
    payload = response.json()
    assert payload["success"] is False
    assert "Speech is unavailable" in payload["error"]
    with session_scope() as session:
        assert session.query(Chat).count() == 1


def test_read_reports_speech_dispatch_exception(monkeypatch):
    def fail(text):
        raise RuntimeError("queue stopped")

    client = _client(monkeypatch, _session_factory(), speak=fail)
    response = client.post(READ_PATH, json={"text": "Something"})
    assert response.status_code == 503
    assert response.json()["success"] is False


def test_read_rejects_non_text_and_oversized_input(monkeypatch):
    client = _client(monkeypatch, _session_factory())
    for text in (None, [], {}, 123):
        assert client.post(READ_PATH, json={"text": text}).status_code == 422
    assert client.post(READ_PATH, json={"text": "x" * (rr.MAX_INPUT_CHARS + 1)}).status_code == 413


def test_read_timeout_does_not_queue_late_speech(monkeypatch):
    import time

    calls = []
    def slow(text, settings):
        time.sleep(0.05)
        return SPOKEN, "ollama", "test-model"

    client = _client(monkeypatch, _session_factory(), generate=slow, speak=lambda text: calls.append(text))
    monkeypatch.setattr(incoming_routes, "PROPOSAL_READ_TIMEOUT_SECONDS", 0.001)
    response = client.post(READ_PATH, json={"text": "Something"})
    assert response.status_code == 504
    assert calls == []


def test_cancelled_read_does_not_speak_or_write_context(monkeypatch):
    calls = []
    client = _client(monkeypatch, _session_factory(), speak=lambda text: calls.append(text))

    async def disconnected(self):
        return True

    monkeypatch.setattr("starlette.requests.Request.is_disconnected", disconnected)
    response = client.post(READ_PATH, json={"text": "Something"})
    assert response.status_code == 499
    assert calls == []


def test_context_is_kept_with_chat_that_started_the_read(monkeypatch):
    session_scope = _session_factory()
    _seed_oracle(session_scope, 7)
    _seed_oracle(session_scope, 8)

    def generate(text, settings):
        monkeypatch.setattr(ChatService, "get_current_chat_id", lambda: 8)
        return SPOKEN, "ollama", "test-model"

    client = _client(monkeypatch, session_scope, current_chat_id=7, generate=generate)
    response = client.post(READ_PATH, json={"text": "Something", "proposal_id": "p1"})
    assert response.status_code == 200
    with session_scope() as session:
        note = session.get(Chat, response.json()["oracle_note_row_id"])
        assert note.parent_id == 7
