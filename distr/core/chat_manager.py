"""
ChatManagerCore - Pure Python chat management (no Qt dependency).

This is the single source of truth for chat state management, usable in both
the main GUI process (via ChatManagerQt adapter) and the agent subprocess.
"""

import os
import json
import getpass
import logging
import threading
from datetime import datetime, timezone
from collections import OrderedDict
from typing import List, Optional, Callable, Any

from sqlalchemy import text

from distr.core.db import get_session, Chat, Settings
from distr.core.agent.constants import DEFAULT_MODELS
from distr.core.chat import (
    ChatService,
    _normalize_provider,
    record_chat_audit_event,
    remove_chat_transcript_audit_events,
)

logger = logging.getLogger(__name__)


def _load_chat_params(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _set_active_turn_chat_row_id(root: Chat, row_id: int) -> None:
    """Mark which chat row the agent is currently answering."""
    params = _load_chat_params(root.params)
    params["active_turn_chat_row_id"] = int(row_id)
    root.params = json.dumps(params)


def _clear_active_turn_chat_row_id(root: Chat) -> None:
    params = _load_chat_params(root.params)
    params.pop("active_turn_chat_row_id", None)
    root.params = json.dumps(params)


def _thread_root_id(session, chat_id: int) -> int:
    root_row = session.execute(
        text("""
            WITH RECURSIVE parents(id, parent_id) AS (
                SELECT id, parent_id FROM chats WHERE id = :start_id
                UNION ALL
                SELECT c.id, c.parent_id FROM chats c JOIN parents p ON c.id = p.parent_id
            )
            SELECT id FROM parents WHERE parent_id IS NULL
        """),
        {"start_id": chat_id},
    ).fetchone()
    return int(root_row[0]) if root_row else int(chat_id)


class RAGInterface:
    """Interface for RAG context operations. Override with real implementation."""

    def attach_context(self, chat_id: int, file_paths: List[str]) -> None:
        pass

    def get_relevant_docs(self, chat_id: int, query: str, top_k: int = 5) -> List[dict]:
        return []

    def cleanup_chat_index(self, chat_id: int) -> None:
        pass


class LRUDict(OrderedDict):
    """A dictionary with a maximum size that evicts least-recently-used items."""

    def __init__(self, maxsize: int = 50):
        super().__init__()
        self._maxsize = maxsize

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self._maxsize:
            self.popitem(last=False)

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value


class ChatManagerCore:
    """Pure Python chat manager - no Qt/QObject dependency.

    Provides a simple event system via ``on(event, callback)`` / ``emit(event, *args)``.
    Events: chat_created, chat_updated, chat_deleted, chat_cleared, current_chat_changed
    """

    # ---- event system ----

    def __init__(self, model_name: str = DEFAULT_MODELS["ollama"]):
        self._listeners: dict[str, list[Callable]] = {}
        self.current_model = model_name
        self.current_provider = "Ollama"
        self.current_voice_provider: Optional[str] = None
        self.current_voice_model: Optional[str] = None
        self.chat_histories: LRUDict = LRUDict(
            maxsize=50
        )  # LRU cache with max 50 chats
        self._current_chat_id: Optional[int] = None
        self._updating_chat = False
        self.rag: Optional[RAGInterface] = None

        # Load agent prompt (persona + system template)
        self.agent_prompt = self._load_agent_prompt()

        # Load last chat ID from settings
        with get_session() as session:
            settings = session.query(Settings).first()
            if settings:
                model = getattr(settings, "conversational_llm_model", None) or getattr(
                    settings, "agent_model", None
                )
                if model:
                    self.current_model = model
                    logger.info(
                        "ChatManagerCore: Loaded model from settings: %s",
                        self.current_model,
                    )

                if settings.last_chat_id:
                    chat = session.get(Chat, settings.last_chat_id)
                    if chat:
                        self._current_chat_id = chat.id
                        logger.info(
                            "ChatManagerCore: Loading last chat ID from settings: %s",
                            chat.id,
                        )
                    else:
                        logger.info(
                            "ChatManagerCore: Last saved chat not found in database"
                        )
                else:
                    logger.info("ChatManagerCore: No last chat ID found in settings")

    # ---- event helpers ----

    def on(self, event: str, callback: Callable) -> None:
        self._listeners.setdefault(event, []).append(callback)

    def off(self, event: str, callback: Callable) -> None:
        cbs = self._listeners.get(event, [])
        if callback in cbs:
            cbs.remove(callback)

    def emit(self, event: str, *args: Any) -> None:
        for cb in self._listeners.get(event, []):
            try:
                cb(*args)
            except Exception as e:
                logger.warning("ChatManagerCore: Error in %s listener: %s", event, e)

    # ---- agent prompt loading ----

    def _load_agent_prompt(self) -> str:
        """Build the system prompt from the template + agent persona."""
        from distr.core.agent.services.llm.utils import (
            load_system_prompt_template,
        )
        from distr.core.agent.constants import DEFAULT_PERSONA

        agent_name = "Heart"

        try:
            with get_session() as session:
                settings = session.query(Settings).first()
                if settings:
                    tts_provider = settings.tts_provider or ""
                    if tts_provider == "Kokoro (Offline)":
                        from distr.core.agent.session import KOKORO_VOICES
                        kokoro_voice = (
                            getattr(settings, "kokoro_voice", "af_heart") or "af_heart"
                        )
                        agent_name = KOKORO_VOICES.get(kokoro_voice, "Heart")
                    elif tts_provider == "ElevenLabs (Online)":
                        agent_name = (
                            getattr(settings, "elevenlabs_voice", None)
                            or "Heart"
                        )
                    elif tts_provider == "OpenAI (Online)":
                        openai_voice = (
                            getattr(settings, "openai_voice", "alloy") or "alloy"
                        )
                        agent_name = openai_voice.capitalize()
                    elif tts_provider == "Coqui TTS (Offline)":
                        coqui_voice = (
                            getattr(settings, "coqui_voice", "p225") or "p225"
                        )
                        try:
                            from distr.core.agent.constants import COQUI_VOICES
                            agent_name = COQUI_VOICES.get(coqui_voice, coqui_voice)
                        except Exception:
                            agent_name = coqui_voice
                    elif tts_provider == "F5-TTS (Offline)":
                        f5tts_voice = (
                            getattr(settings, "f5tts_voice", "default") or "default"
                        )
                        agent_name = f5tts_voice.capitalize() if f5tts_voice != "default" else "F5-TTS"
                    elif tts_provider == "VoxCPM (Offline)":
                        voxcpm_voice = (
                            getattr(settings, "voxcpm_voice", "default") or "default"
                        )
                        agent_name = voxcpm_voice.capitalize() if voxcpm_voice != "default" else "VoxCPM"
                    elif tts_provider == "Supertonic (Offline)":
                        supertonic_voice = (
                            getattr(settings, "supertonic_voice", "M1") or "M1"
                        )
                        try:
                            from distr.core.agent.services.tts.supertonic_descriptor import SUPERTONIC_VOICES
                            agent_name = SUPERTONIC_VOICES.get(supertonic_voice, supertonic_voice)
                        except Exception:
                            agent_name = supertonic_voice
                    elif tts_provider == "Chatterbox (Offline)":
                        chatterbox_voice = (
                            getattr(settings, "chatterbox_voice", "default") or "default"
                        )
                        if str(chatterbox_voice).startswith("custom_"):
                            try:
                                from distr.core.agent.services.tts.chatterbox_descriptor import ChatterboxDescriptor
                                agent_name = ChatterboxDescriptor._resolve_custom_voice_name(chatterbox_voice) or "Chatterbox"
                            except Exception:
                                agent_name = "Chatterbox"
                        else:
                            agent_name = "Chatterbox"
        except Exception as e:
            logger.warning(
                "ChatManagerCore: Could not determine agent from settings: %s", e
            )

        persona = DEFAULT_PERSONA

        try:
            username = getpass.getuser()
        except Exception:
            username = "user"

        try:
            system_template = load_system_prompt_template()
            from distr.core.settings import get_system_folder_paths

            folder_paths = get_system_folder_paths()
            home_path = os.path.expanduser("~")
            dropped_files_context = "No files have been dropped on the oracle ball yet."

            try:
                storage_dir = os.path.join(home_path, ".decisions", "dropped_files")
                storage_file = os.path.join(storage_dir, "current_files.json")
                if os.path.exists(storage_file):
                    with open(storage_file, "r") as f:
                        data = json.load(f)
                        files = data.get("files", [])
                        existing_files = [fp for fp in files if os.path.exists(fp)]
                        if existing_files:
                            file_timestamps = data.get("file_timestamps", {})

                            def _fmt_ts(ts):
                                if not ts:
                                    return "unknown time"
                                return datetime.fromtimestamp(ts).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )

                            from distr.core.files.metadata import get_file_metadata

                            ctx = [
                                f"Recently dropped files/folders ({len(existing_files)} total, most recent is last):"
                            ]
                            show = (
                                existing_files[-3:]
                                if len(existing_files) > 3
                                else existing_files
                            )
                            start_idx = len(existing_files) - len(show) + 1
                            for i, fp in enumerate(show, start_idx):
                                ts = file_timestamps.get(fp)
                                ts_str = _fmt_ts(ts) if ts else "unknown time"
                                label = (
                                    "MOST RECENT" if i == len(existing_files) else ""
                                )
                                if os.path.isfile(fp):
                                    md = get_file_metadata(fp)
                                    parts = [f"Size: {md['size_human']}"]
                                    if md["type_description"] != "unknown":
                                        parts.append(f"Type: {md['type_description']}")
                                    if md["default_app"]:
                                        parts.append(f"Opens with: {md['default_app']}")
                                    if md["image_info"]:
                                        img = md["image_info"]
                                        parts.append(
                                            f"Image: {img['width']}x{img['height']}px ({img['format']}, {img['mode']})"
                                        )
                                    if md["audio_info"]:
                                        aud = md["audio_info"]
                                        parts.append(
                                            f"Audio: {aud['duration_formatted']}, {aud['sample_rate']}Hz, {aud['channels']}ch"
                                        )
                                    info = " | ".join(parts)
                                    suffix = f" \u2190 {label}" if label else ""
                                    ctx.append(
                                        f"  {i}. File: {os.path.basename(fp)}{suffix}"
                                    )
                                    ctx.append(f"      {info} (dropped: {ts_str})")
                                elif os.path.isdir(fp):
                                    suffix = f" \u2190 {label}" if label else ""
                                    ctx.append(
                                        f"  {i}. Folder: {os.path.basename(fp)}{suffix} (dropped: {ts_str})"
                                    )
                            if len(existing_files) > 3:
                                ctx.append(
                                    f"  ... and {len(existing_files) - 3} more (older files)"
                                )
                            ctx.append(
                                "\nThese files/folders are available for file operations and can be accessed via file_operations or execute_code tools."
                            )
                            latest = existing_files[-1]
                            if os.path.isfile(latest):
                                lmd = get_file_metadata(latest)
                                lts = file_timestamps.get(latest)
                                lts_str = _fmt_ts(lts) if lts else "unknown time"
                                linfo = (
                                    f"{lmd['size_human']}, {lmd['type_description']}"
                                )
                                if lmd["default_app"]:
                                    linfo += f", opens with {lmd['default_app']}"
                                ctx.append(
                                    f"Latest file: {latest} (position #{len(existing_files)}, {linfo}, dropped at {lts_str})"
                                )
                            dropped_files_context = "\n".join(ctx)
            except Exception:
                pass

            system_prompt = system_template.format(
                agent_name=agent_name,
                username=username,
                model_name=self.current_model,
                tools_description="",
                desktop_path=folder_paths.get(
                    "Desktop", os.path.join(home_path, "Desktop")
                ),
                documents_path=folder_paths.get(
                    "Documents", os.path.join(home_path, "Documents")
                ),
                downloads_path=folder_paths.get(
                    "Downloads", os.path.join(home_path, "Downloads")
                ),
                pictures_path=folder_paths.get(
                    "Pictures", os.path.join(home_path, "Pictures")
                ),
                music_path=folder_paths.get("Music", os.path.join(home_path, "Music")),
                videos_path=folder_paths.get(
                    "Videos", os.path.join(home_path, "Videos")
                ),
                home_path=home_path,
                dropped_files_context=dropped_files_context,
            )
        except Exception as e:
            logger.warning(
                "ChatManagerCore: Could not load system prompt template: %s", e
            )
            system_prompt = (
                f"You are {agent_name}, a helpful VOICE assistant that can answer questions through conversation.\n\n"
                f"You are speaking with {username} (the logged-in user on this system).\n\n"
                f"IMPORTANT: You are a VOICE assistant. You speak to {username}, and {username} speaks to you."
            )

        return f"{persona}\n\n{system_prompt}"

    # ---- chat state ----

    def set_current_chat(self, chat_id: int, force_reload: bool = False) -> None:
        if self._updating_chat:
            return

        old = self._current_chat_id
        changed = chat_id != old
        if not force_reload and not changed:
            return

        try:
            self._updating_chat = True
            self._current_chat_id = chat_id

            if chat_id in self.chat_histories:
                del self.chat_histories[chat_id]

            if chat_id:
                self.get_chat_history(chat_id)

            with get_session() as session:
                chat = session.get(Chat, chat_id)
                if chat:
                    if chat.model_name:
                        self.current_model = chat.model_name
                    if chat.provider:
                        self.current_provider = _normalize_provider(chat.provider)
                    vp = (getattr(chat, "voice_provider", None) or "").strip() or None
                    vm = (getattr(chat, "voice_model", None) or "").strip() or None
                    if vp:
                        from distr.core.agent.constants import normalize_voice_provider
                        self.current_voice_provider = normalize_voice_provider(vp)
                    if vm:
                        self.current_voice_model = vm
                settings = session.query(Settings).first()
                if settings:
                    settings.last_chat_id = chat_id
                    session.commit()

            if changed:
                self.emit("current_chat_changed", chat_id)
                # Signal agent to update its context (model, provider, voice) to match the current chat
                self.emit("agent_context_updated", chat_id)
        finally:
            self._updating_chat = False

    def get_current_chat(self) -> Optional[int]:
        return self._current_chat_id

    # ---- chat CRUD ----

    def create_chat(
        self, title, input_text="", is_new=False, provider=None, model_name=None
    ) -> int:
        session = get_session()
        try:
            settings = session.query(Settings).first()
            if not settings:
                provider = provider or "Ollama"
                model_name = model_name or self.current_model
                voice_provider = None
                voice_model = None
            else:
                provider = (
                    provider
                    or (
                        getattr(settings, "llm_provider", None)
                        or getattr(settings, "conversational_llm_provider", None)
                        or getattr(settings, "agent_provider", None)
                    )
                    or "Ollama"
                )
                model_name = model_name or (
                    getattr(settings, "llm_model", None)
                    or getattr(settings, "conversational_llm_model", None)
                    or getattr(settings, "agent_model", None)
                    or self.current_model
                )
                voice_provider = (
                    getattr(settings, "tts_provider", None)
                    or getattr(settings, "voice_provider", None)
                    or ""
                ).strip() or None
                from distr.core.chat import resolve_voice_model_from_global_settings

                voice_model = (
                    resolve_voice_model_from_global_settings(voice_provider, settings)
                    or ""
                ).strip() or None
        finally:
            session.close()

        final_model = model_name or self.current_model
        logger.info(
            "ChatManagerCore: Creating new chat '%s'. Provider: %s, Model: %s",
            title,
            provider,
            final_model,
        )

        if self.rag:
            try:
                self.rag.attach_context(0, [])  # placeholder; real ID after creation
            except Exception as e:
                logger.warning("ChatManagerCore: RAG attach_context failed: %s", e)
        else:
            try:
                from distr.core.agent.services.rag.integration import (
                    initialize_global_index,
                )

                threading.Thread(
                    target=lambda: initialize_global_index(model_name=final_model),
                    daemon=True,
                ).start()
            except Exception as e:
                logger.warning("Could not initialize global index: %s", e)

        chat_id, _ = ChatService.create_new_chat(
            llm_provider=provider,
            llm_model=final_model,
            tts_provider=voice_provider,
            tts_voice=voice_model,
            title=title or "New Chat",
            starting_question=(input_text or "").strip() or None,
        )

        self.emit("chat_created", chat_id)
        self.set_current_chat(chat_id)
        return chat_id

    def delete_chat(self, chat_id: int) -> None:
        session = get_session()
        try:
            chat = session.query(Chat).filter(Chat.id == chat_id).one()
            session.delete(chat)
            session.commit()
            remove_chat_transcript_audit_events(chat_id)

            if chat_id in self.chat_histories:
                del self.chat_histories[chat_id]

            if self.rag:
                try:
                    self.rag.cleanup_chat_index(chat_id)
                except Exception as e:
                    logger.warning("RAG cleanup failed for chat %s: %s", chat_id, e)
            else:
                try:
                    from distr.core.agent.services.rag.integration import (
                        cleanup_chat_index,
                    )

                    cleanup_chat_index(chat_id)
                except Exception as e:
                    logger.warning(
                        "Failed to cleanup RAG index for chat %s: %s", chat_id, e
                    )

            if self._current_chat_id == chat_id:
                self._current_chat_id = None
                settings = session.query(Settings).first()
                if settings:
                    settings.last_chat_id = None
                    session.commit()

            self.emit("chat_deleted", chat_id)
        except Exception:
            logger.info("Chat with id %s not found.", chat_id)
        finally:
            session.close()

    def get_chat_history(self, chat_id: int) -> list:
        if chat_id in self.chat_histories:
            return self.chat_histories[chat_id]
        thread_messages = ChatService.get_chat_history(chat_id)
        messages = [{"role": "system", "content": self.agent_prompt}] + thread_messages
        self.chat_histories[chat_id] = messages
        return messages

    def clear_chat_history(self, chat_id: int) -> None:
        if chat_id in self.chat_histories:
            del self.chat_histories[chat_id]

    def clear_chat_messages(self, chat_id: int) -> bool:
        session = get_session()
        try:
            chat = session.get(Chat, chat_id)
            if not chat:
                return False
            root = chat
            while root.parent:
                root = root.parent
            root.title = "New Conversation"
            root.input = ""
            root.response = ""
            root.is_new = True
            root.modified_date = datetime.now(timezone.utc)
            session.query(Chat).filter(Chat.parent_id == root.id).delete()
            session.commit()
            remove_chat_transcript_audit_events(root.id)
            if root.id in self.chat_histories:
                del self.chat_histories[root.id]
            if chat_id in self.chat_histories:
                del self.chat_histories[chat_id]
            self.emit("chat_cleared", root.id)
            self.emit("chat_updated", root.id)
            return True
        except Exception as e:
            logger.error("Error clearing chat messages: %s", e)
            return False
        finally:
            session.close()

    # ---- messages ----

    def add_user_message(
        self, chat_id: int, message: str, *, source_platform: Optional[str] = None
    ) -> None:
        cleaned_text = self.clean_text(message)
        session = get_session()
        chat = None
        try:
            chat = session.get(Chat, chat_id)
        finally:
            session.close()

        if not chat:
            new_chat_id = self.create_chat(
                title=cleaned_text.split("\n")[0][:50], input_text=cleaned_text
            )
            new_title = self.generate_title(cleaned_text, chat_id=new_chat_id)
            self.update_chat_title(new_chat_id, new_title)
            return

        session = get_session()
        try:
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

            # Dedup check
            tail_query = text("""
                WITH RECURSIVE chat_tree(id, parent_id, input, response, created_date) AS (
                    SELECT id, parent_id, input, response, created_date FROM chats WHERE id = :root_id
                    UNION ALL
                    SELECT c.id, c.parent_id, c.input, c.response, c.created_date
                    FROM chats c JOIN chat_tree ct ON c.parent_id = ct.id
                )
                SELECT id, input, response FROM chat_tree ORDER BY created_date DESC LIMIT 1
            """)
            tail_row = session.execute(tail_query, {"root_id": root_id}).fetchone()
            if tail_row:
                _, tail_input, tail_response = tail_row
                if (tail_input or "").strip() == cleaned_text and not (
                    tail_response or ""
                ).strip():
                    logger.info("ChatManagerCore: skipping duplicate user message")
                    return

            if root_id not in self.chat_histories:
                self.get_chat_history(root_id)
            entry: dict[str, Any] = {"role": "user", "content": cleaned_text}
            if source_platform:
                entry["source_platform"] = source_platform
            self.chat_histories[root_id].append(entry)

            has_children = (
                session.query(Chat.id)
                .filter(Chat.parent_id == root_id)
                .limit(1)
                .scalar()
                is not None
            )
            if not root.input and not root.parent_id and not has_children:
                root.input = cleaned_text
                root.modified_date = datetime.now(timezone.utc)
                if source_platform:
                    try:
                        p = json.loads(root.params or "{}")
                        if not isinstance(p, dict):
                            p = {}
                        p["source_platform"] = source_platform
                        root.params = json.dumps(p)
                    except (json.JSONDecodeError, TypeError):
                        root.params = json.dumps({"source_platform": source_platform})
                _set_active_turn_chat_row_id(root, int(root.id))
                session.commit()
                record_chat_audit_event(
                    chat_id=int(root_id),
                    chat_row_id=int(root.id) if root.id is not None else None,
                    role="user",
                    content=cleaned_text,
                    source_platform=source_platform,
                )
            else:
                params_obj: dict[str, Any] = {}
                if source_platform:
                    params_obj["source_platform"] = source_platform
                new_chat = Chat(
                    parent_id=root_id,
                    title=cleaned_text.split("\n")[0][:50],
                    input=cleaned_text,
                    response="",
                    params=json.dumps(params_obj),
                    created_date=datetime.now(timezone.utc),
                    modified_date=datetime.now(timezone.utc),
                )
                session.add(new_chat)
                session.commit()
                session.refresh(new_chat)
                _set_active_turn_chat_row_id(root, int(new_chat.id))
                session.commit()
                record_chat_audit_event(
                    chat_id=int(root_id),
                    chat_row_id=int(new_chat.id) if new_chat.id is not None else None,
                    role="user",
                    content=cleaned_text,
                    source_platform=source_platform,
                )

            self.emit("chat_updated", root_id)

            if chat and chat.is_new:
                new_title = self.generate_title(cleaned_text, chat_id=root_id)
                chat.title = new_title
                chat.is_new = False
                session.commit()
                self.emit("chat_updated", root_id)
        except Exception as e:
            logger.error("Error adding user message: %s", e)
        finally:
            session.close()

    def add_assistant_message(
        self, chat_id: int, text_content: str, is_hidden: bool = False
    ) -> None:
        if chat_id not in self.chat_histories:
            self.get_chat_history(chat_id)
        if (
            self.chat_histories[chat_id]
            and self.chat_histories[chat_id][-1]["role"] == "assistant"
        ):
            self.chat_histories[chat_id][-1]["content"] = text_content
        else:
            self.chat_histories[chat_id].append(
                {"role": "assistant", "content": text_content}
            )

        session = get_session()
        try:
            chat = session.get(Chat, chat_id)
            target = chat
            if chat and chat.children:
                target = max(chat.children, key=lambda x: x.created_date)
            if target:
                target.response = text_content
                target.is_hidden = is_hidden
                target.modified_date = datetime.now(timezone.utc)
                if chat:
                    root_id = _thread_root_id(session, int(chat_id))
                    root = session.get(Chat, root_id)
                    if root:
                        _clear_active_turn_chat_row_id(root)
                session.commit()
                record_chat_audit_event(
                    chat_id=int(chat_id),
                    chat_row_id=int(target.id) if target.id is not None else None,
                    role="assistant",
                    content=text_content,
                    hidden=is_hidden,
                )
            if not is_hidden:
                self.emit("chat_updated", chat_id)
                try:
                    from distr.core.chat_title_auto import schedule_chat_title_refresh

                    schedule_chat_title_refresh(chat_id, emit_update=True)
                except Exception:
                    logger.debug("Could not schedule chat title refresh", exc_info=True)
        except Exception as e:
            logger.error("Error adding assistant message: %s", e)
        finally:
            session.close()

    # ---- model/provider ----

    def update_model(self, model_name: str) -> None:
        self.current_model = model_name
        logger.info("ChatManagerCore: Model updated to %s", model_name)
        session = get_session()
        try:
            settings = session.query(Settings).first()
            if settings:
                settings.llm_model = model_name
                session.commit()
        except Exception as e:
            logger.error("ChatManagerCore: Error saving model: %s", e)
        finally:
            session.close()

    def update_provider(self, provider: str) -> None:
        self.current_provider = provider
        logger.info("ChatManagerCore: Provider updated to %s", provider)
        session = get_session()
        try:
            settings = session.query(Settings).first()
            if settings:
                settings.llm_provider = provider
                session.commit()
        except Exception as e:
            logger.error("ChatManagerCore: Error saving provider: %s", e)
        finally:
            session.close()

    # ---- title generation ----

    def generate_title(
        self,
        text_content: str,
        chat_id: int = None,
        provider: str = None,
        model_name: str = None,
    ) -> str:
        try:
            if chat_id:
                session = get_session()
                try:
                    chat = session.get(Chat, chat_id)
                    if chat:
                        provider = provider or getattr(chat, "provider", None)
                        model_name = model_name or getattr(chat, "model_name", None)
                finally:
                    session.close()
            if not provider or not model_name:
                session = get_session()
                try:
                    settings = session.query(Settings).first()
                    if not provider:
                        provider = (
                            getattr(settings, "conversational_llm_provider", None)
                            or getattr(settings, "agent_provider", None)
                            if settings
                            else None
                        ) or "Ollama"
                    if not model_name:
                        model_name = (
                            getattr(settings, "conversational_llm_model", None)
                            or getattr(settings, "agent_model", None)
                            if settings
                            else None
                        ) or self.current_model
                finally:
                    session.close()

            model_lower = (model_name or "").lower()
            if any(x in model_lower for x in ["embed", "embedding", "nomic-embed"]):
                return (
                    text_content[:40] + "..."
                    if len(text_content) > 40
                    else text_content
                )

            if provider in ("OpenAI", "Anthropic", "OpenRouter", "Groq", "KiloCode", "Google Gemini"):
                return (
                    text_content[:40] + "..."
                    if len(text_content) > 40
                    else text_content
                )

            prompt = f'Create a 2-5 word title for this message. Reply with ONLY the title, nothing else.\n\nMessage: "{text_content}"\n\nTitle:'
            try:
                from ollama import Client

                response = Client().chat(
                    model=model_name, messages=[{"role": "user", "content": prompt}]
                )
                title = response["message"]["content"].strip()
            except Exception as e:
                logger.warning(
                    "Error generating title with Ollama (model: %s): %s", model_name, e
                )
                return (
                    text_content[:40] + "..."
                    if len(text_content) > 40
                    else text_content
                )

            if title.startswith('"') and title.endswith('"'):
                title = title[1:-1]
            if title.startswith("'") and title.endswith("'"):
                title = title[1:-1]
            title = (
                title.replace("**", "")
                .replace("*", "")
                .replace("#", "")
                .replace("`", "")
            )
            for prefix in [
                "Title:",
                "title:",
                "Here's a title:",
                "Short title:",
                "Chat title:",
            ]:
                if title.lower().startswith(prefix.lower()):
                    title = title[len(prefix) :].strip()
            title = title.split("\n")[0].strip()
            if len(title) > 60 or "[" in title or title.count(" ") > 10:
                title = (
                    text_content[:40].strip() + "..."
                    if len(text_content) > 40
                    else text_content
                )
            return title.strip() if title.strip() else text_content[:40] + "..."
        except Exception as e:
            logger.error("Error generating title: %s", e)
            return text_content[:40] + "..." if len(text_content) > 40 else text_content

    def update_chat_title(self, chat_id: int, title: str) -> None:
        session = get_session()
        try:
            chat = session.query(Chat).get(chat_id)
            if chat:
                chat.title = title
                session.commit()
                self.emit("chat_updated", chat_id)
        except Exception as e:
            logger.error("Error updating chat title: %s", e)
        finally:
            session.close()

    # ---- utilities ----

    @staticmethod
    def clean_text(text_content: str) -> str:
        paragraphs = text_content.split("\n")
        cleaned = []
        current = []
        for line in paragraphs:
            line = line.strip()
            if line:
                current.append(line)
            elif current:
                cleaned.append(" ".join(current))
                current = []
        if current:
            cleaned.append(" ".join(current))
        return "\n\n".join(cleaned)
