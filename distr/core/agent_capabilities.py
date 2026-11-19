"""Per-agent capability sets for workspace tools (Calendar, Snippets, Actions, Development).

Capabilities: read, create, update, delete, run, browse.
Defaults: the primary and the orchestrator get all six; every other agent gets
read + run (browser: open/extract/screenshot freely, click/type only after Paul
confirms in chat). A per-agent override can be stored in chat_agent_prefs.capabilities
(JSON list). Deletes and other destructive changes additionally need Paul's
in-chat confirmation at execution time, whatever the capability set says.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from distr.core.db import ChatAgentPrefs, get_session

logger = logging.getLogger(__name__)

ALL = frozenset({"read", "create", "update", "delete", "run", "browse"})
LIMITED = frozenset({"read", "run"})

# Existing tools that mutate workspace data, by the capability they need.
TOOL_CAPABILITY = {
    "create_action": "create",
    "start_recording": "create",
    "stop_recording": "create",
    "scheduled_action": "update",
    "play_action": "run",
    "stop_action": "run",
    "list_actions": "read",
}

DESTRUCTIVE = frozenset({"delete"})

# Returned when the calling agent cannot be resolved; every check on it fails closed.
UNKNOWN_AGENT = -1


def default_capabilities(agent_id: int, *, primary_id: Optional[int], orchestrator_id: Optional[int]) -> frozenset:
    return ALL if agent_id in {primary_id, orchestrator_id} else LIMITED


def capabilities_for(agent_id: Optional[int]) -> frozenset:
    """Effective capability set. No agents configured (legacy single assistant) keeps
    full access; an agent that could not be resolved (UNKNOWN_AGENT) raises."""
    if agent_id == UNKNOWN_AGENT:
        raise LookupError("calling agent could not be resolved")
    if not agent_id:
        return ALL
    from distr.core.agent_groups import orchestrator_id, primary_agent_id

    with get_session() as session:
        pref = session.get(ChatAgentPrefs, int(agent_id))
        override = None
        if pref is not None and pref.capabilities:
            try:
                override = frozenset(str(c) for c in json.loads(pref.capabilities)) & ALL
            except (TypeError, ValueError):
                override = None
        if override is not None:
            return override
        return default_capabilities(int(agent_id), primary_id=primary_agent_id(session),
                                    orchestrator_id=orchestrator_id(session))


def set_capabilities(agent_id: int, capabilities: Optional[list[str]]) -> frozenset:
    """Store an override (None restores the defaults)."""
    from distr.core.agent_groups import next_sort_order

    value = None
    if capabilities is not None:
        unknown = set(capabilities) - ALL
        if unknown:
            raise ValueError(f"Unknown capability: {sorted(unknown)[0]}")
        value = json.dumps(sorted(set(capabilities)))
    with get_session() as session:
        pref = session.get(ChatAgentPrefs, int(agent_id))
        if pref is None:
            pref = ChatAgentPrefs(agent_id=int(agent_id), sort_order=next_sort_order(session), is_orchestrator=False)
            session.add(pref)
        pref.capabilities = value
        session.commit()
    return capabilities_for(agent_id)


def denied(agent_id: Optional[int], capability: str) -> str:
    """Return a refusal message, or "" when allowed. Fails closed: if the
    capability set cannot be read, the action is refused."""
    try:
        caps = capabilities_for(agent_id)
    except Exception:
        logger.warning("capability lookup failed; refusing %s", capability, exc_info=True)
        return f"Not done: could not check this agent's permission to {capability}. Try again."
    if capability in caps:
        return ""
    name = "This agent"
    try:
        from distr.core.db import ChatAgent

        with get_session() as session:
            agent = session.get(ChatAgent, int(agent_id))
            name = agent.name if agent else name
    except Exception:
        pass
    return (f"Not permitted: {name} can {', '.join(sorted(caps)) or 'not use workspace tools'} "
            f"but not {capability}. Ask the primary or orchestrator agent to do this.")


def tool_block_reason(func_name: str, agent_id: Optional[int] = None) -> str:
    """Capability guard for existing tools, keyed by tool name."""
    capability = TOOL_CAPABILITY.get(str(func_name or ""))
    if not capability:
        return ""
    if agent_id is None:
        from distr.core.agent_groups import current_agent_id

        try:
            agent_id = current_agent_id(strict=True)
        except Exception:
            logger.warning("current agent lookup failed; refusing %s", func_name, exc_info=True)
            return f"Not done: could not check this agent's permission to {capability}. Try again."
    return denied(agent_id, capability)


def request_confirmation(title: str, message: str, *, tool: str = "chat_agent", action: str = "confirm",
                         args: Optional[dict] = None, details: Optional[dict] = None,
                         chat_id: Optional[int] = None, timeout_s: Optional[float] = None,
                         danger: bool = True, **_legacy) -> bool:
    """Ask Paul with an Approve/Deny card in the Chat thread and wait for his click.

    Fails CLOSED (see distr.core.chat_approvals): no thread, app unreachable,
    any error, a deny or no answer within 15 minutes all return False. The old
    Qt dialog path (event_queue / confirmation_results_dict) is no longer used;
    those keyword arguments are accepted and ignored.
    """
    from distr.core.chat_approvals import request_in_chat

    kwargs = {} if timeout_s is None else {"timeout_s": timeout_s}
    try:
        decision = request_in_chat(tool, action, args or {"request": message}, message,
                                   {"title": title, **(details or {})}, chat_id=chat_id, **kwargs)
        return decision.approved
    except Exception:
        logger.warning("confirmation failed; refusing: %s", title, exc_info=True)
        return False


def confirm_destructive(summary: str, *, title: str = "Confirm delete", tool: str = "chat_agent",
                        action: str = "delete", args: Optional[dict] = None,
                        details: Optional[dict] = None, chat_id: Optional[int] = None,
                        timeout_s: Optional[float] = None, **_legacy) -> bool:
    """Deletes and other destructive changes always need Paul's in-chat click. Fails closed."""
    return request_confirmation(title, summary, tool=tool, action=action, args=args, details=details,
                                chat_id=chat_id, timeout_s=timeout_s, danger=True)


def safe_confirm(confirm) -> "callable":
    """Wrap a confirm callback so a missing callback or any exception means no."""
    def _inner(summary: str) -> bool:
        if confirm is None:
            return False
        try:
            return confirm(summary) is True
        except Exception:
            logger.warning("confirm callback failed; refusing", exc_info=True)
            return False
    return _inner


# Browser policy -------------------------------------------------------------
# read actions: any agent with "read". Mutating actions (click/type/submit/fill):
#   - agents with "browse" (primary, orchestrator): allowed, still behind the
#     existing approval gates for submits/purchases;
#   - everyone else: only after Paul confirms that specific action.
# Unknown capability state => refused.

def browser_policy(agent_id: Optional[int], *, mutating: bool) -> str:
    """Return "allow", "confirm" or "deny"."""
    try:
        caps = capabilities_for(agent_id)
    except Exception:
        logger.warning("capability lookup failed; browser denied", exc_info=True)
        return "deny"
    if "read" not in caps:
        return "deny"
    if not mutating:
        return "allow"
    return "allow" if "browse" in caps else "confirm"
