"""Context: chat-scoped project, grounded state answers, 6,000-char sections.

Replays the 12 Nov 22:50 SAST failure: in chat 1 Paul asked "Can you tell me
what project I'm currently working on?" and the agent answered "Tensology …
the active Development thread is 'Daily work declaration and approvals'"
(a scheduled automation thread) without calling any tool.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from distr.core.agent import state_grounding as sg
from distr.core.developer_context import (
    DeveloperContextAssembler,
    DeveloperProjectContext,
    DeveloperRuntimeContext,
    DeveloperTicketContext,
    DeveloperWorkContext,
    DeveloperWorkflowContext,
)

QUESTION_12_NOV = "Can you tell me what project I'm currently working on?"


@pytest.fixture
def db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from distr.core.db import Base, Chat
    from distr.core.db.projects import Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        jackcat = Project(name="Jackcat", in_use=True)
        tensology = Project(name="Tensology", in_use=False)
        snuza = Project(name="Snuza", in_use=False)
        session.add_all([jackcat, tensology, snuza])
        session.flush()
        main_chat = Chat(title="why is property such a good investment", input="", response="")
        automation = Chat(title="Daily work declaration and approvals", input="", response="",
                          project_id=tensology.id)
        snuza_chat = Chat(title="Snuza dashboard", input="", response="", project_id=snuza.id)
        session.add_all([main_chat, automation, snuza_chat])
        session.flush()
        thread = Chat(title="Investigate device 2207", input="", response="",
                      params=json.dumps({"orchestrator_scope": {"project_id": snuza.id}}))
        session.add(thread)
        session.commit()
        ids = {
            "jackcat": jackcat.id, "tensology": tensology.id, "snuza": snuza.id,
            "main_chat": main_chat.id, "automation": automation.id,
            "snuza_chat": snuza_chat.id, "thread": thread.id,
        }
    monkeypatch.setattr("distr.core.db.get_session", lambda: factory())
    return ids


def _build(chat_id):
    return DeveloperContextAssembler().build(chat_id=chat_id)


def test_12_nov_case_global_flag_is_labelled_not_presented_as_current_work(db):
    context = _build(db["main_chat"])
    assert context.project_source == "global_default"
    assert context.active_project.name == "Jackcat"
    assert context.active_thread["chat_id"] == db["main_chat"]

    text = context.to_sectioned_prompt_text(max_chars=6000)
    assert "project_source: global_default" in text
    assert "not tied to this chat" in text
    # The automation thread must not leak in as "the active Development thread".
    assert "Daily work declaration" not in text


def test_project_comes_from_the_chat_row_when_it_has_one(db):
    context = _build(db["snuza_chat"])
    assert context.project_source == "chat"
    assert context.active_project.name == "Snuza"
    assert "[source: chat]" in context.to_prompt_text()


def test_project_comes_from_development_thread_scope(db):
    context = _build(db["thread"])
    assert context.project_source == "thread"
    assert context.active_project.name == "Snuza"


@pytest.mark.parametrize(
    "text",
    [
        QUESTION_12_NOV,
        "what am I working on",
        "What's going on?",
        "status?",
        "What's running right now",
        "Give me a status update",
        "which thread is active",
        "where are we at with the Snuza work",
    ],
)
def test_state_questions_are_detected(text):
    assert sg.is_state_question(text)


@pytest.mark.parametrize(
    "text",
    ["Tell me a story.", "What is a bandit?", "No, read from the clipboard.", "Hello, my name is Paul."],
)
def test_non_state_questions_are_not_grounded(text):
    assert not sg.is_state_question(text)


class _Tool:
    def __init__(self, name, output):
        self.name = name
        self.output = output
        self.calls = []

    def _run(self, **kwargs):
        self.calls.append(kwargs)
        return self.output


def _tools():
    return {
        "development_control": _Tool("development_control",
                                     '{"threads": [{"thread_id": 1509, "title": "Confirm delivery code and R140 charge"}]}'),
        "kanban_ticket": _Tool("kanban_ticket", "Active runs: none"),
        "terminal_overview": _Tool("terminal_overview", '{"terminals": []}'),
    }


def test_state_question_calls_overview_ticket_and_terminal_tools_and_logs_them():
    tools = _tools()
    started, finished = [], []
    fixed = datetime(2026, 11, 12, 20, 50, 5, tzinfo=timezone.utc)

    result = sg.ground_state(
        tools, chat_id=1, project_id=None,
        record=lambda chat_id, name, output, status, **kw: finished.append((chat_id, name, status, kw)),
        record_start=lambda chat_id, name, **kw: started.append((chat_id, name, kw)),
        now=lambda: fixed,
    )

    assert result.tool_names == ["development_control", "development_control", "kanban_ticket", "terminal_overview"]
    assert [c["action"] for c in tools["development_control"].calls] == ["list_threads", "list_workflows"]
    assert tools["kanban_ticket"].calls == [{"action": "workflow_overview"}]
    assert tools["terminal_overview"].calls == [{"action": "list"}]
    assert len(started) == 4 and len(finished) == 4
    assert all(item[3]["metadata"]["source"] == "state_grounding" for item in finished)

    block = result.to_prompt_block(QUESTION_12_NOV)
    assert "LIVE DEVELOPMENT STATE — fetched by Decisions at" in block
    assert "Confirm delivery code and R140 charge" in block
    assert "as of" in block and "only a global default" in block
    assert block.rstrip().endswith(QUESTION_12_NOV)


def test_project_scope_is_passed_to_overview():
    tools = _tools()
    sg.ground_state(tools, chat_id=5, project_id=8, record=lambda *a, **k: None, record_start=lambda *a, **k: None)
    assert all(call["project_id"] == 8 for call in tools["development_control"].calls)


def test_failing_probe_is_reported_not_fatal():
    tools = _tools()

    class Boom:
        def _run(self, **_):
            raise RuntimeError("db locked")

    tools["terminal_overview"] = Boom()
    result = sg.ground_state(tools, record=lambda *a, **k: None, record_start=lambda *a, **k: None)
    assert result.calls[-1]["ok"] is False
    assert "db locked" in result.calls[-1]["result"]


def test_chat_service_grounds_state_questions_before_the_model_runs(monkeypatch):
    from distr.core.agent.services.llm.core_mixin import LLMSharedMixin

    monkeypatch.setattr("distr.core.agent.tool_audit.record_tool_execution", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.agent.tool_audit.record_tool_start", lambda *a, **k: None)
    svc = LLMSharedMixin.__new__(LLMSharedMixin)
    svc._tools_dict = _tools()
    svc.chat_manager = type("CM", (), {"get_current_chat": lambda self: None})()

    grounded = svc._ground_state_question(QUESTION_12_NOV, QUESTION_12_NOV)
    assert "LIVE DEVELOPMENT STATE" in grounded
    assert svc._tools_dict["kanban_ticket"].calls
    untouched = svc._ground_state_question("Tell me a story.", "Tell me a story.")
    assert untouched == "Tell me a story."


def _big_context():
    return DeveloperWorkContext(
        runtime=DeveloperRuntimeContext(cwd="/w", current_chat_id=1509, captured_at="2026-11-19T11:00:00+00:00"),
        active_project=DeveloperProjectContext(id=2, name="Merrypak", description="Ecommerce"),
        project_source="chat",
        planning_snapshot={},
        plan_sections_text="PLAN " + ("interfaces data documents " * 300),
        active_tickets=[
            DeveloperTicketContext(id=280 + i, title=f"Ticket number {i}", lane="Current") for i in range(12)
        ],
        active_workflows=[
            DeveloperWorkflowContext(id=900 + i, name=f"Run {i} delivery fix", status="running") for i in range(6)
        ],
    )


def test_sectioned_budget_keeps_tickets_and_runs_when_plan_is_huge():
    context = _big_context()
    old = context.to_prompt_text(max_chars=2200)
    new = context.to_sectioned_prompt_text(max_chars=6000)

    assert len(new) <= 6100
    for heading in ("## Project", "## Thread", "## Tickets", "## Runs", "## Planning"):
        assert heading in new
    assert "#280 Ticket number 0" in new
    assert "#291 Ticket number 11" in new  # more than the old 8-ticket cap
    assert "Run 5 delivery fix" in new
    assert "captured_at: 2026-11-19T11:00:00+00:00" in new
    # The plan is trimmed inside its own section instead of blowing the budget
    # (the old renderer appended plan sections unbounded: 8,000+ chars here).
    assert "…(trimmed)" in new.split("## Planning", 1)[1]
    assert len(old) > 6000


def test_chat_prompt_uses_the_6000_char_sectioned_context():
    import inspect
    from distr.core.agent.services.llm import core_mixin

    source = inspect.getsource(core_mixin.LLMSharedMixin._build_system_prompt_template)
    assert "to_sectioned_prompt_text(max_chars=6000)" in source
