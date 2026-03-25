"""
chat.py - ChatService (pure DB operations) + backward-compat ChatManager alias.

The actual ChatManager logic now lives in distr.core.chat_manager.ChatManagerCore.
The Qt signal bridge is in distr.core.chat_qt_adapter.ChatManagerQt.
"""

from typing import List, Optional, Tuple
import logging
from datetime import datetime, timezone
from sqlalchemy import text
from distr.core.db import get_session, Chat, Settings

logger = logging.getLogger(__name__)

_PROVIDER_NORMALIZE = {
    "ollama": "Ollama",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "kilocode": "KiloCode",
    "kokoro": "Kokoro",
    "elevenlabs": "ElevenLabs",
}


def _normalize_provider(provider: Optional[str]) -> str:
    if not provider or not str(provider).strip():
        return "Ollama"
    key = str(provider).strip().lower()
    return _PROVIDER_NORMALIZE.get(key, provider.strip())


def _title_from_question(question: str, max_len: int = 50) -> str:
    if not question or not question.strip():
        return "New Chat"
    line = " ".join(question.strip().split())
    return (line[:max_len] + "\u2026") if len(line) > max_len else line


class ChatService:
    """Central service for chat DB and settings. Used by web routes and ChatManager. No Qt/signals."""

    @staticmethod
    def create_new_chat(
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        tts_provider: Optional[str] = None,
        tts_voice: Optional[str] = None,
        title: Optional[str] = None,
        starting_question: Optional[str] = None,
    ) -> Tuple[int, Optional[str]]:
        """Create a new chat (root + child). Sets last_chat_id and agent_current_chat_id. Returns (chat_id, starting_question or None)."""
        with get_session() as session:
            settings = session.query(Settings).first()
            if settings:
                if not llm_provider:
                    llm_provider = getattr(
                        settings, "conversational_llm_provider", None
                    ) or getattr(settings, "agent_provider", None)
                if not llm_model:
                    llm_model = getattr(
                        settings, "conversational_llm_model", None
                    ) or getattr(settings, "agent_model", None)
                if not tts_provider:
                    tts_provider = getattr(settings, "tts_provider", None) or getattr(
                        settings, "voice_provider", None
                    )
                if not tts_voice:
                    tts_voice = (
                        getattr(settings, "kokoro_voice", None)
                        or getattr(settings, "openai_voice", None)
                        or getattr(settings, "elevenlabs_voice", None)
                    )

        provider = _normalize_provider(llm_provider or "ollama")
        model_name = (llm_model or "").strip() or None
        voice_provider = (tts_provider or "").strip() or None
        voice_model = (tts_voice or "").strip() or None
        starting_question = (starting_question or "").strip() or None
        if not title and starting_question:
            title = _title_from_question(starting_question)
        title = (title or "").strip() or "New Chat"
        with get_session() as session:
            root = Chat(
                parent_id=None,
                title=title,
                input=None,
                response=None,
                provider=provider,
                model_name=model_name,
                voice_provider=voice_provider,
                voice_model=voice_model,
                created_date=datetime.now(timezone.utc),
                modified_date=datetime.now(timezone.utc),
            )
            session.add(root)
            session.commit()
            session.refresh(root)
            chat_id = root.id
            if starting_question:
                child = Chat(
                    parent_id=chat_id,
                    title=None,
                    input=starting_question,
                    response=None,
                    provider=provider,
                    model_name=model_name,
                    voice_provider=voice_provider,
                    voice_model=voice_model,
                    created_date=datetime.now(timezone.utc),
                    modified_date=datetime.now(timezone.utc),
                )
                session.add(child)
                session.commit()
            settings_row = session.query(Settings).first()
            if settings_row:
                settings_row.last_chat_id = chat_id
                settings_row.agent_current_chat_id = chat_id
                session.commit()
                logger.info(
                    "ChatService: set last_chat_id and agent_current_chat_id to %s",
                    chat_id,
                )
            else:
                logger.warning("ChatService: no Settings row to update")
        return (chat_id, starting_question if starting_question else None)

    @staticmethod
    def get_chat_history(chat_id: int) -> List[dict]:
        """Return list of {role, content} for the thread (no system message)."""
        with get_session() as session:
            chat = session.get(Chat, chat_id)
            if not chat:
                return []
            root_query = text("""
                WITH RECURSIVE parents(id, parent_id) AS (
                    SELECT id, parent_id FROM chats WHERE id = :start_id
                    UNION ALL
                    SELECT c.id, c.parent_id FROM chats c JOIN parents p ON c.id = p.parent_id
                )
                SELECT id FROM parents WHERE parent_id IS NULL
            """)
            root_result = session.execute(root_query, {"start_id": chat_id}).fetchone()
            root_id = root_result[0] if root_result else chat_id
            thread_query = text("""
                WITH RECURSIVE chat_tree(id, parent_id, input, response, is_hidden, created_date) AS (
                    SELECT id, parent_id, input, response, is_hidden, created_date
                    FROM chats WHERE id = :root_id
                    UNION ALL
                    SELECT c.id, c.parent_id, c.input, c.response, c.is_hidden, c.created_date
                    FROM chats c JOIN chat_tree ct ON c.parent_id = ct.id
                )
                SELECT input, response, is_hidden FROM chat_tree ORDER BY created_date
            """)
            rows = session.execute(thread_query, {"root_id": root_id}).fetchall()
            messages = []
            for row in rows:
                if row.input:
                    messages.append({"role": "user", "content": row.input})
                if row.response:
                    messages.append({"role": "assistant", "content": row.response})
            return messages

    @staticmethod
    def add_user_message(chat_id: int, message: str) -> None:
        """Append a user message to the chat thread in the DB."""
        cleaned = (message or "").strip()
        if not cleaned:
            return
        with get_session() as session:
            chat = session.get(Chat, chat_id)
            if not chat:
                logger.warning(
                    "ChatService.add_user_message: Chat %s doesn't exist, message not persisted",
                    chat_id,
                )
                return
            root_query = text("""
                WITH RECURSIVE parents(id, parent_id) AS (
                    SELECT id, parent_id FROM chats WHERE id = :start_id
                    UNION ALL
                    SELECT c.id, c.parent_id FROM chats c JOIN parents p ON c.id = p.parent_id
                )
                SELECT id FROM parents WHERE parent_id IS NULL
            """)
            root_row = session.execute(root_query, {"start_id": chat_id}).fetchone()
            root_id = root_row[0] if root_row else chat_id
            root = session.get(Chat, root_id)
            if not root:
                return
            child = Chat(
                parent_id=root_id,
                title=cleaned.split("\n")[0][:50] if cleaned else None,
                input=cleaned,
                response=None,
                provider=root.provider,
                model_name=root.model_name,
                voice_provider=root.voice_provider,
                voice_model=root.voice_model,
                created_date=datetime.now(timezone.utc),
                modified_date=datetime.now(timezone.utc),
            )
            session.add(child)
            session.commit()

    @staticmethod
    def get_current_chat_id() -> Optional[int]:
        with get_session() as session:
            settings = session.query(Settings).first()
            return (
                getattr(settings, "agent_current_chat_id", None)
                or getattr(settings, "last_chat_id", None)
                if settings
                else None
            )

    @staticmethod
    def set_current_chat_id(chat_id: Optional[int]) -> None:
        with get_session() as session:
            settings = session.query(Settings).first()
            if settings:
                settings.last_chat_id = chat_id
                settings.agent_current_chat_id = chat_id
                session.commit()


# Backward-compat: ``from distr.core.chat import ChatManager`` continues to work.
# Lazy import via __getattr__ to avoid circular imports with chat_manager.py.
def __getattr__(name):
    if name == "ChatManager":
        from distr.core.chat_manager import ChatManagerCore

        return ChatManagerCore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
