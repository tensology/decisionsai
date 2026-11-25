"""Multi-agent chat: agent roster, primary/orchestrator, group threads, agent-to-agent.

Group threads are ordinary root ``chats`` rows with ``chat_group_members`` rows.
Each message is a child row: the owner's messages use ``input``; agent messages use
``response`` with ``params.group_speaker`` naming the agent.

Agents in groups and agent-to-agent exchanges run on each agent's own configured
provider/model through the provider API (llm_factory.create_stream). No fallbacks:
if an agent's model cannot run there, that agent reports the error.
Several agents generate at once (one thread each); the live desktop runtime keeps
running its loaded agent independently.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, Iterable, Optional

from distr.core.db import (AgentConversation, Chat, ChatAgent, ChatAgentPrefs, ChatGroupMember,
                           PrimaryChatAgent, get_session)
from distr.core.db.time import utc_now_naive

logger = logging.getLogger(__name__)

MAX_AGENT_REPLIES = 6      # agent replies per user message (loop guard)
MAX_DEPTH = 3              # delegation hops per user message
MAX_PER_AGENT = 2          # replies by one agent per user message
# Hard cap on one agent's reply. A provider that never returns must not hang the
# whole group turn or the agent-to-agent tool that is waiting on it.
AGENT_REPLY_TIMEOUT_SECS = 120.0
# Wall-clock budget for the whole turn (all hops together). Bounds a chain that
# keeps handing off even when each individual reply is quick.
TURN_BUDGET_SECS = 300.0
HISTORY_LIMIT = 40         # transcript rows sent to the model

Emit = Callable[[dict], None]


class GroupError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def _owner(default: str) -> str:
    from distr.core.agent.owner_identity import owner_label

    return owner_label(default)


def _owner_who() -> str:
    name = _owner("")
    return f"{name} (the user)" if name else "the user"


def _prefs(session) -> dict[int, ChatAgentPrefs]:
    return {p.agent_id: p for p in session.query(ChatAgentPrefs).all()}


def _effective_order(agent: ChatAgent, prefs: dict) -> int:
    pref = prefs.get(agent.id)
    return int(pref.sort_order) if pref is not None else 10_000 + int(agent.id)


def next_sort_order(session) -> int:
    prefs = _prefs(session)
    agents = session.query(ChatAgent).all()
    orders = [_effective_order(a, prefs) for a in agents if a.id is not None]
    return (max(orders) + 1) if orders else 0


def ordered_agents(session) -> list[ChatAgent]:
    prefs = _prefs(session)
    return sorted(session.query(ChatAgent).all(), key=lambda a: (_effective_order(a, prefs), a.id))


def primary_agent_id(session) -> Optional[int]:
    row = session.get(PrimaryChatAgent, 1)
    return int(row.agent_id) if row else None


def orchestrator_id(session) -> Optional[int]:
    """Use the loaded agent, or the starred agent when no agent is loaded."""
    from distr.core.chat_agents import appearance_owner
    from distr.core.db import Settings

    owner, _ = appearance_owner(session, session.query(Settings).first())
    return int(owner.id) if owner else primary_agent_id(session)


def agent_payload(agent: ChatAgent, *, primary_id=None, orch_id=None, order=None) -> dict:
    return {"id": agent.id, "name": agent.name, "role": agent.role or "", "skin_name": agent.skin_name,
            "provider": agent.provider, "model_name": agent.model_name,
            "voice_provider": agent.voice_provider, "voice_model": agent.voice_model,
            "main_chat_id": agent.main_chat_id, "is_primary": agent.id == primary_id,
            "is_orchestrator": agent.id == orch_id, "sort_order": order}


def list_agents() -> list[dict]:
    with get_session() as session:
        prefs = _prefs(session)
        pid, oid = primary_agent_id(session), orchestrator_id(session)
        return [agent_payload(a, primary_id=pid, orch_id=oid, order=_effective_order(a, prefs))
                for a in ordered_agents(session)]


def reorder_agents(agent_ids: Iterable[int]) -> list[dict]:
    ids = [int(i) for i in agent_ids]
    with get_session() as session:
        known = {a.id for a in session.query(ChatAgent).all()}
        if len(set(ids)) != len(ids) or not set(ids) <= known:
            raise GroupError("Unknown or duplicate agent in order.")
        # Agents left out keep their relative order after the given ones.
        rest = [a.id for a in ordered_agents(session) if a.id not in set(ids)]
        prefs = _prefs(session)
        for index, agent_id in enumerate(ids + rest):
            pref = prefs.get(agent_id)
            if pref is None:
                session.add(ChatAgentPrefs(agent_id=agent_id, sort_order=index))
            else:
                pref.sort_order = index
        session.commit()
    return list_agents()


def set_primary(agent_id: int) -> dict:
    """Make this agent primary. The caller then loads its chat into the live runtime."""
    from distr.core.chat_agents import primary_agent_payload

    with get_session() as session:
        agent = session.get(ChatAgent, int(agent_id))
        if not agent:
            raise GroupError("Agent not found.")
        if not agent.main_chat_id:
            raise GroupError("This agent has no conversation to load.")
        row = session.get(PrimaryChatAgent, 1)
        if row is None:
            session.add(PrimaryChatAgent(id=1, agent_id=agent.id))
        else:
            row.agent_id = agent.id
        session.commit()
        return primary_agent_payload(agent)


def update_agent(agent_id: int, *, name: Optional[str] = None, role: Optional[str] = None) -> dict:
    from distr.core.chat_agents import validate_agent_profile

    with get_session() as session:
        agent = session.get(ChatAgent, int(agent_id))
        if not agent:
            raise GroupError("Agent not found.")
        new_name, new_role = validate_agent_profile(agent.name if name is None else name,
                                                    agent.role if role is None else role)
        agent.name, agent.role = new_name, new_role
        session.commit()
    return next(a for a in list_agents() if a["id"] == int(agent_id))


def delete_agent(agent_id: int) -> dict:
    """Really delete a non-primary agent: its conversations, group seats, prefs and private memory."""
    from distr.core.chat import cleanup_chat_dependencies, remove_chat_transcript_audit_events

    agent_id = int(agent_id)
    with get_session() as session:
        agent = session.get(ChatAgent, agent_id)
        if not agent:
            raise GroupError("Agent not found.")
        if primary_agent_id(session) == agent_id:
            raise GroupError("Make another agent primary before deleting this one.")
        chat_ids = {row.chat_id for row in session.query(AgentConversation).filter(AgentConversation.agent_id == agent_id)}
        if agent.main_chat_id:
            chat_ids.add(agent.main_chat_id)
        deleted = []
        for chat_id in sorted(chat_ids):
            chat = session.get(Chat, chat_id)
            if chat is None:
                continue
            cleanup_chat_dependencies(session, chat_id, delete_owned_ticket=True, delete_owned_workflows=True)
            session.delete(chat)
            deleted.append(chat_id)
        session.query(AgentConversation).filter(AgentConversation.agent_id == agent_id).delete(synchronize_session=False)
        session.query(ChatGroupMember).filter(ChatGroupMember.agent_id == agent_id).delete(synchronize_session=False)
        session.query(ChatAgentPrefs).filter(ChatAgentPrefs.agent_id == agent_id).delete(synchronize_session=False)
        session.flush()
        session.delete(session.get(ChatAgent, agent_id))
        session.commit()
    for chat_id in deleted:
        try:
            remove_chat_transcript_audit_events(chat_id)
        except Exception:
            logger.debug("transcript audit cleanup failed", exc_info=True)
    try:
        from distr.core.memory.files import delete_agent_memory
        from distr.core.orchestrator_memory import delete_agent_memories

        delete_agent_memory(agent_id)
        delete_agent_memories(agent_id)
    except Exception:
        logger.warning("Agent %s memory cleanup failed", agent_id, exc_info=True)
    return {"deleted_agent_id": agent_id, "deleted_chat_ids": deleted}


def peer_summary(agent_id: int) -> str:
    with get_session() as session:
        oid = orchestrator_id(session)
        lines = []
        for agent in ordered_agents(session):
            if agent.id == agent_id:
                continue
            role = (agent.role or "").strip().splitlines()[0][:120] if (agent.role or "").strip() else ""
            tag = " (orchestrator)" if agent.id == oid else ""
            lines.append(f"- {agent.name}{tag}" + (f": {role}" if role else ""))
        return "\n".join(lines)


def resolve_agent(target: Any, *, exclude: Optional[int] = None) -> Optional[ChatAgent]:
    """Find an agent by id, full name, or unambiguous first name (case-insensitive)."""
    with get_session() as session:
        agents = [a for a in ordered_agents(session) if a.id != exclude]
        session.expunge_all()
    raw = str(target or "").strip().lstrip("@")
    if raw.isdigit():
        return next((a for a in agents if a.id == int(raw)), None)
    low = raw.lower()
    exact = [a for a in agents if a.name.lower() == low]
    if exact:
        return exact[0]
    first = [a for a in agents if a.name.lower().split()[0] == low or a.name.lower().startswith(low)]
    return first[0] if len(first) == 1 else None


# ---------------------------------------------------------------------------
# Group threads
# ---------------------------------------------------------------------------

def _params(chat) -> dict:
    try:
        value = json.loads(chat.params or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def is_group_chat(session, chat_id: int) -> bool:
    return session.query(ChatGroupMember).filter(ChatGroupMember.chat_id == int(chat_id)).first() is not None


def group_chat_ids(session) -> set[int]:
    return {row.chat_id for row in session.query(ChatGroupMember.chat_id).distinct()}


def _member_agents(session, chat_id: int) -> list[ChatAgent]:
    rows = (session.query(ChatAgent).join(ChatGroupMember, ChatGroupMember.agent_id == ChatAgent.id)
            .filter(ChatGroupMember.chat_id == int(chat_id))
            .order_by(ChatGroupMember.position, ChatAgent.id).all())
    return rows


def _group_payload(session, root: Chat) -> dict:
    pid, oid = primary_agent_id(session), orchestrator_id(session)
    members = [agent_payload(a, primary_id=pid, orch_id=oid) for a in _member_agents(session, root.id)]
    meta = _params(root).get("group") or {}
    last = (session.query(Chat).filter(Chat.parent_id == root.id, Chat.is_hidden.is_(False))
            .order_by(Chat.id.desc()).first())
    preview = ((last.response or last.input or "") if last else "").strip()
    return {"id": root.id, "title": root.title or ", ".join(m["name"] for m in members),
            "kind": meta.get("kind") or "group", "members": members, "latest_message": preview[:200],
            "modified_date": root.modified_date.isoformat() if root.modified_date else None}


def list_groups() -> list[dict]:
    with get_session() as session:
        ids = group_chat_ids(session)
        roots = (session.query(Chat).filter(Chat.id.in_(ids), Chat.is_archived.is_(False))
                 .order_by(Chat.modified_date.desc(), Chat.id.desc()).all()) if ids else []
        return [_group_payload(session, root) for root in roots]


def create_group(agent_ids: Iterable[int], title: str = "", *, kind: str = "group") -> dict:
    ids = list(dict.fromkeys(int(i) for i in agent_ids))
    if len(ids) < (2 if kind == "agents" else 1):
        raise GroupError("Choose at least one agent." if kind == "group" else "Two agents are needed.")
    with get_session() as session:
        agents = {a.id: a for a in session.query(ChatAgent).filter(ChatAgent.id.in_(ids)).all()}
        if set(agents) != set(ids):
            raise GroupError("Unknown agent.")
        title = (title or "").strip()[:120] or (" ↔ ".join if kind == "agents" else ", ".join)(agents[i].name for i in ids)
        now = utc_now_naive()
        root = Chat(title=title, input="", response="", params=json.dumps({"group": {"kind": kind}}),
                    is_new=False, created_date=now, modified_date=now)
        session.add(root)
        session.flush()
        for position, agent_id in enumerate(ids):
            session.add(ChatGroupMember(chat_id=root.id, agent_id=agent_id, position=position))
        session.commit()
        return _group_payload(session, root)


def set_group_members(chat_id: int, agent_ids: Iterable[int]) -> dict:
    ids = list(dict.fromkeys(int(i) for i in agent_ids))
    if not ids:
        raise GroupError("A group needs at least one agent.")
    with get_session() as session:
        root = session.get(Chat, int(chat_id))
        if not root or not is_group_chat(session, root.id):
            raise GroupError("Group not found.")
        if session.query(ChatAgent).filter(ChatAgent.id.in_(ids)).count() != len(ids):
            raise GroupError("Unknown agent.")
        session.query(ChatGroupMember).filter(ChatGroupMember.chat_id == root.id).delete(synchronize_session=False)
        for position, agent_id in enumerate(ids):
            session.add(ChatGroupMember(chat_id=root.id, agent_id=agent_id, position=position))
        session.commit()
        return _group_payload(session, root)


def archive_group(chat_id: int) -> None:
    with get_session() as session:
        root = session.get(Chat, int(chat_id))
        if not root or not is_group_chat(session, root.id):
            raise GroupError("Group not found.")
        root.is_archived = True
        session.commit()


def _speaker(row) -> dict:
    value = _params(row).get("group_speaker")
    return value if isinstance(value, dict) else {}


def group_messages(chat_id: int) -> dict:
    with get_session() as session:
        root = session.get(Chat, int(chat_id))
        if not root or not is_group_chat(session, root.id):
            raise GroupError("Group not found.")
        rows = (session.query(Chat).filter(Chat.parent_id == root.id, Chat.is_hidden.is_(False))
                .order_by(Chat.id).all())
        names = {a.id: a for a in session.query(ChatAgent).all()}
        messages = []
        for row in rows:
            speaker = _speaker(row)
            if row.input:
                messages.append({"id": row.id, "role": "user", "content": row.input,
                                 "timestamp": row.created_date.isoformat() if row.created_date else None})
            if row.response:
                agent = names.get(speaker.get("agent_id"))
                messages.append({"id": row.id, "role": "agent", "content": row.response,
                                 "agent_id": speaker.get("agent_id"),
                                 "agent_name": agent.name if agent else speaker.get("agent_name") or "Agent",
                                 "skin_name": agent.skin_name if agent else None,
                                 "error": bool(speaker.get("error")),
                                 "timestamp": (row.modified_date or row.created_date).isoformat() if (row.modified_date or row.created_date) else None})
        return {**_group_payload(session, root), "messages": messages}


def _append_row(chat_id: int, *, user_text: str = "", agent: Optional[ChatAgent] = None,
                text: str = "", error: bool = False) -> int:
    with get_session() as session:
        root = session.get(Chat, int(chat_id))
        now = utc_now_naive()
        speaker = ({"kind": "agent", "agent_id": agent.id, "agent_name": agent.name, "error": error}
                   if agent is not None else {"kind": "user"})
        row = Chat(parent_id=root.id, title=root.title, input=user_text or None,
                   response=(text or None) if agent is not None else None,
                   params=json.dumps({"group_speaker": speaker}), created_date=now, modified_date=now)
        session.add(row)
        root.modified_date = now
        session.commit()
        return row.id


# ---------------------------------------------------------------------------
# Who answers
# ---------------------------------------------------------------------------

def mentioned_agents(text: str, members: list[ChatAgent], *, exclude: Optional[int] = None,
                     leading_name: bool = False) -> list[ChatAgent]:
    """Members addressed with @Name / @FirstName (or, for the owner, a leading "Name,")."""
    raw = text or ""
    found: list[tuple[int, ChatAgent]] = []
    for agent in members:
        if agent.id == exclude:
            continue
        names = sorted({agent.name, agent.name.split()[0]}, key=len, reverse=True)
        best = None
        for name in names:
            match = re.search(r"@" + re.escape(name) + r"(?![\w])", raw, re.IGNORECASE)
            if match is None and leading_name:
                match = re.match(r"\s*(?:hey\s+|hi\s+)?" + re.escape(name) + r"\s*[,:]", raw, re.IGNORECASE)
            if match is not None and (best is None or match.start() < best):
                best = match.start()
        if best is not None:
            found.append((best, agent))
    return [agent for _, agent in sorted(found, key=lambda item: item[0])]


def first_responders(text: str, members: list[ChatAgent], *, primary: Optional[int],
                     orchestrator: Optional[int], kind: str = "group") -> list[ChatAgent]:
    """Mentions win; else the orchestrator (if a member); else the primary (if a member);
    else the first member."""
    addressed = mentioned_agents(text, members, leading_name=True)
    if addressed:
        return addressed
    by_id = {a.id: a for a in members}
    for preferred in (orchestrator, primary):
        if preferred in by_id:
            return [by_id[preferred]]
    return members[:1]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _memory_block(agent_id: int) -> str:
    parts = []
    try:
        from distr.core.memory.files import agent_memory_snippet, load_context_snippets_for_llm

        shared = load_context_snippets_for_llm()
        if shared.get("user"):
            parts.append(f"Shared notes about {_owner('the user')}:\n" + shared["user"][:2000])
        if shared.get("memory"):
            parts.append("Shared memory:\n" + shared["memory"][:2500])
        private = agent_memory_snippet(agent_id)
        if private:
            parts.append("Your private memory (only you see this):\n" + private)
    except Exception:
        logger.debug("group memory context unavailable", exc_info=True)
    try:
        from distr.core.orchestrator_memory import build_memory_context

        learned = build_memory_context(limit=15, agent_id=agent_id)
        if learned:
            parts.append("Learned preferences:\n" + learned[:2000])
    except Exception:
        pass
    return "\n\n".join(parts)


def system_prompt(agent: ChatAgent, members: list[ChatAgent], *, orchestrator: Optional[int], kind: str) -> str:
    roster = []
    for member in members:
        tag = " (orchestrator)" if member.id == orchestrator else ""
        role = (member.role or "").strip().replace("\n", " ")[:160]
        roster.append(f"- {member.name}{tag}" + (f": {role}" if role else ""))
    where = ("a direct conversation with another AI agent" if kind == "agents"
             else f"a group chat with {_owner_who()} and other AI agents")
    lines = [f"You are {agent.name}, in {where}.",
             f"Your role: {agent.role.strip()}" if (agent.role or "").strip() else "Your role: general assistant.",
             "Members:", *roster,
             "Messages from others start with their name in brackets. Reply as yourself only, "
             "without a name prefix, and keep it short and conversational.",
             "To ask another member something, @mention them by name with a specific request. "
             "Only mention someone when you actually need them; never mention yourself."]
    if agent.id == orchestrator:
        lines.append(f"You are the orchestrator: answer {_owner('the user')} first, then delegate parts of the work to the "
                     "best-suited members by @mentioning them, and pull their answers together.")
    memory = _memory_block(agent.id)
    if memory:
        lines += ["", memory]
    return "\n".join(lines)


def transcript_for(agent: ChatAgent, chat_id: int, names: dict[int, str]) -> list[dict]:
    with get_session() as session:
        rows = (session.query(Chat).filter(Chat.parent_id == int(chat_id), Chat.is_hidden.is_(False))
                .order_by(Chat.id.desc()).limit(HISTORY_LIMIT).all())
        rows = list(reversed(rows))
        out = []
        for row in rows:
            speaker = _speaker(row)
            if row.input:
                out.append({"role": "user", "content": f"[{_owner('User')}] {row.input}"})
            if row.response and not speaker.get("error"):
                if speaker.get("agent_id") == agent.id:
                    out.append({"role": "assistant", "content": row.response})
                else:
                    name = names.get(speaker.get("agent_id")) or speaker.get("agent_name") or "Agent"
                    out.append({"role": "user", "content": f"[{name}] {row.response}"})
        # Providers expect the conversation to end on a user turn.
        if out and out[-1]["role"] == "assistant":
            out.append({"role": "user", "content": "[System] Continue only if you have something new to add."})
        return out


def _clean_reply(agent: ChatAgent, text: str) -> str:
    text = (text or "").strip()
    for prefix in (f"[{agent.name}]", f"{agent.name}:", f"**{agent.name}:**"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
    return text


def _stream_agent(agent: ChatAgent, messages: list[dict], emit: Emit) -> str:
    from distr.core.llm_factory import create_stream
    from distr.core.settings import load_settings_from_db

    provider = (agent.provider or "").strip().lower()
    if not provider or not (agent.model_name or "").strip():
        raise GroupError(f"{agent.name} has no model configured.")
    deadline = time.monotonic() + AGENT_REPLY_TIMEOUT_SECS
    chunks = []
    for token in create_stream(provider, agent.model_name, messages, load_settings_from_db()):
        # Belt-and-braces: the provider client has its own request timeout, but a
        # stream that keeps trickling is also bounded here.
        if time.monotonic() > deadline:
            raise GroupError(f"{agent.name} took too long to reply.")
        chunks.append(token)
        emit({"type": "token", "agent_id": agent.id, "text": token})
    return "".join(chunks)


def run_turn(chat_id: int, message: str, *, emit: Optional[Emit] = None,
             from_agent_id: Optional[int] = None, responders: Optional[list[int]] = None,
             max_replies: int = MAX_AGENT_REPLIES, chain: bool = True,
             generate: Optional[Callable[[ChatAgent, list[dict], Emit], str]] = None) -> list[dict]:
    """Persist one message to a group and let the right agents answer (concurrently per hop).

    ``from_agent_id`` posts the message as that agent (agent-to-agent). Returns the
    replies in completion order. Loop guard: at most ``max_replies`` replies,
    ``MAX_DEPTH`` hops and ``MAX_PER_AGENT`` replies per agent.
    """
    emit = emit or (lambda _event: None)
    generate = generate or _stream_agent
    text = (message or "").strip()
    if not text:
        raise GroupError("Message is required.")
    with get_session() as session:
        root = session.get(Chat, int(chat_id))
        if not root or not is_group_chat(session, root.id):
            raise GroupError("Group not found.")
        kind = (_params(root).get("group") or {}).get("kind") or "group"
        members = _member_agents(session, root.id)
        names = {a.id: a.name for a in session.query(ChatAgent).all()}
        pid, oid = primary_agent_id(session), orchestrator_id(session)
        sender = session.get(ChatAgent, int(from_agent_id)) if from_agent_id else None
        session.expunge_all()
    if from_agent_id and sender is None:
        raise GroupError("Sending agent not found.")
    if sender is not None:
        row_id = _append_row(chat_id, agent=sender, text=text)
        emit({"type": "message", "role": "agent", "id": row_id, "agent_id": sender.id,
              "agent_name": sender.name, "content": text})
    else:
        row_id = _append_row(chat_id, user_text=text)
        emit({"type": "message", "role": "user", "id": row_id, "content": text})

    by_id = {a.id: a for a in members}
    if responders:
        wave = [by_id[i] for i in responders if i in by_id]
    elif sender is not None:
        wave = mentioned_agents(text, members, exclude=sender.id) or [a for a in members if a.id != sender.id][:1]
    else:
        wave = first_responders(text, members, primary=pid, orchestrator=oid, kind=kind)
    counts: dict[int, int] = {}
    replies: list[dict] = []
    lock = threading.Lock()

    def answer(agent: ChatAgent) -> dict:
        emit({"type": "start", "agent_id": agent.id, "agent_name": agent.name, "skin_name": agent.skin_name})
        error = False
        try:
            convo = [{"role": "system", "content": system_prompt(agent, members, orchestrator=oid, kind=kind)}]
            convo += transcript_for(agent, chat_id, names)
            reply = _clean_reply(agent, generate(agent, convo, emit))
            if not reply:
                raise GroupError(f"{agent.name}'s model returned an empty reply.")
        except Exception as exc:  # report, never fall back to another model
            logger.warning("Group agent %s failed: %s", agent.id, exc)
            error = True
            reply = f"{agent.name} couldn't answer: {str(exc)[:300] or 'model error'}"
        row = _append_row(chat_id, agent=agent, text=reply, error=error)
        result = {"id": row, "agent_id": agent.id, "agent_name": agent.name, "content": reply, "error": error,
                  "voice_provider": agent.voice_provider, "voice_model": agent.voice_model}
        emit({"type": "done", **result})
        return result

    depth = 0
    turn_started = time.monotonic()
    while wave and depth < MAX_DEPTH:
        if time.monotonic() - turn_started > TURN_BUDGET_SECS:
            logger.warning("Group turn exceeded its %.0fs budget; stopping", TURN_BUDGET_SECS)
            emit({"type": "capped", "limit": max_replies, "reason": "time_budget"})
            break
        with lock:
            room = max_replies - len(replies)
            wave = [a for a in dict.fromkeys(wave) if counts.get(a.id, 0) < MAX_PER_AGENT][:max(room, 0)]
        if not wave:
            break
        for agent in wave:
            counts[agent.id] = counts.get(agent.id, 0) + 1
        with ThreadPoolExecutor(max_workers=len(wave)) as pool:
            futures = {pool.submit(answer, agent): agent for agent in wave}
            done, _pending = wait(list(futures), timeout=AGENT_REPLY_TIMEOUT_SECS)
            results = []
            for future in list(futures):
                agent = futures[future]
                if future in done:
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        logger.warning("Group agent %s failed: %s", agent.id, exc)
                        results.append({
                            "id": None, "agent_id": agent.id, "agent_name": agent.name,
                            "content": f"{agent.name} couldn't answer: {str(exc)[:300] or 'model error'}",
                            "error": True,
                        })
                else:
                    future.cancel()
                    logger.warning(
                        "Group agent %s exceeded %.0fs and was skipped",
                        agent.id, AGENT_REPLY_TIMEOUT_SECS,
                    )
                    results.append({
                        "id": None, "agent_id": agent.id, "agent_name": agent.name,
                        "content": f"{agent.name} took longer than {AGENT_REPLY_TIMEOUT_SECS:.0f}s to reply.",
                        "error": True,
                    })
        replies.extend(results)
        depth += 1
        if not chain:
            break
        next_wave: list[ChatAgent] = []
        for result in results:
            if result["error"]:
                continue
            next_wave += mentioned_agents(result["content"], members, exclude=result["agent_id"])
        wave = next_wave
    if wave and (len(replies) >= max_replies or depth >= MAX_DEPTH):
        emit({"type": "capped", "limit": max_replies})
    emit({"type": "end", "replies": len(replies)})
    return replies


def stream_turn(chat_id: int, message: str, **kwargs):
    """Run a turn in a worker thread and yield its events as they happen."""
    events: "queue.Queue[Optional[dict]]" = queue.Queue()

    def worker():
        try:
            run_turn(chat_id, message, emit=events.put, **kwargs)
        except GroupError as exc:
            events.put({"type": "error", "message": str(exc)})
        except Exception:
            logger.exception("Group turn failed")
            events.put({"type": "error", "message": "The group turn failed."})
        finally:
            events.put(None)

    threading.Thread(target=worker, name=f"group-turn-{chat_id}", daemon=True).start()
    while True:
        event = events.get()
        if event is None:
            return
        yield event


# ---------------------------------------------------------------------------
# Agent-to-agent
# ---------------------------------------------------------------------------

def agents_thread(a_id: int, b_id: int) -> int:
    """The direct thread between two agents, created on first use."""
    with get_session() as session:
        for chat_id in group_chat_ids(session):
            root = session.get(Chat, chat_id)
            if not root or root.is_archived or (_params(root).get("group") or {}).get("kind") != "agents":
                continue
            ids = {m.agent_id for m in session.query(ChatGroupMember).filter(ChatGroupMember.chat_id == chat_id)}
            if ids == {int(a_id), int(b_id)}:
                return chat_id
    return create_group([a_id, b_id], kind="agents")["id"]


def message_agent(from_agent_id: int, target: Any, message: str, *, group_id: Optional[int] = None,
                  generate=None, max_replies: int = 3, chain: bool = True) -> dict:
    """Send a message from one agent to another and return the reply.

    With ``group_id`` the exchange happens in that group (both must be members);
    otherwise in the two agents' direct thread. The recipient answers, and may
    @mention another member to pull them in — bounded by ``max_replies``,
    ``MAX_DEPTH`` hops and ``MAX_PER_AGENT`` replies per agent, so a chain can
    never run away. The sender gets the recipient's own reply back (the rest of
    the exchange is in the shared thread).
    """
    recipient = resolve_agent(target, exclude=int(from_agent_id))
    if recipient is None:
        raise GroupError(f"No other agent called {target!r}.")
    chat_id = int(group_id) if group_id else agents_thread(int(from_agent_id), recipient.id)
    replies = run_turn(chat_id, message, from_agent_id=int(from_agent_id), responders=[recipient.id],
                       max_replies=max(1, int(max_replies)), chain=bool(chain), generate=generate)
    # The recipient's own reply is the answer; a chained reply from a third agent
    # is collaboration, not the answer to hand back.
    reply = next((r for r in replies if int(r.get("agent_id", 0)) == int(recipient.id)),
                 replies[0] if replies else {"content": "", "error": True})
    return {"chat_id": chat_id, "agent_id": recipient.id, "agent_name": recipient.name,
            "reply": reply["content"], "error": bool(reply.get("error")),
            "replies": len(replies)}


def current_agent_id(strict: bool = False) -> Optional[int]:
    """The agent whose conversation is loaded in the live runtime (else the primary).
    With strict=True a lookup error is raised instead of returning None, so
    permission checks can fail closed."""
    try:
        from distr.core.chat import ChatService
        from distr.core.chat_agents import agent_for_conversation

        chat_id = ChatService.get_current_chat_id()
        with get_session() as session:
            if chat_id:
                agent = agent_for_conversation(session, int(chat_id))
                if agent:
                    return int(agent.id)
            return primary_agent_id(session)
    except Exception:
        if strict:
            raise
        return None
