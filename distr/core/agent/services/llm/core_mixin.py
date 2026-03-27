"""
Shared LLM Service Mixin

Core mixin providing shared utility methods for all LLM services:
- Tool routing (semantic router integration)
- System prompt building and caching
- Chat lifecycle (on_chat_changed, on_chat_deleted, on_chat_cleared)
- process_frame (unified TranscriptionFrame handling)
- process_chat_input (unified text input from web/voice/telegram)
- send_welcome_message (with conversation summaries)
- Setters (listening, hands-free, speaker, agent name, TTS service)

Domain-specific behaviour is composed from separate mixins:
- VoiceDictationMixin  (voice_mixin.py)  — voice commands, dictation
- FastActionMixin      (fast_action_mixin.py) — fast action execution, read-this, clipboard
- TelegramMixin        (telegram_mixin.py) — telegram flag propagation, response emission
"""

import asyncio
import json
import logging
import os
import platform
import time
from typing import Optional

from distr.core.signals import signal_manager
from distr.core.agent.services.llm.text_utils import clean_text_for_tts
from .mixins.voice import VoiceDictationMixin
from .mixins.fast_actions import FastActionMixin
from .mixins.telegram import TelegramMixin

logger = logging.getLogger(__name__)


class LLMSharedMixin(VoiceDictationMixin, FastActionMixin, TelegramMixin):
    MAX_DROPPED_ITEMS_IN_PROMPT = 30
    """
    Mixin providing shared utility methods for LLM services.

    Expects the following attributes on self:
    - event_queue
    - chat_manager
    - _model_name
    - _is_dictating, _is_hands_free, _is_listening
    - _hands_free_before_dictation
    - set_hands_free(enabled)
    """

    def _get_provider_name(self) -> str:
        """Return the provider name for chat persistence. Inferred from class name."""
        return self.__class__.__name__.replace("LLMService", "")

    def _build_tool_router_async(self):
        """Build the semantic tool router index in a background thread."""
        import threading

        tools = getattr(self, '_tools', None)
        if not tools:
            return

        def _build():
            try:
                from distr.core.agent.services.llm.tool_router import get_tool_router
                router = get_tool_router()
                router.build_index(tools)
            except Exception as e:
                logger.debug("ToolRouter build skipped: %s", e)

        threading.Thread(target=_build, daemon=True, name="tool-router-build").start()

    def _get_filtered_tools(self, last_user_message: str = None):
        """Return tools filtered by semantic router + request type detection.

        This is the single entry point for tool filtering across ALL providers.
        """
        if not last_user_message:
            return self._tools
        try:
            from distr.core.agent.services.llm.tool_routing import filter_tools_by_context
            return filter_tools_by_context(last_user_message, self._tools, self._messages)
        except Exception as e:
            logger.debug("_get_filtered_tools fallback to all tools: %s", e)
            return self._tools

    def _get_username(self) -> str:
        """Extract OS username (first name if available)."""
        import getpass
        try:
            system = platform.system()
            if system == "Windows":
                return os.getenv('USERNAME') or os.getenv('USER') or getpass.getuser()
            else:
                os_username = getpass.getuser()
                try:
                    import pwd
                    user_info = pwd.getpwnam(os_username)
                    full_name = user_info.pw_gecos.split(',')[0] if user_info.pw_gecos else os_username
                    if full_name and full_name != os_username:
                        return full_name.split()[0] if full_name.split() else os_username
                    return os_username
                except (ImportError, KeyError, AttributeError):
                    return os_username
        except Exception as e:
            logger.warning("Could not determine username: %s", e)
            return "User"

    def _ensure_user_message_persisted(self, text: str) -> Optional[int]:
        """Ensure the user message is persisted to the current chat.

        NOTE: Does NOT auto-create chats. The web route should create chats
        explicitly before sending messages.
        """
        if not getattr(self, "chat_manager", None) or not (text or "").strip():
            return None
        current_chat_id = self.chat_manager.get_current_chat()
        if not current_chat_id:
            logger.debug("_ensure_user_message_persisted: No current chat, not auto-creating")
            return None
        self.chat_manager.add_user_message(current_chat_id, text.strip())
        try:
            signal_manager.chat_message_added.emit(int(current_chat_id), "user", text.strip())
        except Exception:
            pass
        return current_chat_id

    # ------------------------------------------------------------------ #
    #  File/Folder context utilities                                      #
    # ------------------------------------------------------------------ #

    def _get_folder_structure_info(self, folder_path: str, max_depth: int = 2) -> str:
        """Get folder structure information for context."""
        try:
            if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
                return "Folder not found"

            file_count = 0
            file_types = {}
            subdir_count = 0
            top_level_items = []

            for root, dirs, files in os.walk(folder_path):
                level = root.replace(folder_path, '').count(os.sep)

                if level > max_depth:
                    dirs[:] = []
                    continue

                dirs[:] = [d for d in dirs if not d.startswith('.')]
                visible_files = [f for f in files if not f.startswith('.')]
                file_count += len(visible_files)

                for f in visible_files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext:
                        file_types[ext] = file_types.get(ext, 0) + 1
                    else:
                        file_types['(no extension)'] = file_types.get('(no extension)', 0) + 1

                if level == 0:
                    subdir_count = len(dirs)
                    top_level_items = [d + '/' for d in dirs[:5]] + [f for f in visible_files[:5]]
                    if len(dirs) > 5 or len(visible_files) > 5:
                        top_level_items.append("...")

            parts = [f"{file_count} file(s)"]
            if file_types:
                type_summary = ", ".join(
                    f"{count} {ext}" for ext, count in
                    sorted(file_types.items(), key=lambda x: x[1], reverse=True)[:5]
                )
                parts.append(f"({type_summary})")
            if subdir_count > 0:
                parts.append(f"in {subdir_count} subdirector{'y' if subdir_count == 1 else 'ies'}")

            structure_info = " ".join(parts)
            if top_level_items:
                structure_info += f"\n    Top-level: {', '.join(top_level_items[:8])}"
            return structure_info
        except Exception as e:
            logger.debug("Error getting folder structure for %s: %s", folder_path, e)
            return "Unable to read folder structure"

    def _get_dropped_files_context(self, chat_id=None) -> str:
        """Get context about recently dropped files for the system prompt."""
        storage_dir = os.path.join(os.path.expanduser("~"), ".decisionsai", "dropped_files")
        storage_file = os.path.join(storage_dir, "current_files.json")

        logger.debug("_get_dropped_files_context: Checking storage_file=%s, chat_id=%s", storage_file, chat_id)

        if not os.path.exists(storage_file):
            return "No files have been dropped on the oracle ball yet."

        try:
            with open(storage_file, 'r') as f:
                data = json.load(f)
                files = data.get("files", [])
                dropped_folders = data.get("dropped_folders", [])
                chat_files_index = data.get("chat_files_index", {})

                file_chat_mapping = data.get("file_chat_mapping", {})
                folder_chat_mapping = data.get("folder_chat_mapping", {})
                file_timestamps = data.get("file_timestamps", {})
                folder_timestamps = data.get("folder_timestamps", {})

                if chat_id:
                    chat_bucket = chat_files_index.get(str(chat_id), {})
                    if isinstance(chat_bucket, dict):
                        bucket_files = chat_bucket.get("files", [])
                        bucket_folders = chat_bucket.get("dropped_folders", [])
                        if bucket_files or bucket_folders:
                            files = bucket_files
                            dropped_folders = bucket_folders
                            logger.info(
                                "_get_dropped_files_context: Using chat_files_index for chat_id=%s: %d folders, %d files",
                                chat_id, len(dropped_folders), len(files)
                            )

                    def is_associated_with_chat(item_path, mapping):
                        normalized_path = item_path.rstrip('/').rstrip('\\')
                        chat_ids = (
                            mapping.get(item_path) or
                            mapping.get(normalized_path) or
                            mapping.get(normalized_path + '/') or
                            []
                        )
                        if isinstance(chat_ids, list):
                            return chat_id in chat_ids
                        return chat_ids == chat_id

                    if not chat_bucket:
                        dropped_folders = [f for f in dropped_folders if is_associated_with_chat(f, folder_chat_mapping)]
                        files = [f for f in files if is_associated_with_chat(f, file_chat_mapping)]
                        logger.info("_get_dropped_files_context: Filtered for chat_id=%s: %d folders, %d files",
                                   chat_id, len(dropped_folders), len(files))

                if not files and not dropped_folders:
                    if chat_id:
                        return "No files have been dropped on the oracle ball in this chat yet."
                    return "No files have been dropped on the oracle ball yet."

                # Filter out files inside dropped folders
                individual_files = []
                for f in files:
                    is_inside_folder = False
                    for folder in dropped_folders:
                        try:
                            normalized_file = os.path.normpath(os.path.abspath(f))
                            normalized_folder = os.path.normpath(os.path.abspath(folder))
                            if normalized_file.startswith(normalized_folder + os.sep) or normalized_file == normalized_folder:
                                is_inside_folder = True
                                break
                        except (ValueError, OSError):
                            pass
                    if not is_inside_folder and os.path.exists(f):
                        individual_files.append(f)

                items_to_show = dropped_folders + individual_files
                existing_items = [item for item in items_to_show if os.path.exists(item)]

                if not existing_items:
                    return "Previously dropped files/folders are no longer available."

                # Sort by timestamp (most recent last)
                def get_item_timestamp(item_path):
                    return file_timestamps.get(item_path) or folder_timestamps.get(item_path) or 0

                existing_items.sort(key=get_item_timestamp)

                def format_timestamp(ts):
                    if not ts:
                        return "unknown time"
                    from datetime import datetime
                    dt = datetime.fromtimestamp(ts)
                    return dt.strftime("%Y-%m-%d %H:%M:%S")

                total_items = len(existing_items)
                folder_count = sum(1 for item in existing_items if os.path.isdir(item))
                file_count = sum(1 for item in existing_items if os.path.isfile(item))

                max_items = max(1, int(getattr(self, "MAX_DROPPED_ITEMS_IN_PROMPT", 30)))
                visible_items = existing_items[-max_items:] if total_items > max_items else existing_items

                if folder_count > 0 and file_count > 0:
                    context_parts = [f"Recently dropped items ({folder_count} folder(s), {file_count} file(s), {total_items} total, most recent is LAST):"]
                elif folder_count > 0:
                    context_parts = [f"Recently dropped folders ({folder_count} total, most recent is LAST). Folder contents are indexed and can be queried via RAG."]
                else:
                    context_parts = [f"Recently dropped files ({file_count} total, most recent is LAST):"]

                latest_item = existing_items[-1] if existing_items else None

                from distr.core.files.metadata import get_file_metadata

                visible_start = total_items - len(visible_items) + 1
                for i, item_path in enumerate(visible_items, visible_start):
                    item_ts = file_timestamps.get(item_path) or folder_timestamps.get(item_path)
                    timestamp_str = format_timestamp(item_ts) if item_ts else "unknown time"

                    if os.path.isdir(item_path):
                        folder_name = os.path.basename(item_path) or item_path
                        structure_info = self._get_folder_structure_info(item_path, max_depth=2)
                        marker = " [MOST RECENT]" if i == total_items else ""
                        context_parts.append(f"  [{i}] Folder: {folder_name} (full path: {item_path}){marker} - dropped at {timestamp_str}")
                        context_parts.append(f"      Contains: {structure_info}")
                        context_parts.append(f"      Path resolution: Files inside can be referenced as '{item_path}/filename.ext'")
                        context_parts.append(f"      Indexing: Use IndexFolderTool to index this folder for semantic search when needed")
                    elif os.path.isfile(item_path):
                        metadata = get_file_metadata(item_path)
                        info_parts = [f"Size: {metadata['size_human']}"]
                        if metadata['type_description'] != 'unknown':
                            info_parts.append(f"Type: {metadata['type_description']}")
                        if metadata['default_app']:
                            info_parts.append(f"Opens with: {metadata['default_app']}")
                        if metadata['image_info']:
                            img = metadata['image_info']
                            info_parts.append(f"Image: {img['width']}x{img['height']}px ({img['format']}, {img['mode']})")
                        if metadata['audio_info']:
                            audio = metadata['audio_info']
                            info_parts.append(f"Audio: {audio['duration_formatted']}, {audio['sample_rate']}Hz, {audio['channels']}ch ({audio.get('format', 'unknown')})")
                        file_info = " | ".join(info_parts)
                        marker = " [MOST RECENT]" if i == total_items else ""
                        context_parts.append(f"  [{i}] File: {item_path}{marker} - dropped at {timestamp_str}")
                        context_parts.append(f"      {file_info}")

                hidden_count = total_items - len(visible_items)
                if hidden_count > 0:
                    context_parts.append(f"  ... and {hidden_count} older item(s) not shown")

                context_parts.append("\nThese files/folders are available for file operations.")
                context_parts.append("When user says 'the file in that folder' or 'the file in the folder I dropped', resolve paths relative to the folder path shown above.")
                context_parts.append("Folders can be indexed on-demand using IndexFolderTool for semantic search when the user wants to search or query folder contents.")

                if visible_items and latest_item:
                    try:
                        folders = [item for item in visible_items if os.path.isdir(item)]
                        files_list = [item for item in visible_items if os.path.isfile(item)]
                        context_parts.append(f"\nIMPORTANT: When the user says 'the file I dropped', 'the folder I dropped', 'what I just dropped', 'the files I dropped', or similar phrases:")

                        if len(folders) == 1 and not files_list:
                            context_parts.append(f"  They are referring to the folder: {os.path.basename(folders[0]) or folders[0]} ({folders[0]})")
                        elif len(files_list) == 1 and not folders:
                            context_parts.append(f"  They are referring to the file: {os.path.basename(files_list[0])} ({files_list[0]})")
                        else:
                            context_parts.append(f"  They are referring to {len(visible_items)} item(s) (most recent is last):")
                            for idx, ip in enumerate(visible_items, 1):
                                fname = os.path.basename(ip) or ip
                                m = " [MOST RECENT]" if ip == latest_item else ""
                                kind = "Folder" if os.path.isdir(ip) else "File"
                                context_parts.append(f"    [{idx}] {kind}: {fname} ({ip}){m}")

                        latest_name = os.path.basename(latest_item) or latest_item
                        kind = "Folder" if os.path.isdir(latest_item) else "File"
                        context_parts.append(f"\n  MOST RECENT (when user says 'the last one'): {kind}: {latest_name} ({latest_item})")
                    except Exception as e:
                        logger.warning("Error generating explicit reference section: %s", e)

                return "\n".join(context_parts)
        except Exception as e:
            logger.debug("Error reading dropped files context: %s", e)
            return "Unable to retrieve dropped files information."

    # ------------------------------------------------------------------ #
    #  Interruption cleanup                                               #
    # ------------------------------------------------------------------ #

    def _emit_interruption_cleanup(self):
        """Emit signals to restore chat window UI after interruption."""
        try:
            current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
            if not current_chat_id:
                return
            if self.event_queue:
                self.event_queue.put(('chat_stream_finished', {'chat_id': current_chat_id, 'response_text': ''}), block=False)
                self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
            else:
                signal_manager.chat_stream_finished.emit(current_chat_id)
                signal_manager.typing_indicator_changed.emit(False)
        except Exception as e:
            logger.warning("Error in _emit_interruption_cleanup: %s", e)

    # ------------------------------------------------------------------ #
    #  Conversation summary (LLM call — used by FastActionMixin)          #
    # ------------------------------------------------------------------ #

    async def _generate_conversation_summary(self, conversation_messages: list) -> str:
        """Generate a summary of the conversation using the LLM."""
        if not conversation_messages:
            return "We haven't talked about anything yet. This is the start of our conversation!"

        conversation_text = ""
        for msg in conversation_messages[-10:]:
            role = msg.get('role', 'user')
            content = msg.get('content', '').strip()
            if content:
                prefix = "User" if role == 'user' else "Assistant"
                conversation_text += f"{prefix}: {content}\n"

        if not conversation_text.strip():
            return "We haven't had much conversation yet."

        try:
            import ollama as _ollama
            client = _ollama.AsyncClient(timeout=120.0)
            summary_prompt = (
                "You are summarizing a conversation between YOURSELF (an AI assistant) and a user.\n\n"
                "CRITICAL INSTRUCTIONS:\n"
                "- Summarize what WE (you and the user) were ACTUALLY TALKING ABOUT\n"
                "- Use 'We' to refer to yourself and the user\n"
                "- Focus on TOPICS, STORIES, QUESTIONS, or TASKS discussed\n"
                "- DO NOT mention tools, capabilities, functions, or system features\n"
                "- Keep it conversational and natural (2-3 sentences max)\n\n"
                f"Conversation:\n{conversation_text}\n\n"
                "Provide a brief, natural summary of what we were actually talking about (using 'We'):"
            )
            response = await client.chat(
                model=self._model_name,
                messages=[{"role": "user", "content": summary_prompt}],
                options={"keep_alive": -1},
            )
            summary = response.get('message', {}).get('content', '').strip()
            bad_words = ['tools', 'functions', 'actions', 'capabilities', 'features',
                         'f1-f12', 'function keys', 'oracle/globe', 'chatbot system']
            if summary and not any(w in summary.lower() for w in bad_words):
                return summary
        except Exception as e:
            logger.error("Error generating conversation summary: %s", e, exc_info=True)

        user_messages = [m.get('content', '') for m in conversation_messages if m.get('role') == 'user']
        if user_messages:
            return f"We were talking about {user_messages[-1][:100].lower()}."
        return "We were having a conversation, but I can't provide a summary right now."

    # ------------------------------------------------------------------ #
    #  System prompt building                                             #
    # ------------------------------------------------------------------ #

    def _build_system_prompt_template(self, chat_id=None, include_tools_description=True):
        """Build the formatted system prompt template with folder paths and dropped files.

        Caches the result and skips the expensive rebuild when dropped-files
        context hasn't changed.
        """
        from distr.core.settings import get_system_folder_paths
        from distr.core.agent.services.llm.prompt import build_tools_description

        dropped_files_context = self._get_dropped_files_context(chat_id=chat_id)
        ctx_hash = hash((dropped_files_context, include_tools_description))

        if (hasattr(self, '_cached_template_hash')
                and self._cached_template_hash == ctx_hash
                and hasattr(self, '_cached_template')
                and self._cached_template):
            return self._cached_template

        folder_paths = get_system_folder_paths()
        home_path = os.path.expanduser("~")

        tools_desc = build_tools_description(self._tools) if include_tools_description else ""

        template = self._default_template_raw.format(
            agent_name=self._agent_name,
            username=self._username,
            tools_description=tools_desc,
            model_name=self._model_name,
            desktop_path=folder_paths.get("Desktop", os.path.join(home_path, "Desktop")),
            documents_path=folder_paths.get("Documents", os.path.join(home_path, "Documents")),
            downloads_path=folder_paths.get("Downloads", os.path.join(home_path, "Downloads")),
            pictures_path=folder_paths.get("Pictures", os.path.join(home_path, "Pictures")),
            music_path=folder_paths.get("Music", os.path.join(home_path, "Music")),
            videos_path=folder_paths.get("Videos", os.path.join(home_path, "Videos")),
            home_path=home_path,
            dropped_files_context=dropped_files_context,
        )

        try:
            from distr.core.agent.services.rag.project import get_active_project_context
            project_context = get_active_project_context()
            if project_context:
                template += f"\n\n{project_context}"
        except Exception as e:
            logger.warning("Could not inject project context: %s", e)

        self._cached_template = template
        self._cached_template_hash = ctx_hash
        return template

    def _build_system_message(self, chat_id=None, include_tools_description=True):
        """Build the full system message dict (persona + template)."""
        template = self._build_system_prompt_template(chat_id=chat_id, include_tools_description=include_tools_description)
        self.default_template = template

        persona = getattr(self, '_persona', None)
        content = f"{persona}\n\n{template}" if persona else template
        return {"role": "system", "content": content}

    # ------------------------------------------------------------------ #
    #  One-liner setters                                                  #
    # ------------------------------------------------------------------ #

    def set_listening(self, enabled: bool):
        self._is_listening = enabled

    def set_hands_free(self, enabled: bool):
        self._is_hands_free = enabled

    def set_speaker_enabled(self, enabled: bool):
        self._speaker_enabled = enabled

    def set_agent_name(self, agent_name: str):
        """Update agent name and regenerate the system prompt template."""
        old_name = self._agent_name
        self._agent_name = agent_name

        if not hasattr(self, '_default_template_raw') or not self._default_template_raw:
            logger.warning(f"LLM agent name updated to '{agent_name}' but template not yet initialized")
            return

        self._build_system_prompt_template(
            chat_id=self.chat_manager.get_current_chat() if self.chat_manager else None
        )
        logger.info(f"LLM agent name updated: '{old_name}' -> '{agent_name}'")

    def set_tts_service(self, tts_service):
        """Set TTS service after initialization and reload tools."""
        from distr.core.agent.tools import load_tools
        self._tts_service = tts_service
        try:
            self._tools = load_tools(
                chat_manager=self.chat_manager, use_navigation_tools=True,
                llm_service=self, tts_service=tts_service,
                llm_model=self._model_name, event_queue=self.event_queue,
                command_queue=self.command_queue,
                confirmation_results_dict=self.confirmation_results_dict,
            )
            self._tools_dict = {tool.name: tool for tool in self._tools}
            logger.debug(f"Reloaded {len(self._tools)} tools with TTS service")
        except Exception as e:
            logger.warning(f"Failed to reload tools with TTS service: {e}")

    # ------------------------------------------------------------------ #
    #  _on_files_indexed / _setup_system_prompt                           #
    # ------------------------------------------------------------------ #

    def _on_files_indexed(self, notification_message: str):
        """Handle notification when files are dropped."""
        try:
            current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
            system_msg = self._build_system_message(chat_id=current_chat_id)
            if self._messages and self._messages[0].get('role') == 'system':
                self._messages[0] = system_msg
            else:
                self._messages.insert(0, system_msg)

            self._messages.append({
                "role": "system",
                "content": f"[SYSTEM NOTIFICATION] {notification_message}",
            })
            logger.debug("%s: Updated system message with fresh dropped files context", self._get_provider_name())
        except Exception as e:
            logger.error("Error handling files_indexed notification: %s", e, exc_info=True)

    def _setup_system_prompt(self, system_prompt=None):
        """Initialize the system prompt template and build the first system message."""
        from distr.core.agent.services.llm.prompt import load_system_prompt_template
        self._default_template_raw = load_system_prompt_template()
        self._persona = system_prompt if system_prompt else None

        system_msg = self._build_system_message(
            chat_id=self.chat_manager.get_current_chat() if self.chat_manager else None
        )
        self._messages = [system_msg]

    # ------------------------------------------------------------------ #
    #  Chat lifecycle                                                     #
    # ------------------------------------------------------------------ #

    def on_chat_changed(self, chat_id):
        """Update LLM context when the active chat changes."""
        if not self.chat_manager:
            return

        if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
            self._cancelled = True
            self._generation_task.cancel()
            self._emit_interruption_cleanup()

        self._cancelled = False

        try:
            system_prompt = self._build_system_message(chat_id=chat_id)
        except Exception as e:
            logger.warning("%s: Failed to rebuild system prompt: %s", self._get_provider_name(), e)
            system_prompt = self._messages[0] if self._messages else {"role": "system", "content": getattr(self, 'default_template', '')}

        if not chat_id:
            self._messages = [system_prompt]
            return

        try:
            history = self.chat_manager.get_chat_history(chat_id)
            new_messages = [msg for msg in history if msg.get('role') != 'system']
            validated = self._validate_messages(new_messages) if hasattr(self, '_validate_messages') else new_messages
            self._messages = [system_prompt] + validated if validated else [system_prompt]
            logger.debug("%s: Loaded %d messages for chat %s", self._get_provider_name(), len(validated), chat_id)
        except Exception as e:
            logger.error("Error loading chat history: %s", e, exc_info=True)
            self._messages = [system_prompt]

    def on_chat_deleted(self, chat_id):
        """Clear conversation history when a chat is deleted."""
        current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
        if current_chat_id == chat_id:
            system_prompt = self._messages[0] if self._messages else None
            self._messages = [system_prompt] if system_prompt else []

            new_current = self.chat_manager.get_current_chat() if self.chat_manager else None
            if new_current and new_current != chat_id:
                self.on_chat_changed(new_current)

    def on_chat_cleared(self, chat_id):
        """Reset messages to system prompt only when a chat is cleared."""
        current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
        if current_chat_id == chat_id:
            system_prompt = self._messages[0] if self._messages else {"role": "system", "content": getattr(self, 'default_template', '')}
            self._messages = [system_prompt]

    # ------------------------------------------------------------------ #
    #  process_chat_input                                                 #
    # ------------------------------------------------------------------ #

    async def process_chat_input(self, text: str, is_telegram: bool = False,
                                  uploaded_image_path: str = None, speaker_enabled=None):
        """Process text input from chat window. Unified for all providers."""
        self._cancelled = False
        if hasattr(self, '_generation_requested_at'):
            self._generation_requested_at = time.monotonic()
        await asyncio.sleep(0.05)

        if speaker_enabled is not None:
            self._speaker_enabled = bool(speaker_enabled)

        logger.debug("%s: Processing chat input: '%s...' (is_telegram=%s)",
                     self._get_provider_name(), text[:50], is_telegram)

        self._is_telegram_request = is_telegram
        self._uploaded_image_path = uploaded_image_path

        if is_telegram:
            import threading
            threading.current_thread().telegram_request = True

        # Verify chat provider matches this service
        provider_name = self._get_provider_name()
        if self.chat_manager:
            current_chat_id = self.chat_manager.get_current_chat()
            if current_chat_id:
                try:
                    from distr.core.db import get_session, Chat
                    session = get_session()
                    chat = session.get(Chat, current_chat_id)
                    if chat and chat.provider:
                        p = (chat.provider or "").strip().lower()
                        if p and p != provider_name.lower():
                            logger.error("%s cannot process chat with provider '%s'. Rejecting.",
                                         self._get_provider_name(), chat.provider)
                            session.close()
                            return
                    session.close()
                except Exception as e:
                    logger.warning("Could not verify chat provider: %s", e)

        self._ensure_user_message_persisted(text)

        # Handle vision input
        from distr.core.agent.services.llm.image_utils import (
            get_image_path_from_context, convert_image_to_base64,
            check_vision_model_support, analyze_image_with_vision_llm,
        )

        user_message_content = text
        image_path = get_image_path_from_context(uploaded_image_path)

        if image_path and os.path.exists(image_path):
            logger.debug("📸 Image found: %s", image_path)
            chat_model_vision = check_vision_model_support(self._model_name, provider_name)

            if chat_model_vision:
                try:
                    base64_image, mime_type = convert_image_to_base64(image_path)
                    user_message_content = self._format_vision_message(text, base64_image, mime_type)
                    logger.debug("✅ Image embedded in message (native vision)")
                except Exception as e:
                    logger.error("Failed to embed image: %s", e, exc_info=True)
                    user_message_content = text
            else:
                analysis = await analyze_image_with_vision_llm(image_path, text)
                if analysis:
                    user_message_content = f"[The user uploaded an image. Here's what's in the image: {analysis}]\n\nUser's question: {text}"
                else:
                    user_message_content = text
        elif image_path:
            logger.warning("Image path provided but file doesn't exist: %s", image_path)

        self._messages.append({"role": "user", "content": user_message_content})

        if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
            self._cancelled = True
            self._generation_task.cancel()
            try:
                await self._generation_task
            except asyncio.CancelledError:
                pass
            self._cancelled = False

        self._generation_task = asyncio.create_task(self._generate_response())

    def _format_vision_message(self, text: str, base64_image: str, mime_type: str):
        """Format a vision message. Override in subclass for provider-specific format."""
        return [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
        ]

    # ------------------------------------------------------------------ #
    #  process_frame                                                      #
    # ------------------------------------------------------------------ #

    async def process_frame(self, frame, direction):
        """Process incoming frames — common routing for all providers."""
        from distr.core.agent.libs import (
            StartFrame, CancelFrame, InterruptionFrame, TranscriptionFrame,
            UserStartedSpeakingFrame, TextFrame,
        )
        from distr.core.agent.services.llm.fast_action_detector import detect_fast_action, ActionType
        from distr.core.agent.tools.base import fast_tool_matcher

        self._pipeline_direction = direction

        if isinstance(frame, StartFrame):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, CancelFrame):
            if hasattr(self, '_generation_requested_at') and self._generation_requested_at > 0:
                now = time.monotonic()
                if (now - self._generation_requested_at) < 0.5:
                    logger.debug("LLM: Ignoring stale CancelFrame (%.0fms since generation requested)",
                                 (now - self._generation_requested_at) * 1000)
                    return
            self._cancelled = True
            if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
                self._generation_task.cancel()
            self._emit_interruption_cleanup()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            if self._is_hands_free:
                self._cancelled = True
                if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
                    self._generation_task.cancel()
                self._emit_interruption_cleanup()
                await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            self._cancelled = True
            if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
                self._generation_task.cancel()
            self._emit_interruption_cleanup()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            self._cancelled = False
            text = frame.text.strip()
            if not text:
                logger.info("LLM: Received empty TranscriptionFrame — ignoring")
                return

            logger.info("LLM: Received transcription: '%s'", text[:100])
            text_lower = text.lower().strip()

            # When listening is disabled, only "start listening" can get through
            if not self._is_listening:
                if self._check_start_listening_command(text_lower):
                    return
                return

            # Dictation mode takes priority
            if self._check_dictation_commands(text_lower, text):
                return
            if self._is_dictating:
                text_to_type = self._process_dictation_text(text)
                if text_to_type:
                    await self._type_dictation_text(text_to_type)
                return

            # Voice commands
            if await self._check_and_execute_voice_command(text_lower, direction):
                return

            # Duplicate message detection
            last_user_msg = self._messages[-1] if self._messages else None
            if last_user_msg and last_user_msg.get('role') == 'user' and last_user_msg.get('content') == text:
                logger.warning("Duplicate user message detected: '%s...' — skipping", text[:50])
                return

            # Fast action detection
            has_clipboard_context = any(
                'CLIPBOARD CONTENT:' in msg.get('content', '')
                for msg in self._messages[-5:] if msg.get('role') == 'tool'
            )
            fast_action = detect_fast_action(text, has_clipboard_context)

            can_execute_directly = (
                fast_action.confidence >= 0.9
                and fast_action.action_type != ActionType.CONVERSATIONAL
                and (fast_action.action_type != ActionType.UNKNOWN or fast_action.tool_name)
            )
            if can_execute_directly:
                logger.info("⚡ FAST ACTION: %s (confidence %.2f)", fast_action.action_type.value, fast_action.confidence)
                self._processed_fast_actions.clear()
                current_chat_id = self._ensure_user_message_persisted(text)
                self._messages.append({"role": "user", "content": text})
                if await self._execute_fast_action(fast_action, current_chat_id):
                    return

            # Fast tool matcher fallback
            fast_match = fast_tool_matcher(text, self._tools, self._tools_dict)
            if fast_match:
                tool, args, confidence = fast_match
                logger.debug("Fast tool match: %s (confidence=%.1f%%)", tool.name, confidence * 100)
                current_chat_id = self._ensure_user_message_persisted(text)
                self._messages.append({"role": "user", "content": text})

                # Signal the UI that the agent is working
                if self.event_queue and current_chat_id:
                    self.event_queue.put(('typing_indicator_changed', {'show': True}), block=False)
                    self.event_queue.put(('chat_stream_started', {'chat_id': current_chat_id}), block=False)

                try:
                    if 'text' not in args:
                        args['text'] = text
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, lambda t=tool, a=args: t._run(**a)
                    )

                    if hasattr(tool, '_read_task') and tool._read_task:
                        try:
                            await tool._read_task
                        except Exception as e:
                            logger.error("Error awaiting read task: %s", e)
                        finally:
                            tool._read_task = None

                    from distr.core.agent.tool_audit import record_tool_execution
                    record_tool_execution(current_chat_id, tool.name, str(result), "completed", event_queue=self.event_queue)

                    if self.chat_manager and current_chat_id:
                        self.chat_manager.add_assistant_message(current_chat_id, result)
                    self._messages.append({"role": "assistant", "content": result})

                    if self._tts_service and self._speaker_enabled and result:
                        cleaned = clean_text_for_tts(str(result))
                        if cleaned:
                            await self.push_frame(TextFrame(text=cleaned))
                except Exception as e:
                    logger.error("Error running fast tool %s: %s", tool.name, e, exc_info=True)
                finally:
                    # Signal the UI that the agent is done
                    if self.event_queue and current_chat_id:
                        self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
                        self.event_queue.put(('chat_stream_finished', {'chat_id': current_chat_id, 'response_text': ''}), block=False)
                return

            # Normal LLM generation
            self._ensure_user_message_persisted(text)
            self._messages.append({"role": "user", "content": text})

            if hasattr(self, '_generation_task') and self._generation_task and not self._generation_task.done():
                self._cancelled = True
                self._generation_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(self._generation_task), timeout=3.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                self._cancelled = False

            if hasattr(self, '_generation_requested_at'):
                self._generation_requested_at = time.monotonic()
            self._generation_task = asyncio.create_task(self._generate_response())
            return

        # All other frames — pass through
        await self.push_frame(frame, direction)

    # ------------------------------------------------------------------ #
    #  send_welcome_message                                               #
    # ------------------------------------------------------------------ #

    async def send_welcome_message(self, agent_name: str = "Heart"):
        """Send a welcome message through the pipeline."""
        from distr.core.agent.libs import LLMFullResponseStartFrame, LLMFullResponseEndFrame, TextFrame

        if self._cancelled:
            return

        if not getattr(self, '_FrameProcessor__started', False):
            setattr(self, '_FrameProcessor__started', True)

        welcome_sentences = await self._build_welcome_sentences(agent_name)

        if self._cancelled:
            return

        full_message = " ".join(welcome_sentences)
        logger.info("WELCOME: %s", full_message)

        await self.push_frame(LLMFullResponseStartFrame(), getattr(self, '_pipeline_direction', None))

        try:
            if self._speaker_enabled and not self._cancelled:
                for sentence in welcome_sentences:
                    if self._cancelled:
                        break
                    await self.push_frame(TextFrame(text=sentence), getattr(self, '_pipeline_direction', None))
                    await asyncio.sleep(0.15)
                    if self._cancelled:
                        break

                wait_time = max(1.0, len(welcome_sentences) * 0.5)
                await asyncio.sleep(wait_time)
        except asyncio.CancelledError:
            logger.warning("Welcome message task cancelled")
        finally:
            try:
                await self.push_frame(LLMFullResponseEndFrame(), getattr(self, '_pipeline_direction', None))
            except Exception as e:
                logger.error("Failed to push LLMFullResponseEndFrame in welcome: %s", e)

        self._messages.append({"role": "assistant", "content": full_message})

    async def _build_welcome_sentences(self, agent_name: str) -> list:
        """Build welcome sentences, including conversation summary if available."""
        include_interaction = not (hasattr(self, 'event_queue') and self.event_queue is not None)

        def _add_interaction(sentences):
            if include_interaction:
                if self._is_hands_free:
                    sentences.append(f"To get my attention, just use my name {agent_name}, or say 'Agent' or any wake word you prefer.")
                else:
                    sentences.append("To talk to me, just hold down on the oracle and then speak.")
            return sentences

        default_welcome = _add_interaction([
            f"Hello {self._username}!",
            "I'm your AI assistant, here to help you get things done!",
            "I'm ready to help!",
        ])

        if not self.chat_manager:
            return default_welcome

        current_chat_id = self.chat_manager.get_current_chat()
        if not current_chat_id:
            return default_welcome

        try:
            history = self.chat_manager.get_chat_history(current_chat_id)
            conversation_messages = [
                msg for msg in history
                if msg.get('role') != 'system' and msg.get('content', '').strip()
            ]

            if not conversation_messages:
                return default_welcome

            conversation_text = ""
            for msg in conversation_messages[-6:]:
                role = msg.get('role', 'user')
                content = msg.get('content', '').strip()
                if content:
                    prefix = "You" if role == 'user' else agent_name
                    conversation_text += f"{prefix}: {content}\n"

            if not conversation_text.strip():
                return default_welcome

            if self._cancelled:
                return default_welcome

            summary = await self._generate_welcome_summary(conversation_text, agent_name)

            if self._cancelled:
                return default_welcome

            if summary and summary.lower() not in ('nothing', 'we discussed nothing', 'we talked about nothing'):
                return _add_interaction([
                    f"Hello {self._username}! Welcome back!",
                    summary,
                    "What would you like to talk about or do today?",
                ])

            return _add_interaction([
                f"Hello {self._username}! Welcome back!",
                "We were chatting previously. What would you like to continue with?",
            ])

        except Exception as e:
            logger.error("Error building welcome sentences: %s", e, exc_info=True)

            error_str = str(e)
            if any(kw in error_str.lower() for kw in ['429', 'rate_limit', 'insufficient_quota', 'exceeded']):
                if self.event_queue:
                    try:
                        current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                        self.event_queue.put(('chat_stream_error', {
                            'error': "API Quota Exceeded — check your billing or switch providers in Settings > LLMs",
                            'chat_id': current_chat_id,
                        }), block=False)
                    except Exception:
                        pass

            return _add_interaction([
                f"Hello {self._username}! Welcome back!",
                "We were chatting previously. What would you like to continue with?",
            ])

    async def _generate_welcome_summary(self, conversation_text: str, agent_name: str) -> str:
        """Generate a welcome summary. Default uses Ollama. Override for other providers."""
        summary_prompt = (
            f"You are summarizing a previous conversation between you and the user.\n\n"
            f"IMPORTANT:\n"
            f"- Summarize what you and the user were TALKING ABOUT — topics, questions, stories, tasks.\n"
            f"- ALWAYS refer to the user as \"You\" (second person).\n"
            f"- ALWAYS refer to yourself as \"I\" (first person).\n"
            f"- Use \"You and I\" or \"We\" when referring to shared actions.\n\n"
            f"Conversation history:\n{conversation_text}\n\n"
            f"Provide a brief, natural summary (max 2 sentences). Just the summary, no explanations:"
        )
        try:
            import ollama as _ollama
            client = _ollama.AsyncClient(timeout=120.0)
            response = await client.chat(
                model=self._model_name,
                messages=[{"role": "user", "content": summary_prompt}],
                options={"keep_alive": -1, "num_predict": 200},
            )
            return response.get('message', {}).get('content', '').strip()
        except Exception as e:
            logger.error("Error generating welcome summary: %s", e, exc_info=True)
            return ""
