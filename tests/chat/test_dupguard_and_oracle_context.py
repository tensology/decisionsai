"""Oracle note reaches the LLM prompt; duplicate Development submits are rejected."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core import chat as chat_mod
from distr.core.db import Base, Chat


# ---------- oracle note in the live LLM context ----------

def _llm(messages):
    from distr.core.agent.services.llm import core_mixin

    cls = next(v for v in vars(core_mixin).values() if isinstance(v, type) and hasattr(v, "_ensure_user_message_persisted"))
    obj = cls.__new__(cls)
    obj._messages = messages
    obj._is_telegram_request = False
    obj.chat_manager = SimpleNamespace(get_current_chat=lambda: 1652, add_user_message=lambda cid, t, source_platform=None: 999)
    obj._notify_transcription_progress = lambda *a, **k: None
    return obj


NOTE = chat_mod.SPOKEN_NOTE_PREFIX + "1746 ('t'): The funniest release is the Oracle Skins System."
HISTORY = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "I'm here, Paul."},
    {"role": "assistant", "content": NOTE},
]


def test_note_saved_after_chat_load_reaches_prompt_before_user_message():
    # Context was loaded at startup (before the note existed), exactly like the live repro.
    llm = _llm([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}, {"role": "assistant", "content": "I'm here, Paul."}])
    with patch.object(chat_mod.ChatService, "get_chat_history", return_value=HISTORY), \
         patch("distr.core.chat_manager.bind_active_chat_turn"), \
         patch("distr.core.agent.services.llm.core_mixin.signal_manager"):
        llm._ensure_user_message_persisted("What did you just talk about?")
    llm._messages.append({"role": "user", "content": "What did you just talk about?"})  # as the voice/typed path does next
    assert llm._messages[-2] == {"role": "assistant", "content": NOTE}
    assert llm._messages[-1]["content"] == "What did you just talk about?"


def test_note_sync_on_web_persisted_skip_path_and_is_idempotent():
    llm = _llm([{"role": "system", "content": "sys"}])
    with patch.object(chat_mod.ChatService, "get_chat_history", return_value=HISTORY), \
         patch("distr.core.chat_manager.bind_active_chat_turn"), \
         patch("distr.core.chat_turns.resolve_active_chat_turn_row_id", return_value=1):
        llm._ensure_user_message_persisted("q", skip=True)
        llm._ensure_user_message_persisted("q", skip=True)
    assert sum(1 for m in llm._messages if m["content"] == NOTE) == 1
    assert all(m["content"] != "I'm here, Paul." for m in llm._messages)  # only spoken rows are synced


# ---------- duplicate submit guard ----------

def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Chat.__table__])
    Session = sessionmaker(bind=engine)

    @contextmanager
    def cm():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    s = Session()
    root = Chat(title="t", input="q0", response="a0")
    s.add(root)
    s.commit()
    rid = root.id
    s.close()
    return Session, cm, rid


def _add_user(Session, rid, text, *, age_s=0.0, response=None):
    s = Session()
    when = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=age_s)
    s.add(Chat(parent_id=rid, input=text, response=response, created_date=when, modified_date=when))
    s.commit()
    s.close()


def test_duplicate_pending_input_rules():
    Session, cm, rid = _db()
    with patch.object(chat_mod, "get_session", cm):
        assert chat_mod.is_duplicate_pending_input(rid, "fun thing?") is False
        _add_user(Session, rid, "fun thing?", age_s=1)
        assert chat_mod.is_duplicate_pending_input(rid, " fun thing? ") is True      # double submit
        assert chat_mod.is_duplicate_pending_input(rid, "other question") is False  # different text
        assert chat_mod.is_duplicate_pending_input(rid, "fun thing?", window_sec=0.5) is False  # outside window
    Session2, cm2, rid2 = _db()
    _add_user(Session2, rid2, "fun thing?", age_s=1, response="answered")
    with patch.object(chat_mod, "get_session", cm2):
        assert chat_mod.is_duplicate_pending_input(rid2, "fun thing?") is False     # already answered: deliberate re-ask OK


def test_duplicate_instruction_checks_pending_row():
    from distr.core.workflow import development_control as dc

    with patch.object(chat_mod, "is_duplicate_pending_input", return_value=True):
        assert dc.is_duplicate_instruction(1746, "fun thing?") is True
    assert dc.is_duplicate_instruction(1746, "   ") is False
