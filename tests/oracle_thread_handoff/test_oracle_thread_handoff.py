"""Run with HOME and DECISIONS_DB_DIR pointing at a /tmp copy of the DB."""
import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    not str(os.environ.get("DECISIONS_DB_DIR", "")).startswith(("/tmp", "/private/tmp")),
    reason="needs DECISIONS_DB_DIR on a /tmp DB copy",
)

BUG_TEXT = (
    "if you basically start doing any sort of development work can you basically put it in the decisions "
    "thread that we currently work with or can we create a new thread can you create a new thread in the "
    "project decisions AI and what I want you to do is create an MD file on my downloads folder called Bob "
    "and I want you to put 15 vegetables in that file and then open the file but I need you to put all these "
    "instructions inside a thread"
)


def test_mentions_development_thread():
    from distr.core.workflow.oracle_thread_handoff import mentions_development_thread as m
    assert m(BUG_TEXT)
    assert m("put this in the development thread")
    assert m("create a new thread in project DecisionsAI")
    assert not m("new chat")
    assert not m("start a new conversation")
    assert not m("list my downloads folder")


def test_fast_action_does_not_hijack_dev_thread_request():
    from distr.core.agent.services.llm.fast_action_detector import ActionType, FastActionDetector
    d = FastActionDetector()
    assert d.detect(BUG_TEXT).action_type == ActionType.UNKNOWN
    assert d.detect("new chat").tool_name == "new_chat"


class _CM:
    def __init__(self):
        self.current = 1652
        self.created = []
    def get_current_chat(self):
        return self.current
    def create_chat(self, title, input_text="", is_new=True):
        self.created.append(title)
        self.current = 99999
        return 99999
    def set_current_chat(self, cid):
        self.current = cid


def test_new_chat_refuses_dev_thread_requests():
    from distr.core.agent.tools.chat.new_chat import NewChatTool
    cm = _CM()
    out = json.loads(NewChatTool(chat_manager=cm)._run(last_user_message=BUG_TEXT))
    assert out["status"] == "refused" and "development_control" in out["reason"]
    assert cm.created == []


def test_new_chat_closes_originating_turn(monkeypatch):
    import distr.core.chat_turns as ct
    from distr.core.agent.tools.chat.new_chat import NewChatTool
    closed = []
    monkeypatch.setattr(ct, "complete_turn", lambda cid, **kw: closed.append((cid, kw)))
    cm = _CM()
    out = json.loads(NewChatTool(chat_manager=cm)._run(last_user_message="new chat"))
    # routee2e 2026-10-22 (Paul): new_chat is now hard-refused for every instruction.
    assert out["status"] == "refused"
    assert cm.created == []


def _make_thread(title="handoff test"):
    from distr.core.workflow.development_threads import ensure_development_thread
    return int(ensure_development_thread(title=title, project_id=None, source_type="orchestrator"))


def test_send_instruction_runs_in_existing_thread():
    from distr.core.db import Chat, get_session
    from distr.core.workflow.oracle_thread_handoff import send_instruction_to_thread
    tid = _make_thread()
    calls = []
    r = send_instruction_to_thread(tid, "create Bob.md", _dispatch=lambda cid, msg, **kw: calls.append((cid, msg)) or {"job_id": "j1"})
    assert r["started"] and r["thread_id"] == tid and r["job_id"] == "j1"
    assert calls == [(tid, "create Bob.md")]
    with get_session() as db:
        kids = db.query(Chat).filter(Chat.parent_id == tid).all()
        assert any(k.input == "create Bob.md" for k in kids)
    # identical immediate resend is de-duplicated, not dispatched twice
    r2 = send_instruction_to_thread(tid, "create Bob.md", _dispatch=lambda *a, **k: calls.append(a))
    assert r2.get("duplicate") and len(calls) == 1


def test_send_instruction_rejects_non_development_chat():
    from distr.core.workflow.oracle_thread_handoff import send_instruction_to_thread
    r = send_instruction_to_thread(1652, "x", _dispatch=lambda *a, **k: pytest.fail("dispatched"))
    assert not r["started"] and "not a Development thread" in r["error"]


def test_development_control_send_and_create_start(monkeypatch):
    import distr.core.workflow.oracle_thread_handoff as h
    from distr.core.agent.tools.integrations.development_control import DevelopmentControlTool
    seen = []
    monkeypatch.setattr(h, "send_instruction_to_thread", lambda tid, msg: seen.append((tid, msg)) or {"started": True, "thread_id": tid})
    tool = DevelopmentControlTool()
    out = json.loads(tool._run(action="send_instruction", thread_id=1746, instruction="make Bob.md"))
    assert out["started"] and seen[-1] == (1746, "make Bob.md")
    out = json.loads(tool._run(action="create_thread", project_id=None, title="Bob",
                               instruction="Create ~/Downloads/Bob.md listing 15 vegetables"))
    assert out["created"] and out["run"]["started"] and seen[-1][0] == out["thread_id"]
