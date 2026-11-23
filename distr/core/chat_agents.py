"""Persistent conversation-agent profiles. No runtime or global-setting mutation."""
from distr.core.db import AgentConversation, ChatAgent, PrimaryChatAgent, get_session
from threading import Lock

_primary_lock = Lock()


def primary_agent(session):
    selected = session.get(PrimaryChatAgent, 1)
    return session.get(ChatAgent, selected.agent_id) if selected else None


def ensure_primary_agent(settings):
    """Adopt the existing conversational identity once; never follow active-chat changes."""
    from distr.core.db import Chat
    from distr.core.agent.service_factory import resolve_voice_to_display_name
    from distr.core.workflow.development_threads import is_development_thread
    from sqlalchemy.exc import IntegrityError

    with _primary_lock:
        try:
            with get_session() as session:
                existing = primary_agent(session)
                if existing:
                    return primary_agent_payload(existing)
                preferred = [settings.get('agent_current_chat_id'), settings.get('last_chat_id')]
                candidates = [session.get(Chat, int(value)) for value in preferred if value]
                candidates += session.query(Chat).filter(Chat.parent_id.is_(None), Chat.is_archived.is_(False)).order_by(Chat.id).all()
                from distr.core.agent_groups import group_chat_ids

                groups = group_chat_ids(session)
                root = next((chat for chat in candidates if chat and chat.parent_id is None and not chat.is_archived
                             and chat.id not in groups and not is_development_thread(session, chat)), None)
                if not root:
                    return None
                agent = agent_for_conversation(session, root.id)
                if not agent:
                    name = resolve_voice_to_display_name(root.voice_provider or settings.get('tts_provider'), root.voice_model or '', settings)
                    agent = create_agent_profile(session, root, name or 'Oracle', '')
                session.add(PrimaryChatAgent(id=1, agent_id=agent.id))
                session.flush()
                return primary_agent_payload(agent)
        except IntegrityError:
            # Another web process may have initialized the same singleton.
            with get_session() as session:
                existing = primary_agent(session)
                if not existing:
                    raise
                return primary_agent_payload(existing)


def primary_agent_payload(agent):
    return {'agent_id': agent.id, 'name': agent.name, 'skin_name': agent.skin_name,
            'main_conversation_id': agent.main_chat_id, 'oracle_background': agent.oracle_background}


def appearance_owner(session, settings):
    """Target the loaded/live conversation, or the primary when no chat is loaded.

    last_chat_id is the viewed thread only and must not be used as the live-agent fallback.
    """
    from distr.core.db import Chat

    chat_id = getattr(settings, "agent_current_chat_id", None) if settings else None
    chat = session.get(Chat, chat_id) if chat_id else None
    if chat and chat.parent_id is None:
        return agent_for_conversation(session, chat.id), chat
    return primary_agent(session), None


def sync_primary_to_live_chat(chat_id):
    """Star the agent that owns this conversation so the star, loaded chat and live agent agree.

    last_chat_id (the viewed thread) is left unchanged. Returns the primary payload, or None
    when the chat has no agent profile.
    """
    if chat_id is None:
        return None
    try:
        chat_id = int(chat_id)
    except (TypeError, ValueError):
        return None
    if chat_id <= 0:
        return None
    with get_session() as session:
        agent = agent_for_conversation(session, chat_id)
        if not agent:
            return None
        row = session.get(PrimaryChatAgent, 1)
        if row is None:
            session.add(PrimaryChatAgent(id=1, agent_id=agent.id))
        elif row.agent_id != agent.id:
            row.agent_id = agent.id
        session.commit()
        return primary_agent_payload(agent)


def set_agent_skin(session, agent, skin):
    from distr.core.chat_sidebar import set_chat_skin
    from distr.core.db import Chat

    agent.skin_name = skin
    for chat in session.query(Chat).join(AgentConversation).filter(AgentConversation.agent_id == agent.id):
        set_chat_skin(chat, skin)


def validate_agent_profile(name, role=""):
    name, role = str(name or "").strip(), str(role or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Enter an agent name of 1 to 80 characters.")
    if len(role) > 4000:
        raise ValueError("Keep the agent role within 4000 characters.")
    return name, role


def create_agent_profile(session, chat, name, role):
    from distr.core.chat_sidebar import chat_skin
    from distr.core.chat_title_auto import _chat_additional_context
    from distr.core.db import Settings

    name, role = validate_agent_profile(name, role)
    background = _chat_additional_context(chat.additional_context).get("oracle_background")
    if not background:
        settings = session.query(Settings).first()
        background = settings.default_oracle_background if settings else None
    agent = ChatAgent(name=name, role=role, main_chat_id=chat.id,
                      skin_name=chat_skin(chat), provider=chat.provider,
                      oracle_background=background,
                      model_name=chat.model_name, voice_provider=chat.voice_provider,
                      voice_model=chat.voice_model)
    session.add(agent)
    session.flush()
    session.add(AgentConversation(agent_id=agent.id, chat_id=chat.id))
    # New agents go to the bottom of the sidebar.
    from distr.core.agent_groups import next_sort_order
    from distr.core.db import ChatAgentPrefs

    session.add(ChatAgentPrefs(agent_id=agent.id, sort_order=next_sort_order(session)))
    return agent


def agent_for_conversation(session, chat_id):
    return (session.query(ChatAgent).join(AgentConversation, AgentConversation.agent_id == ChatAgent.id)
            .filter(AgentConversation.chat_id == chat_id).first())


def agent_identity(session, chat_id):
    agent = agent_for_conversation(session, chat_id)
    return ({"agent_id": agent.id, "agent_name": agent.name, "agent_role": agent.role,
             "main_conversation_id": agent.main_chat_id} if agent else {})


def agent_instructions(chat_id):
    with get_session() as session:
        agent = agent_for_conversation(session, chat_id)
        if not agent:
            return ""
        text = f"Your agent name is {agent.name}.\nYour assigned role:\n{agent.role}" if agent.role else f"Your agent name is {agent.name}."
        agent_id = agent.id
    try:
        from distr.core.agent_groups import peer_summary
        from distr.core.memory.files import agent_memory_snippet

        peers = peer_summary(agent_id)
        if peers:
            text += ("\nOther agents you can reach with the message_agent tool:\n" + peers)
        private = agent_memory_snippet(agent_id)
        if private:
            text += "\nYour private memory (only you see this):\n" + private
    except Exception:
        pass
    return text
