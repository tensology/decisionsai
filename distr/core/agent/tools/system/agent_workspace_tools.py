"""Workspace tools for chat agents: Calendar blocks, Snippets, Development overview, message_agent.

Every call is checked against the calling agent's capability set
(distr.core.agent_capabilities). Deletes always need Paul's in-chat confirmation.
The calling agent is the one loaded in the live runtime (else the primary).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.agent_capabilities import confirm_destructive, denied

logger = logging.getLogger(__name__)

_ACTION_CAPABILITY = {"list": "read", "get": "read", "create": "create", "update": "update", "delete": "delete"}


def _caller(agent_id: Optional[int]) -> Optional[int]:
    if agent_id is not None:
        return agent_id
    from distr.core.agent_capabilities import UNKNOWN_AGENT
    from distr.core.agent_groups import current_agent_id

    try:
        return current_agent_id(strict=True)
    except Exception:
        return UNKNOWN_AGENT  # every capability check on it refuses


def _guard(action: str, agent_id: Optional[int]) -> str:
    capability = _ACTION_CAPABILITY.get(action)
    if capability is None:
        return f"Unknown action {action!r}."
    return denied(agent_id, capability)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "")).replace(microsecond=0)


# ---------------------------------------------------------------------------
# Calendar (schedule blocks shown on /calendar)
# ---------------------------------------------------------------------------

def calendar_op(action: str, *, agent_id: Optional[int] = None, start: str = "", end: str = "",
                block_id: Optional[int] = None, title: Optional[str] = None, project_id: Optional[int] = None,
                confirm=None) -> str:
    from distr.core.db import get_session
    from distr.core.db.schedule_blocks import ScheduleBlock
    from distr.core.services import schedule_blocks as svc

    agent_id = _caller(agent_id)
    refusal = _guard(action, agent_id)
    if refusal:
        return refusal
    with get_session() as session:
        if action == "list":
            start_dt = _dt(start) if start else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = _dt(end) if end else start_dt + timedelta(days=7)
            blocks = svc.blocks_for_range(session, start_dt, end_dt)
            rows = [svc.serialize_block(session, b, extend_running_end=True) for b in blocks]
            return json.dumps({"blocks": [{k: r.get(k) for k in ("id", "title", "start_at", "end_at", "project_id")}
                                          for r in rows]}, default=str)
        if action == "create":
            if not start or not end:
                return "Give start and end (ISO datetime) for the new block."
            block = svc.create_block(session, title=title or "Untitled block", start_at=start, end_at=end,
                                     board_id=None, board_provider="local", external_board_id=None,
                                     ticket_id=None, external_ticket_key=None, project_id=project_id)
            return f"Created calendar block {block.id}: {block.title} {block.start_at}–{block.end_at}."
        block = session.query(ScheduleBlock).filter(ScheduleBlock.id == int(block_id or 0)).first()
        if not block:
            return "Calendar block not found."
        if action == "get":
            return json.dumps(svc.serialize_block(session, block), default=str)
        if action == "update":
            changes = {k: v for k, v in {"title": title, "start_at": start or None, "end_at": end or None,
                                         "project_id": project_id}.items() if v is not None}
            svc.update_block(session, block, changes)
            return f"Updated calendar block {block.id}."
        if action == "delete":
            if not (confirm or (lambda _s: False))(f"Delete calendar block {block.id} “{block.title}”?"):
                return "Not deleted: Paul did not approve it (or did not answer within 15 minutes). Do not retry."
            session.delete(block)
            session.commit()
            return f"Deleted calendar block {block.id}."
    return f"Unknown action {action!r}."


# ---------------------------------------------------------------------------
# Snippets
# ---------------------------------------------------------------------------

def snippets_op(action: str, *, agent_id: Optional[int] = None, snippet_id: Optional[int] = None,
                title: Optional[str] = None, text: Optional[str] = None, confirm=None) -> str:
    from distr.core.db import Snippet, get_session

    agent_id = _caller(agent_id)
    refusal = _guard(action, agent_id)
    if refusal:
        return refusal
    with get_session() as session:
        if action == "list":
            rows = session.query(Snippet).order_by(Snippet.modified_date.desc()).limit(100).all()
            return json.dumps([{"id": s.id, "title": s.title or "", "preview": (s.description or "")[:120]} for s in rows])
        if action == "create":
            if not (text or "").strip():
                return "Give the snippet text."
            from distr.core.settings import load_settings_from_db
            from distr.gui.web.routes.settings.snippets import (
                _next_default_remote_hotkey, _notify_snippet_hotkeys_changed, _snippet_title,
                _validate_snippet_remote_hotkey)

            hotkey = _validate_snippet_remote_hotkey(_next_default_remote_hotkey(session, Snippet),
                                                     settings=load_settings_from_db(),
                                                     existing_snippets=session.query(Snippet).all())
            snippet = Snippet(title=title or _snippet_title(text), description=text,
                              additional_trigger_words="[]", remote_hotkey=hotkey)
            session.add(snippet)
            session.commit()
            _notify_snippet_hotkeys_changed()
            return f"Created snippet {snippet.id}: {snippet.title}."
        snippet = session.query(Snippet).filter(Snippet.id == int(snippet_id or 0)).first()
        if not snippet:
            return "Snippet not found."
        if action == "get":
            return json.dumps({"id": snippet.id, "title": snippet.title or "", "text": snippet.description or ""})
        if action == "update":
            if title is not None:
                snippet.title = title
            if text is not None:
                snippet.description = text
            session.commit()
            return f"Updated snippet {snippet.id}."
        if action == "delete":
            if not (confirm or (lambda _s: False))(f"Delete snippet {snippet.id} “{snippet.title}”?"):
                return "Not deleted: Paul did not approve it (or did not answer within 15 minutes). Do not retry."
            session.delete(snippet)
            session.commit()
            return f"Deleted snippet {snippet.id}."
    return f"Unknown action {action!r}."


# ---------------------------------------------------------------------------
# Development (read only)
# ---------------------------------------------------------------------------

def development_overview(*, agent_id: Optional[int] = None, project_id: Optional[int] = None, limit: int = 20) -> str:
    from distr.core.db import Chat, get_session
    from distr.core.db.projects import Project
    from distr.core.db.workflow import DevelopmentWorkItem

    refusal = denied(_caller(agent_id), "read")
    if refusal:
        return refusal
    limit = max(1, min(int(limit or 20), 50))
    with get_session() as session:
        projects = (session.query(Project).filter(Project.deleted_at.is_(None))
                    .order_by(Project.position, Project.id).all())
        items_q = session.query(DevelopmentWorkItem)
        if project_id:
            items_q = items_q.filter(DevelopmentWorkItem.project_id == int(project_id))
        items = items_q.order_by(DevelopmentWorkItem.modified_at.desc()).limit(limit).all()
        titles = {c.id: c.title for c in session.query(Chat.id, Chat.title)
                  .filter(Chat.id.in_([i.chat_id for i in items])).all()} if items else {}
        names = {p.id: p.name for p in projects}
        return json.dumps({
            "projects": [{"id": p.id, "name": p.name, "in_use": bool(p.in_use), "board": p.board_name or ""}
                         for p in projects],
            "threads": [{"thread_id": i.chat_id, "title": titles.get(i.chat_id) or i.ticket_title or "",
                         "project": names.get(i.project_id, ""), "ticket": i.ticket_key or "",
                         "lane": i.ticket_lane or "", "timer_running": not bool(i.time_paused),
                         "time_seconds": int(i.time_accumulated_seconds or 0),
                         "updated": i.modified_at.isoformat() if i.modified_at else None} for i in items],
        }, default=str)


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------

class _ConfirmingTool(BaseTool):
    event_queue: Optional[Any] = Field(default=None, exclude=True)
    command_queue: Optional[Any] = Field(default=None, exclude=True)
    confirmation_results_dict: Optional[Any] = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, command_queue=None, confirmation_results_dict=None, **kwargs):
        super().__init__(**kwargs)
        self.event_queue = event_queue
        self.command_queue = command_queue
        self.confirmation_results_dict = confirmation_results_dict

    def _confirm(self, summary: str) -> bool:
        # In-chat Approve/Deny card; fails closed (no Qt dialog).
        return confirm_destructive(summary, tool=getattr(self, "name", "chat_agent"), action="delete")


class CalendarInput(BaseModel):
    action: Literal["list", "get", "create", "update", "delete"] = Field(description="What to do.")
    start: str = Field(default="", description="ISO start (list range start, or block start).")
    end: str = Field(default="", description="ISO end (list range end, or block end).")
    block_id: Optional[int] = Field(default=None, description="Block id for get/update/delete.")
    title: Optional[str] = Field(default=None, description="Block title for create/update.")
    project_id: Optional[int] = Field(default=None, description="Optional project to link.")


class CalendarBlocksTool(_ConfirmingTool):
    name: str = "calendar_blocks"
    description: str = ("Read and manage Paul's Decisions Calendar (the /calendar time blocks): list a date range, "
                        "get, create, update or delete a block. Deletes ask Paul to confirm.")
    args_schema: type[BaseModel] = CalendarInput

    def _run(self, action: str = "list", start: str = "", end: str = "", block_id: Optional[int] = None,
             title: Optional[str] = None, project_id: Optional[int] = None, **_: Any) -> str:
        try:
            return calendar_op(action, start=start, end=end, block_id=block_id, title=title,
                               project_id=project_id, confirm=self._confirm)
        except ValueError as exc:
            return f"Calendar error: {exc}"


class SnippetsInput(BaseModel):
    action: Literal["list", "get", "create", "update", "delete"] = Field(description="What to do.")
    snippet_id: Optional[int] = Field(default=None, description="Snippet id for get/update/delete.")
    title: Optional[str] = Field(default=None, description="Title (trigger word).")
    text: Optional[str] = Field(default=None, description="Snippet text.")


class SnippetsTool(_ConfirmingTool):
    name: str = "snippets"
    description: str = ("Read and manage Paul's text Snippets: list, get, create, update or delete. "
                        "Deletes ask Paul to confirm.")
    args_schema: type[BaseModel] = SnippetsInput

    def _run(self, action: str = "list", snippet_id: Optional[int] = None, title: Optional[str] = None,
             text: Optional[str] = None, **_: Any) -> str:
        try:
            return snippets_op(action, snippet_id=snippet_id, title=title, text=text, confirm=self._confirm)
        except Exception as exc:
            return f"Snippets error: {str(exc)[:200]}"


class DevelopmentOverviewInput(BaseModel):
    project_id: Optional[int] = Field(default=None, description="Limit threads to one project.")
    limit: int = Field(default=20, ge=1, le=50, description="How many recent threads.")


class DevelopmentOverviewTool(BaseTool):
    name: str = "development_overview"
    description: str = ("Read-only view of the Development environment: projects, recent Development threads "
                        "and their work items (ticket, lane, timer, time spent).")
    args_schema: type[BaseModel] = DevelopmentOverviewInput

    def _run(self, project_id: Optional[int] = None, limit: int = 20, **_: Any) -> str:
        return development_overview(project_id=project_id, limit=limit)


class MessageAgentInput(BaseModel):
    agent: str = Field(description="The other agent's name (or id).")
    message: str = Field(description="What to say or ask.")


class MessageAgentTool(BaseTool):
    name: str = "message_agent"
    description: str = ("Send a message to another Decisions agent (by name) and get its reply. The exchange is "
                        "saved in your shared thread with that agent. Use when Paul asks you to ask, tell, check "
                        "with or delegate to another agent.")
    args_schema: type[BaseModel] = MessageAgentInput

    def _run(self, agent: str = "", message: str = "", **_: Any) -> str:
        from distr.core.agent_groups import GroupError, current_agent_id, message_agent

        sender = current_agent_id()
        if not sender:
            return "No current agent to send from."
        try:
            result = message_agent(sender, agent, message)
        except GroupError as exc:
            return str(exc)
        if result["error"]:
            return f"{result['agent_name']} could not reply: {result['reply']}"
        return f"{result['agent_name']} replied: {result['reply']}"


# ---------------------------------------------------------------------------
# Browser for Chat agents: the Development structured browser on the signed-in
# ~/.decisions/chrome-profile, headless unless asked. Never the Decisions app,
# never password fields; submits/purchase-like clicks need Paul's confirmation.
# ---------------------------------------------------------------------------

BROWSER_READ_ACTIONS = {"open", "navigate", "extract", "screenshot", "close"}


def _plain_browser_summary(agent_name: str, action: str, kwargs: dict, page: Optional[str] = None) -> str:
    element = kwargs.get("selector") or "the page"
    where = page or kwargs.get("url") or "the current page"
    if action == "type":
        text = (kwargs.get("text") or "")[:200]
        tail = " and press Enter (submit)" if kwargs.get("submit") else ""
        return f"{agent_name} wants to type “{text}” into {element} on {where}{tail}."
    if action == "click":
        return f"{agent_name} wants to click {element} on {where}."
    return f"{agent_name} wants to {action} {element} on {where}."


def _agent_name(agent_id: Optional[int]) -> str:
    try:
        from distr.core.db import ChatAgent, get_session

        with get_session() as session:
            agent = session.get(ChatAgent, int(agent_id)) if agent_id and agent_id > 0 else None
            return agent.name if agent else "The agent"
    except Exception:
        return "The agent"


def chat_browser(action: str, *, agent_id: Optional[int] = None, approve=None, transport=None,
                 chat_id: Optional[int] = None, **kwargs) -> dict:
    """Structured browser for Chat agents.

    approve(tool, action, args, summary, details) -> Decision/bool; defaults to the
    in-chat Approve/Deny card. Any error or missing answer means no.
    """
    from distr.core.agent_capabilities import browser_policy
    from distr.core.chat_approvals import request_in_chat, sensitive_ask_enabled
    from distr.core.workflow import dev_browser_tools as dbt

    agent_id = _caller(agent_id)
    mutating = action not in BROWSER_READ_ACTIONS or bool(kwargs.get("submit"))
    policy = browser_policy(agent_id, mutating=mutating)
    if policy == "deny":
        return {"ok": False, "summary": denied(agent_id, "read") or "Not permitted: this agent cannot use the browser."}

    def ask(tool, act, args, summary, details):
        try:
            if approve is not None:
                result = approve(tool, act, args, summary, details)
            else:
                result = request_in_chat(tool, act, args, summary, details, chat_id=chat_id)
        except Exception:
            logger.warning("browser approval failed; refusing", exc_info=True)
            return False, f"Not done: the approval could not be completed ({summary}). Do not retry."
        ok = bool(getattr(result, "approved", result) is True)
        message = getattr(result, "message", "") or ("" if ok else f"Paul did not approve: {summary}. Do not retry.")
        return ok, message

    name = _agent_name(agent_id)
    pre_approved = False
    if policy == "confirm":
        summary = _plain_browser_summary(name, action, kwargs)
        args = {k: kwargs.get(k) for k in ("url", "selector", "text", "submit") if kwargs.get(k) is not None}
        ok, message = ask("web_browser", action, {"agent_id": agent_id, **args}, summary,
                          {"agent": name, "action": action, **args})
        if not ok:
            return {"ok": False, "summary": message}
        pre_approved = True
    ask_sensitive = sensitive_ask_enabled()

    def gate(tool, act, args, approval_id, summary, details=None, **_):
        if pre_approved or not ask_sensitive:
            return None
        page = (details or {}).get("page") or (args or {}).get("page")
        element = (details or {}).get("element") or (details or {}).get("field") or {}
        label = (element.get("text") or (args or {}).get("selector") or "an element")[:80]
        if act == "submit":
            plain = f"{name} wants to type “{(args or {}).get('text') or ''}” into {label} and submit it on {page}."
        else:
            plain = f"{name} wants to click “{label}” on {page}."
        ok, message = ask(tool, act, {**(args or {}), "agent_id": agent_id}, plain, {"agent": name, **(details or {})})
        if ok:
            return None
        return {"ok": False, "tool": tool, "summary": message, "artifacts": [], "needs_approval": False}

    settings = dict(dbt._settings())
    settings["dev_browser_enabled"] = True  # Chat access only; Paul's Development setting is unchanged
    from pathlib import Path

    previous = dbt.transport
    if transport is not None:
        dbt.transport = transport
    try:
        return dbt.browser(action, settings=settings, gate=gate, project_folder=str(Path.home()), **kwargs)
    except Exception as exc:
        logger.warning("chat browser failed", exc_info=True)
        return {"ok": False, "summary": f"Browser error: {exc}"}
    finally:
        dbt.transport = previous


class ChatBrowserInput(BaseModel):
    action: Literal["open", "navigate", "click", "type", "extract", "screenshot", "close"]
    url: Optional[str] = Field(default=None, description="For open/navigate.")
    selector: Optional[str] = Field(default=None, description="Playwright selector for click/type/extract.")
    text: Optional[str] = Field(default=None, description="Text to type.")
    submit: bool = Field(default=False, description="Press Enter after typing (needs approval).")
    mode: Literal["text", "links", "html"] = Field(default="text", description="extract mode")
    headed: bool = Field(default=False, description="Show the window. Only when Paul asks to see it.")


class ChatBrowserTool(_ConfirmingTool):
    name: str = "web_browser"
    description: str = ("Use a real Chrome (the signed-in Decisions profile, so logins persist): open/navigate a URL, "
                        "extract text/links, screenshot, click, type, close. Headless unless Paul asks to see it "
                        "(headed=true). Never types passwords; submits and purchase-like clicks ask Paul first.")
    args_schema: type[BaseModel] = ChatBrowserInput

    def _run(self, action: str = "open", url: Optional[str] = None, selector: Optional[str] = None,
             text: Optional[str] = None, submit: bool = False, mode: str = "text", headed: bool = False,
             **_: Any) -> str:
        result = chat_browser(action, url=url, selector=selector, text=text,
                              submit=submit, mode=mode, headed=headed)
        body = {k: v for k, v in result.items() if k in ("summary", "url", "title", "text", "links", "artifacts")}
        return json.dumps(body, default=str)[:12000]

    def _confirm_action(self, summary: str) -> bool:
        from distr.core.agent_capabilities import request_confirmation

        return request_confirmation("Allow browser action", summary, tool="web_browser", action="confirm")
