"""Read response button: spoken rewrite service + routes (LLM and speech mocked)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core import read_response as rr
from distr.core.db import Base, Chat

SETTINGS = {
    "conversational_llm_provider": "openai",
    "conversational_llm_model": "chat-model-x",
    "coding_llm_provider": "gemini",
    "coding_llm_model": "flash-dev",
}


def test_resolve_oracle_model_uses_conversational_not_dev():
    provider, model = rr.resolve_oracle_model(SETTINGS)
    assert model == "chat-model-x"
    assert provider.lower() == "openai"


def test_resolve_falls_back_to_agent_model():
    provider, model = rr.resolve_oracle_model({"agent_provider": "ollama", "agent_model": "m1"})
    assert (provider.lower(), model) == ("ollama", "m1")


def test_prompt_forbids_markdown_paths_urls():
    msgs = rr.build_messages("| a | b |")
    sys_prompt = msgs[0]["content"].lower()
    for word in ("markdown", "tables", "code", "paths", "urls", "summarise"):
        assert word in sys_prompt
    assert "| a | b |" in msgs[1]["content"]


def test_generate_spoken_text_calls_llm_with_oracle_model_and_sanitizes():
    fake = MagicMock(return_value="## Done\n- Edited `/Users/p/x.py` see https://x.io/a\n**All good.**")
    spoken, provider, model = rr.generate_spoken_text("written reply", SETTINGS, complete=fake)
    args = fake.call_args[0]
    assert args[1] == "chat-model-x"
    assert "#" not in spoken and "`" not in spoken and "**" not in spoken
    assert "https://" not in spoken and "/Users/" not in spoken
    assert "All good." in spoken


def test_generate_rejects_empty():
    with pytest.raises(ValueError):
        rr.generate_spoken_text("  ", SETTINGS, complete=MagicMock())


def test_store_spoken_message_appends_assistant_row_with_marker():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Chat.__table__])
    session = sessionmaker(bind=engine)()
    root = Chat(title="t", input="hi", response="long reply", provider="Gemini", model_name="flash-dev")
    session.add(root)
    session.commit()
    row_id = rr.store_spoken_message(session, root.id, "Short spoken.", source_row_id=root.id, provider="OpenAI", model="chat-model-x")
    row = session.get(Chat, row_id)
    assert row.parent_id == root.id and row.input is None and row.response == "Short spoken."
    ctx = json.loads(row.additional_context)
    assert ctx["spoken"] is True and ctx["spoken_source_row_id"] == root.id and ctx["spoken_model"] == "chat-model-x"


def test_speak_spoken_text_uses_direct_signal_mocked():
    with patch("distr.core.signals.speak_text_directly_event_queue") as speak:
        assert rr.speak_spoken_text("Hello there.") is True
        speak.assert_called_once_with("Hello there.")
        assert rr.speak_spoken_text("   ") is False
        assert speak.call_count == 1


@pytest.fixture()
def client():
    from distr.gui.web.routes import chat as chat_routes

    from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token

    app = FastAPI()
    app.include_router(chat_routes.create_routes(Path("."), ""), prefix="/api")
    # speak-spoken now has its own localhost + internal-token guard.
    return (
        TestClient(app, client=("127.0.0.1", 50000), headers={INTERNAL_AUTH_HEADER: get_internal_api_token()}),
        chat_routes,
    )


def _fake_session(chat_exists=True):
    sess = MagicMock()
    sess.query.return_value.filter.return_value.first.return_value = MagicMock() if chat_exists else None

    @contextmanager
    def cm():
        yield sess

    return cm


def test_read_response_route(client):
    tc, chat_routes = client
    with patch.object(chat_routes, "load_settings_from_db", return_value=SETTINGS), \
         patch.object(chat_routes, "get_session", _fake_session()), \
         patch("distr.core.read_response._default_complete", return_value="It worked fine."), \
         patch("distr.core.read_response.store_spoken_message", return_value=99) as store, \
         patch("distr.core.signals.speak_text_directly_event_queue") as speak:
        r = tc.post("/api/chats/5/read-response", json={"text": "# Title\nBody", "chat_row_id": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"spoken_text": "It worked fine.", "chat_row_id": 99, "provider": body["provider"], "model": "chat-model-x"}
    assert store.call_args.kwargs["source_row_id"] == 7
    speak.assert_not_called()  # generation never speaks by itself


def test_read_response_route_llm_error_is_502(client):
    tc, chat_routes = client
    with patch.object(chat_routes, "load_settings_from_db", return_value=SETTINGS), \
         patch("distr.core.read_response._default_complete", side_effect=RuntimeError("down")):
        r = tc.post("/api/chats/5/read-response", json={"text": "hello"})
    assert r.status_code == 502


def test_speak_spoken_route_mocked(client):
    tc, _ = client
    with patch("distr.core.signals.speak_text_directly_event_queue") as speak:
        r = tc.post("/api/chats/speak-spoken", json={"text": "Short spoken."})
    assert r.json() == {"ok": True, "oracle_note_row_id": None}
    speak.assert_called_once_with("Short spoken.")


def test_spoken_flag_serialized():
    from distr.gui.web.routes.chat import _append_row_messages

    row = MagicMock(is_hidden=False, input=None, response="Short.", additional_context=json.dumps({"spoken": True}))
    msgs = []
    _append_row_messages(msgs, row)
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["spoken"] is True


def _sqlite_thread_with_spoken():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Chat.__table__])
    Session = sessionmaker(bind=engine)
    s = Session()
    root = Chat(title="t", input="what is the main feature?", response="Long **markdown** answer")
    s.add(root)
    s.commit()
    rr.store_spoken_message(s, root.id, "The main feature is the Oracle.", source_row_id=root.id, provider="OpenAI", model="m")
    rid = root.id
    s.close()

    @contextmanager
    def cm():
        sess = Session()
        try:
            yield sess
        finally:
            sess.close()

    return rid, cm


def test_chat_service_history_includes_labelled_spoken_row():
    from distr.core import chat as chat_mod

    rid, cm = _sqlite_thread_with_spoken()
    with patch.object(chat_mod, "get_session", cm), \
         patch.object(chat_mod, "get_compact_checkpoint_prompt", return_value=None):
        history = chat_mod.ChatService.get_chat_history(rid)
    assert [m["role"] for m in history] == ["user", "assistant", "assistant"]
    assert history[-1]["content"] == chat_mod.SPOKEN_HISTORY_PREFIX + "The main feature is the Oracle."
    assert set(history[-1]) == {"role", "content"}  # no provider-unsafe extra keys


def test_chat_manager_merges_spoken_rows_into_stale_cache():
    from distr.core.chat import SPOKEN_HISTORY_PREFIX
    from distr.core.chat_manager import ChatManagerCore as ChatManager

    mgr = ChatManager.__new__(ChatManager)
    spoken = SPOKEN_HISTORY_PREFIX + "Short."
    mgr.chat_histories = {1: [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}, {"role": "assistant", "content": "long"}]}
    db = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "long"}, {"role": "assistant", "content": spoken}]
    with patch("distr.core.chat.ChatService.get_chat_history", return_value=db):
        mgr._merge_spoken_rows(1)
        mgr._merge_spoken_rows(1)  # idempotent
    assert mgr.chat_histories[1][-1] == {"role": "assistant", "content": spoken}
    assert sum(1 for m in mgr.chat_histories[1] if m["content"] == spoken) == 1


def test_spoken_source_row_id_serialized():
    from distr.gui.web.routes.chat import _append_row_messages

    row = MagicMock(is_hidden=False, input=None, response="Short.", additional_context=json.dumps({"spoken": True, "spoken_source_row_id": 1748}))
    msgs = []
    _append_row_messages(msgs, row)
    assert msgs[-1]["spoken_source_row_id"] == 1748


# ---- oracle context note + dedup (ctxnote) ----

def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Chat.__table__])
    Session = sessionmaker(bind=engine)

    @contextmanager
    def cm():
        sess = Session()
        try:
            yield sess
        finally:
            sess.close()

    s = Session()
    thread = Chat(title="what theme is this project", input="q", response="long reply")
    oracle = Chat(title="Oracle chat", input="hi", response="hello")
    s.add_all([thread, oracle])
    s.commit()
    ids = (thread.id, oracle.id)
    s.close()
    return Session, cm, ids


def test_find_existing_spoken_ignores_oracle_notes():
    Session, _, (tid, oid) = _db()
    s = Session()
    assert rr.find_existing_spoken(s, tid, tid) is None
    rid = rr.store_spoken_message(s, tid, "Navy and orange.", source_row_id=tid, provider="p", model="m")
    assert rr.find_existing_spoken(s, tid, tid) == (rid, "Navy and orange.")
    assert rr.find_existing_spoken(s, tid, None) is None
    assert rr.find_existing_spoken(s, oid, tid) is None


def test_oracle_note_inserted_once_and_skipped_for_same_or_none():
    Session, _, (tid, oid) = _db()
    s = Session()
    assert rr.add_oracle_context_note(s, tid, "Navy.", source_row_id=tid, current_chat_id=None) is None
    assert rr.add_oracle_context_note(s, tid, "Navy.", source_row_id=tid, current_chat_id=tid) is None
    nid = rr.add_oracle_context_note(s, tid, "Navy.", source_row_id=tid, current_chat_id=oid)
    row = s.get(Chat, nid)
    assert row.parent_id == oid
    assert row.response == f"Spoken aloud to the user from Development thread {tid} ('what theme is this project'): Navy."
    ctx = json.loads(row.additional_context)
    assert ctx["spoken"] is True and ctx["oracle_note"] is True and ctx["source_thread_id"] == tid
    # Play again: no duplicate (same source), and no duplicate without source when text matches
    assert rr.add_oracle_context_note(s, tid, "Navy.", source_row_id=tid, current_chat_id=oid) is None
    assert rr.add_oracle_context_note(s, tid, "Navy.", source_row_id=None, current_chat_id=oid) is None
    assert s.query(Chat).filter(Chat.parent_id == oid).count() == 1


def test_oracle_note_history_label_not_double_prefixed():
    from distr.core import chat as chat_mod

    Session, cm, (tid, oid) = _db()
    s = Session()
    rr.add_oracle_context_note(s, tid, "Navy.", source_row_id=tid, current_chat_id=oid)
    s.close()
    with patch.object(chat_mod, "get_session", cm), patch.object(chat_mod, "get_compact_checkpoint_prompt", return_value=None):
        history = chat_mod.ChatService.get_chat_history(oid)
    assert history[-1]["role"] == "assistant"
    assert history[-1]["content"].startswith(chat_mod.SPOKEN_NOTE_PREFIX)
    assert chat_mod.SPOKEN_HISTORY_PREFIX not in history[-1]["content"]


def test_chat_manager_merges_oracle_note():
    from distr.core.chat import SPOKEN_NOTE_PREFIX
    from distr.core.chat_manager import ChatManagerCore

    mgr = ChatManagerCore.__new__(ChatManagerCore)
    note = SPOKEN_NOTE_PREFIX + "1746 ('t'): Navy."
    mgr.chat_histories = {5: [{"role": "user", "content": "q"}]}
    with patch("distr.core.chat.ChatService.get_chat_history", return_value=[{"role": "user", "content": "q"}, {"role": "assistant", "content": note}]):
        mgr._merge_spoken_rows(5)
    assert mgr.chat_histories[5][-1] == {"role": "assistant", "content": note}


def test_read_response_route_returns_existing_without_llm(client):
    tc, chat_routes = client
    with patch.object(chat_routes, "get_session", _fake_session()), \
         patch("distr.core.read_response.find_existing_spoken", return_value=(42, "Already spoken.")), \
         patch("distr.core.read_response._default_complete") as llm, \
         patch("distr.core.read_response.store_spoken_message") as store:
        r = tc.post("/api/chats/5/read-response", json={"text": "x", "chat_row_id": 7})
    assert r.status_code == 200
    assert r.json() == {"spoken_text": "Already spoken.", "chat_row_id": 42, "existing": True}
    llm.assert_not_called()
    store.assert_not_called()


def test_speak_spoken_route_adds_oracle_note(client):
    tc, chat_routes = client
    with patch("distr.core.signals.speak_text_directly_event_queue") as speak, \
         patch.object(chat_routes.ChatService, "get_current_chat_id", return_value=1652), \
         patch.object(chat_routes, "get_session", _fake_session()), \
         patch("distr.core.read_response.add_oracle_context_note", return_value=77) as note:
        r = tc.post("/api/chats/speak-spoken", json={"text": "Navy.", "chat_id": 1746, "source_row_id": 1757})
    assert r.json() == {"ok": True, "oracle_note_row_id": 77}
    speak.assert_called_once_with("Navy.")
    assert note.call_args.args[1:] == (1746, "Navy.")
    assert note.call_args.kwargs == {"source_row_id": 1757, "current_chat_id": 1652}


def test_speak_spoken_route_without_thread_adds_no_note(client):
    tc, chat_routes = client
    with patch("distr.core.signals.speak_text_directly_event_queue"), \
         patch("distr.core.read_response.add_oracle_context_note") as note:
        r = tc.post("/api/chats/speak-spoken", json={"text": "Navy."})
    assert r.json() == {"ok": True, "oracle_note_row_id": None}
    note.assert_not_called()
