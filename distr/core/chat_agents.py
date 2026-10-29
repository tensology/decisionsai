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
                root = next((chat for chat in candidates if chat and chat.parent_id is None and not chat.is_archived and not is_development_thread(session, chat)), None)
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
            'main_conversation_id': agent.main_chat_id}


def validate_agent_profile(name, role=""):
    name, role = str(name or "").strip(), str(role or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Enter an agent name of 1 to 80 characters.")
    if len(role) > 4000:
        raise ValueError("Keep the agent role within 4000 characters.")
    return name, role


def create_agent_profile(session, chat, name, role):
    from distr.core.chat_sidebar import chat_skin

    name, role = validate_agent_profile(name, role)
    agent = ChatAgent(name=name, role=role, main_chat_id=chat.id,
                      skin_name=chat_skin(chat), provider=chat.provider,
                      model_name=chat.model_name, voice_provider=chat.voice_provider,
                      voice_model=chat.voice_model)
    session.add(agent)
    session.flush()
    session.add(AgentConversation(agent_id=agent.id, chat_id=chat.id))
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
        return f"Your agent name is {agent.name}.\nYour assigned role:\n{agent.role}" if agent.role else f"Your agent name is {agent.name}."
