"""Development awareness (area 3) and agent loop (area 4).

Reproduces the real cases from Paul's chat history:
  * 11 Nov, turn 1030: "No, read from the clipboard." — the turn stayed open for
    hours and five bank-statement workflow reports were filed under it.
  * 15 Nov, turn 1430: "I just want you to read it." (about Uncle Roger's chat)
    — read the clipboard aloud instead of the conversation.
  * 18 Nov, turn 1543: a Pi Development turn made 80+ Read/Grep/Ls calls with
    no edit until Paul stopped it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from distr.core.agent import tool_relevance as tr
from distr.core.agent.anaphora import should_defer_clipboard_read
from distr.core.agent.loop_guard import ReadLoopGuard, is_harness_read, round_cap_notice
from distr.core.agent.plan_step import is_multi_step_request, with_plan_step
from distr.core.agent.side_effect_confirmation import confirmation_block_reason


class _Tool:
    def __init__(self, name, description=""):
        self.name = name
        self.description = description
        self.calls = []


@pytest.fixture
def db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from distr.core.db import Base, Chat, ChatAgent
    from distr.core.db.projects import Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        merrypak = Project(name="Merrypak", in_use=False)
        session.add(merrypak)
        session.flush()
        dev_chat = Chat(title="Merrypak delivery", input="", response="", project_id=merrypak.id)
        roger_chat = Chat(title="Uncle Roger", input="", response="")
        main = Chat(title="Main", input="", response="")
        session.add_all([dev_chat, roger_chat, main])
        session.flush()
        session.add(ChatAgent(name="Uncle Roger", role="comedian", main_chat_id=roger_chat.id))
        session.add(Chat(parent_id=roger_chat.id, input="Roger, the AI Bandits course needs a funny intro.",
                         response="Haiyaa, fine. Use more MSG."))
        old_turn = Chat(parent_id=main.id, input="No, read from the clipboard.", response="",
                        created_date=datetime.utcnow() - timedelta(hours=3))
        session.add(old_turn)
        session.flush()
        main.params = json.dumps({"active_turn_chat_row_id": old_turn.id})
        session.commit()
        ids = {"merrypak": merrypak.id, "dev_chat": dev_chat.id, "roger_chat": roger_chat.id,
               "main": main.id, "old_turn": old_turn.id}
    monkeypatch.setattr("distr.core.db.get_session", lambda: factory())
    return ids


# --------------------------------------------------------------------------- area 3

@pytest.mark.parametrize("query", [
    "What's going on in the development area?",
    "Which tickets are still open on Merrypak?",
    "Is the terminal for Snuza still running?",
    "What does the plan say for Merrypak?",
    "What is Codex thread doing right now?",
    "What project am I working on?",
])
def test_dev_queries_pin_the_development_tools(query):
    names = tr.intent_tool_names(query)
    for tool in ("development_control", "kanban_ticket", "terminal_overview", "planning"):
        assert tool in names


@pytest.mark.parametrize("query", ["Tell me a joke.", "What's the weather in Cape Town?", "Hello, my name is Paul."])
def test_non_dev_queries_do_not_get_development_tools(query):
    assert not tr.is_development_query(query)


def _filtering_service(monkeypatch, retrieved):
    from distr.core.agent.services.llm.core_mixin import LLMSharedMixin

    class _Retriever:
        def retrieve(self, text, model):
            return list(retrieved)

    monkeypatch.setattr("distr.core.agent.tool_retriever.get_tool_retriever", lambda: _Retriever())
    svc = LLMSharedMixin.__new__(LLMSharedMixin)
    names = ["memory_search", "development_control", "kanban_ticket", "terminal_overview", "planning",
             "google_workspace", "tensology_workspace", "read_conversation", "open_thread", "clipboard_action"]
    svc._tools_dict = {name: _Tool(name) for name in names}
    svc._tools = list(svc._tools_dict.values())
    svc._model_name = "deepseek-v4.1-flash"
    return svc


def test_chat_tool_filter_surfaces_dev_tools_even_when_retrieval_misses_them(monkeypatch):
    svc = _filtering_service(monkeypatch, ["memory_search"])
    got = {tool.name for tool in svc._get_filtered_tools("What's happening in development on Merrypak?")}
    assert {"development_control", "kanban_ticket", "terminal_overview", "planning"} <= got


def test_mail_intent_groups_kept_the_old_keyword_behaviour(monkeypatch):
    svc = _filtering_service(monkeypatch, ["memory_search"])
    assert {"tensology_workspace", "google_workspace"} <= {t.name for t in svc._get_filtered_tools("check my inbox")}
    gmail = {t.name for t in svc._get_filtered_tools("send an email through gmail to Bob")}
    assert "google_workspace" in gmail and "tensology_workspace" not in gmail


def test_planning_tool_reads_the_plan_of_the_chats_project(db, monkeypatch):
    from distr.core.agent.tools.system.planning_tool import PlanningTool

    seen = {}

    def fake_snapshot(project_id, **_):
        seen["project_id"] = project_id
        return {"items": 3}

    monkeypatch.setattr("distr.core.planning.context.planning_snapshot", fake_snapshot)
    monkeypatch.setattr("distr.core.planning.context.planning_prompt_text", lambda snap, max_chars=1800: "3 plan items: delivery, R140, invoices")
    out = json.loads(PlanningTool()._run(chat_id=db["dev_chat"]))
    assert out["ok"] is True
    assert out["project"] == "Merrypak"
    assert seen["project_id"] == db["merrypak"]
    assert "R140" in out["plan"]
    missing = json.loads(PlanningTool()._run(chat_id=db["main"]))
    assert missing["ok"] is False


def test_every_chat_tool_call_is_logged_including_guard_skips(monkeypatch):
    from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService

    logged = []
    monkeypatch.setattr(
        "distr.core.agent.tool_audit.record_tool_execution",
        lambda chat_id, name, result, status="completed", **kw: logged.append((name, status, kw.get("metadata", {}))),
    )
    svc = OpenAICompatibleLLMService.__new__(OpenAICompatibleLLMService)
    svc._messages = [{"role": "user", "content": "do the thing"}]
    svc.chat_manager = None
    svc.event_queue = None
    svc._tools_dict = {}
    svc._is_telegram_request = False
    calls = [{"id": "c1", "function": {"name": "made_up_tool", "arguments": "{}"}}]
    asyncio.run(svc._execute_tool_calls_with_chaining(calls))
    assert logged and logged[0][0] == "made_up_tool"
    assert logged[0][2].get("skipped_by") == "unknown_tool"


def test_mcp_tools_get_real_descriptions_for_retrieval():
    tool = _Tool("mcp__headroom__compress", "")
    tool._server_name = "headroom"
    tool._mcp_tool_name = "compress"
    text = tr.describe_tool(tool, {})
    assert "compress" in text.lower() and "Headroom" in text
    composio = _Tool("mcp__composio__GMAIL_SEND", "Send a Gmail message")
    composio._server_name = "composio-connect"
    assert "Composio" in tr.describe_tool(composio, {}) and "Gmail" in tr.describe_tool(composio, {})
    laya = _Tool("mcp__laya__submit", "")
    assert "Laya" in tr.describe_tool(laya, {})


def test_relevance_threshold_adds_strong_matches_beyond_k_and_drops_noise():
    names = [f"t{i}" for i in range(30)]
    scores = [0.0] * 30
    scores[0], scores[1], scores[2], scores[3] = 0.9, 0.8, 0.7, 0.6
    scores[20] = 0.5  # strong, but ranked 5th → within K anyway
    picked = tr.select_by_relevance(names, scores, k=3, backend="sbert")
    assert picked[:3] == ["t0", "t1", "t2"]
    assert "t3" in picked and "t20" in picked  # strong matches beyond K are kept
    sparse = tr.select_by_relevance(names, [0.0] * 29 + [0.02], k=15, backend="tfidf")
    assert sparse == ["t29"]  # zero-score padding is gone


# --------------------------------------------------------------------------- area 4

CLIPBOARD_11_NOV = "No, read from the clipboard."


def test_11_nov_workflow_run_from_a_clipboard_request_needs_confirmation():
    owner = SimpleNamespace()
    reason = confirmation_block_reason(owner, "run_workflow", {"name": "Run Tensology bank statement upload"}, CLIPBOARD_11_NOV)
    assert reason.startswith("Needs confirmation")
    assert confirmation_block_reason(owner, "play_action", {}, CLIPBOARD_11_NOV)
    # Paul says yes → that tool is released once.
    assert confirmation_block_reason(owner, "play_action", {}, "Yes, go ahead") == ""
    # Explicit requests are never blocked.
    assert confirmation_block_reason(SimpleNamespace(), "run_workflow", {}, "Run the bank statement upload workflow") == ""
    assert confirmation_block_reason(SimpleNamespace(), "google_workspace", {"action": "send_email"}, "send Bob the invoice") == ""
    assert confirmation_block_reason(SimpleNamespace(), "google_workspace", {"action": "send_email"}, "read my inbox")
    assert confirmation_block_reason(SimpleNamespace(), "google_workspace", {"action": "list_messages"}, "read my inbox") == ""


def test_11_nov_gate_is_enforced_by_the_shared_tool_policy():
    from distr.core.agent.services.llm.tool_execution_policy import tool_execution_block_reason

    owner = SimpleNamespace(chat_manager=None)
    assert tool_execution_block_reason(owner, "run_workflow", {"workflow_id": 4}, CLIPBOARD_11_NOV).startswith("Needs confirmation")
    assert tool_execution_block_reason(owner, "clipboard_action", {"action": "get"}, CLIPBOARD_11_NOV) == ""


def test_11_nov_late_workflow_reports_are_not_filed_under_a_stale_turn(db, monkeypatch):
    from distr.core.workflow import chat_trace

    started = []
    monkeypatch.setattr("distr.core.chat_turns.start_tool", lambda *a, **k: started.append((a, k)) or (None, False, None))
    monkeypatch.setattr("distr.core.chat_turns.find_running_tool_event", lambda *a, **k: None)
    assert chat_trace._active_turn_is_stale(db["main"]) is True
    chat_trace.record_chat_workflow_event(db["main"], "run_completed", status="completed",
                                          summary="Done. Run Tensology bank statement upload finished.")
    assert started == []  # not attached to "No, read from the clipboard."


def test_fresh_turns_still_receive_their_workflow_events(db, monkeypatch):
    from distr.core.db import Chat, get_session
    from distr.core.workflow import chat_trace

    with get_session() as session:
        main = session.get(Chat, db["main"])
        fresh = Chat(parent_id=main.id, input="run the bank statement workflow", response="")
        session.add(fresh)
        session.flush()
        main.params = json.dumps({"active_turn_chat_row_id": fresh.id})
        session.commit()
    assert chat_trace._active_turn_is_stale(db["main"]) is False


HISTORY_15_NOV = ["Can you see what I said to Uncle Roger?", "Well, that's what I'm asking you to do, is Uncle Roger."]
READ_15_NOV = "I just want you to read it."


def test_15_nov_read_it_is_detected_as_clipboard_read_by_the_fast_path():
    from distr.core.agent.services.llm.fast_action_detector import ActionType, detect_fast_action

    action = detect_fast_action(READ_15_NOV)
    assert action is not None and action.action_type == ActionType.CLIPBOARD_READ  # the original bug


def test_15_nov_read_it_after_talking_about_uncle_roger_goes_to_the_conversation(db):
    assert should_defer_clipboard_read(READ_15_NOV, HISTORY_15_NOV) == "the conversation with Uncle Roger"
    assert should_defer_clipboard_read(READ_15_NOV, []) == ""
    assert should_defer_clipboard_read("read the clipboard", HISTORY_15_NOV) == ""
    assert should_defer_clipboard_read("read it from my clipboard", HISTORY_15_NOV) == ""


def test_15_nov_chat_service_skips_the_clipboard_and_hints_the_thread(db):
    from distr.core.agent.services.llm.core_mixin import LLMSharedMixin

    svc = LLMSharedMixin.__new__(LLMSharedMixin)
    svc._messages = [{"role": "user", "content": text} for text in HISTORY_15_NOV] + [{"role": "user", "content": READ_15_NOV}]
    svc._processed_fast_actions = set()
    assert svc._check_fast_actions() is None
    assert "Uncle Roger" in svc._messages[-1]["content"]
    assert "read_conversation" in svc._messages[-1]["content"]
    assert "read_conversation" in tr.intent_tool_names(svc._messages[-1]["content"])


def test_15_nov_read_conversation_reads_uncle_rogers_chat(db):
    from distr.core.agent.tools.chat.read_conversation import ReadConversationTool

    out = json.loads(ReadConversationTool()._run(agent_name="Uncle Roger"))
    assert out["ok"] is True
    assert out["exchanges"][-1]["user"].startswith("Roger, the AI Bandits course")


def _harness_events(count_reads=82):
    events = []
    for index in range(count_reads):
        tool = ("read", "grep", "ls")[index % 3]
        events.append({"type": "tool_execution_end", "toolName": tool, "toolCallId": f"c{index}",
                       "args": {"path": f"shop/file_{index}.py"}, "result": {"content": []}})
    return events


def test_18_nov_development_read_loop_is_stopped_with_a_plain_reason(monkeypatch):
    from distr.core.turn_runtime import ModelRoute, TurnRequest, TurnScope
    from distr.core.turn_runtime.cli_harness import CliHarnessTurnRuntime

    relayed = []

    async def fake_dispatch(context):
        for raw in _harness_events():
            context.on_event(raw)
            relayed.append(raw)
        return SimpleNamespace(execution_session_id=5, evidence={},
                               result=SimpleNamespace(success=True, output="done", error="", waits_for_human=False))

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", fake_dispatch)
    monkeypatch.setattr("distr.core.kanban.project_execution.cancel_execution_session", lambda *_: None)
    request = TurnRequest(
        instruction="Fix the Merrypak DELIVERY / R140 task completely.",
        project=SimpleNamespace(id=2, folder_location="/tmp/merrypak"),
        route=ModelRoute(provider="openrouter", model="deepseek/deepseek-v4.1-flash", backend_id="pi"),
        scope=TurnScope(chat_id=1509, project_id=2, turn_id=1543),
    )
    events = []
    result = asyncio.run(CliHarnessTurnRuntime().execute(request, on_event=events.append))
    assert result.success is False
    assert "going in circles" in result.error
    assert len(relayed) < 82  # stopped before Paul had to
    assert any((e.details or {}).get("read_loop_guard") == "nudge" for e in events)


def test_read_loop_guard_resets_after_a_change():
    guard = ReadLoopGuard.for_development()
    for index in range(50):
        assert guard.observe("read", {"path": f"f{index}"}, read=True) != "stop"
    guard.observe("edit", {"path": "x"}, read=False)
    assert guard.consecutive_reads == 0
    assert is_harness_read("run_command", {"command": "rg delivery shop/"}) is True
    assert is_harness_read("run_command", {"command": "python manage.py migrate"}) is False


def test_chat_read_loop_withholds_tools_and_asks_for_an_answer():
    from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService

    svc = OpenAICompatibleLLMService.__new__(OpenAICompatibleLLMService)
    svc._messages = []
    guard = ReadLoopGuard.for_chat()
    tools = ["tool-schema"]
    rounds = [[{"id": f"r{i}", "function": {"name": "memory_search", "arguments": json.dumps({"query": f"q{i}"})}}]
              for i in range(12)]
    results = [OpenAICompatibleLLMService._apply_read_loop_guard(svc, guard, calls, tools) for calls in rounds]
    assert results[7] == tools and "[LOOP GUARD]" in svc._messages[0]["content"]
    assert results[11] is None and "Tools are now off" in svc._messages[-1]["content"]


def test_round_cap_is_told_to_the_user():
    from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService

    saved = []
    svc = OpenAICompatibleLLMService.__new__(OpenAICompatibleLLMService)
    svc._save_assistant_message = saved.append
    svc.event_queue = None
    OpenAICompatibleLLMService._notify_round_cap(svc, 10, [{"function": {"name": "web_search"}}])
    assert saved and "limit of 10 tool rounds" in saved[0] and "web_search" in saved[0]
    assert "continue" in round_cap_notice(10)


@pytest.mark.parametrize("text,expected", [
    ("Check my inbox, then draft a reply to Bob and add a ticket for the invoice.", True),
    ("1. Read the Merrypak plan\n2. Create tickets for delivery\n3. Start the run", True),
    ("What's the time?", False),
    ("Read it.", False),
])
def test_multi_step_requests_get_a_plan_step(text, expected):
    assert is_multi_step_request(text) is expected
    content, planned = with_plan_step(text, text)
    assert planned is expected
    assert ("[PLAN FIRST]" in content) is expected
