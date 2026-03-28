"""
Ollama response generation helpers.

Extracted from OllamaLLMService._generate_response() to keep ollama.py under 1800 lines.
OllamaLLMService inherits from this mixin to get these methods.
"""

import asyncio
import json
import logging
import os
import re
import time

from distr.core.agent.services.llm.prompt import build_tools_description
from distr.core.agent.services.llm.text_utils import (
    clean_text_for_tts, parse_tool_calls_from_content,
)
from distr.core.agent.services.llm.fast_action_detector import detect_fast_action, ActionType
from distr.core.signals import signal_manager

logger = logging.getLogger(__name__)


class OllamaResponseMixin:
    """Helpers for Ollama _generate_response(), mixed into OllamaLLMService."""

    # ------------------------------------------------------------------
    # 1. Prepare messages: load DB history, rebuild system prompt
    # ------------------------------------------------------------------

    def _prepare_messages_and_prompt(self):
        """Build self._messages from in-memory state + system prompt.

        Uses the in-memory message list directly instead of reloading from DB
        every time.  The DB is the source of truth for *persistence*; the
        in-memory list is the source of truth for the current generation.

        Returns (current_chat_id, is_processing_tool_result, last_user_content).
        """
        current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
        is_processing_tool_result = False

        if not (self.chat_manager and current_chat_id):
            logger.warning("LLM: No chat_manager or current_chat_id")
            return current_chat_id, False, ""

        try:
            # Determine last user content for tool filtering
            last_user_content = ""
            for msg in reversed(self._messages):
                if msg.get('role') == 'user':
                    last_user_content = msg.get('content', '')
                    break

            # Rebuild system prompt only if dropped-files context changed
            # Skip tool descriptions in system prompt — Ollama receives full tool
            # schemas via the ``tools`` API parameter, so repeating them in the
            # system prompt just wastes prompt-eval tokens (saves ~2-3k tokens).
            self._maybe_rebuild_system_prompt(current_chat_id, include_tools_description=False)

            # Ensure system message is first
            system_content = self._get_full_system_prompt()
            if self._messages and self._messages[0].get('role') == 'system':
                self._messages[0] = {"role": "system", "content": system_content}
            else:
                self._messages.insert(0, {"role": "system", "content": system_content})

            # Validate and truncate conversation (keep system + last 20)
            conv = [m for m in self._messages[1:] if m.get('role') != 'system']
            conv = self._validate_messages_for_ollama(conv)
            if len(conv) > 20:
                conv = conv[-20:]
            self._messages = [self._messages[0]] + conv

            # Detect if we're processing a tool result
            is_processing_tool_result = self._detect_tool_result_state()

        except Exception as e:
            logger.error("LLM: Error preparing messages: %s", e, exc_info=True)

        return current_chat_id, is_processing_tool_result, last_user_content

    def _maybe_rebuild_system_prompt(self, chat_id, include_tools_description=True):
        """Rebuild self.default_template only when something actually changed.

        Caches the dropped-files context hash and skips the expensive rebuild
        (disk I/O, file metadata, folder paths) when nothing changed.

        Args:
            include_tools_description: If False, omit the verbose tool descriptions
                from the system prompt.  When tools are passed via the Ollama ``tools``
                API parameter the model already has full tool schemas, so repeating
                them in the system prompt just wastes prompt-eval tokens.
        """
        try:
            dropped_files_context = self._get_dropped_files_context(chat_id=chat_id)
            # Include the tools flag in the cache key so switching between
            # tools-in-prompt vs tools-via-API correctly invalidates the cache.
            ctx_hash = hash((dropped_files_context, include_tools_description))

            if (hasattr(self, '_cached_prompt_hash')
                    and self._cached_prompt_hash == ctx_hash
                    and hasattr(self, 'default_template')
                    and self.default_template):
                return  # Nothing changed — skip rebuild

            current_model = self._model_name
            if self.chat_manager and hasattr(self.chat_manager, 'current_model') and self.chat_manager.current_model:
                current_model = self.chat_manager.current_model

            from distr.core.settings import get_system_folder_paths
            folder_paths = get_system_folder_paths()
            home_path = os.path.expanduser("~")

            tools_desc = build_tools_description(self._tools) if include_tools_description else ""

            self.default_template = self._default_template_raw.format(
                agent_name=self._agent_name,
                username=self._username,
                tools_description=tools_desc,
                model_name=current_model,
                desktop_path=folder_paths.get("Desktop", os.path.join(home_path, "Desktop")),
                documents_path=folder_paths.get("Documents", os.path.join(home_path, "Documents")),
                downloads_path=folder_paths.get("Downloads", os.path.join(home_path, "Downloads")),
                pictures_path=folder_paths.get("Pictures", os.path.join(home_path, "Pictures")),
                music_path=folder_paths.get("Music", os.path.join(home_path, "Music")),
                videos_path=folder_paths.get("Videos", os.path.join(home_path, "Videos")),
                home_path=home_path,
                dropped_files_context=dropped_files_context,
            )

            # Inject active project context
            try:
                from distr.core.agent.services.rag.project import get_active_project_context
                project_context = get_active_project_context()
                if project_context:
                    self.default_template += f"\n\n{project_context}"
            except Exception as e:
                logger.warning("Could not inject project context: %s", e)

            self._cached_prompt_hash = ctx_hash

        except Exception as e:
            logger.warning("LLM: Failed to reload/format system prompt: %s", e)
            if not hasattr(self, 'default_template') or not self.default_template:
                self.default_template = f"You are {self._agent_name}. {build_tools_description(self._tools)}"

    def _collect_pending_tool_results(self, conversation_messages):
        """Return tool results in self._messages that aren't in conversation_messages yet."""
        pending = []
        if not self._messages:
            return pending
        for msg in self._messages:
            if msg.get('role') == 'tool' and msg.get('name') in ['execute_code', 'file_operations', 'web_search']:
                exists = any(
                    m.get('role') == 'tool' and m.get('name') == msg.get('name') and m.get('content') == msg.get('content')
                    for m in conversation_messages
                )
                if not exists:
                    pending.append(msg)
        return pending

    def _detect_tool_result_state(self):
        """Check if the last messages indicate we're processing a tool result."""
        if len(self._messages) >= 2:
            if (self._messages[-1].get('role') == 'tool'
                    and self._messages[-2].get('role') == 'assistant'
                    and self._messages[-2].get('tool_calls')):
                return True
        if self._messages and self._messages[-1].get('role') == 'tool':
            if self._messages[-1].get('name', '') in ['execute_code', 'file_operations', 'web_search']:
                return True
        return False

    # ------------------------------------------------------------------
    # 2. Provider mismatch detection (hot-reload)
    # ------------------------------------------------------------------

    def _check_provider_mismatch(self, current_model, current_chat_id):
        """Return True (and enqueue hot-reload event) if model belongs to another provider."""
        from distr.core.llm_factory import is_openai_model, is_anthropic_model

        model_lower = (current_model or "").lower()

        # Embedding models
        if any(x in model_lower for x in ['embed', 'embedding', 'nomic-embed']):
            raise ValueError(f"Embedding model '{current_model}' does not support chat. Select a chat model.")

        if is_openai_model(current_model):
            self._enqueue_hot_reload('OpenAI', current_model, current_chat_id)
            return True
        if is_anthropic_model(current_model):
            self._enqueue_hot_reload('Anthropic', current_model, current_chat_id)
            return True

        # Groq
        _groq_suffixes = ('-versatile', '-32768', '-specdec', '-8192', '-131072')
        _groq_prefixes = ('llama-3', 'llama3', 'mixtral-', 'gemma2-', 'whisper-large')
        if (any(model_lower.endswith(s) for s in _groq_suffixes)
                or (any(model_lower.startswith(p) for p in _groq_prefixes) and ':' not in current_model)):
            self._enqueue_hot_reload('Groq', current_model, current_chat_id)
            return True

        # OpenRouter / KiloCode (provider/model format)
        if '/' in current_model:
            provider = current_model.split('/')[0].lower()
            known = ['google', 'anthropic', 'openai', 'meta', 'mistral', 'deepseek',
                     'perplexity', 'aws', 'groq', 'openrouter', 'cerebras', 'huggingface']
            if provider in known or any(provider in p for p in known):
                self._enqueue_hot_reload('OpenRouter', current_model, current_chat_id)
                return True
            # Unknown provider/model format — still not Ollama
            self._enqueue_hot_reload('OpenRouter', current_model, current_chat_id)
            return True

        return False

    def _enqueue_hot_reload(self, provider, model_name, chat_id):
        logger.warning("Model '%s' needs %s but OllamaLLMService is active. Requesting hot-reload.", model_name, provider)
        if self.event_queue:
            self.event_queue.put(('model_hot_reload', {'provider': provider, 'model_name': model_name, 'chat_id': chat_id}), block=False)


    # ------------------------------------------------------------------
    # 3. Intent detection (conversational vs command)
    # ------------------------------------------------------------------

    @staticmethod
    # ------------------------------------------------------------------
    # 4. Extract tool calls from final streaming message
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tool_calls_from_final_message(final_message, is_processing_tool_result):
        """Parse tool_calls from the final Ollama message (Pydantic or dict).

        Returns list of tool call dicts (may be empty).
        """
        if final_message is None:
            return []

        def _block_if_processing(calls):
            if is_processing_tool_result and calls:
                logger.warning("LLM: Found %d tool call(s) in final message but processing tool result — blocking.", len(calls))
                return []
            return calls

        # Pydantic Message object
        if hasattr(final_message, 'tool_calls'):
            raw = getattr(final_message, 'tool_calls', None) or []
            converted = []
            for tc in raw:
                func = getattr(tc, 'function', None)
                if func and hasattr(func, 'name') and hasattr(func, 'arguments'):
                    converted.append({'function': {'name': func.name, 'arguments': func.arguments}})
            return _block_if_processing(converted)

        # Plain dict
        if isinstance(final_message, dict):
            if 'tool_calls' in final_message:
                return _block_if_processing(final_message.get('tool_calls', []))
            if 'function_call' in final_message:
                fc = final_message['function_call']
                return _block_if_processing([{'function': {'name': fc.get('name', ''), 'arguments': fc.get('arguments', '{}')}}])
            return []

        # Try model_dump / dict()
        try:
            if hasattr(final_message, 'model_dump'):
                d = final_message.model_dump()
            elif hasattr(final_message, 'dict'):
                d = final_message.dict()
            else:
                d = dict(final_message) if hasattr(final_message, '__iter__') else {}
            if 'tool_calls' in d:
                return _block_if_processing(d.get('tool_calls', []))
        except Exception:
            pass

        return []

    # ------------------------------------------------------------------
    # 5. Intercept / fix hallucinated tool names
    # ------------------------------------------------------------------

    @staticmethod
    def _intercept_tool_calls(tool_calls, last_user_message):
        """Fix common LLM tool-name hallucinations in-place."""
        if not tool_calls or not last_user_message:
            return
        user_lower = last_user_message.lower()
        is_snippet = any(s in user_lower for s in ['snippet', 'snip it', 'snipit'])

        for i, tc in enumerate(tool_calls):
            name = tc.get('function', {}).get('name', '')

            # 1. create snippet redirect
            if ('create' in user_lower or 'make' in user_lower) and name == 'clipboard_action' and is_snippet:
                tool_calls[i] = {'function': {'name': 'create_snippet', 'arguments': json.dumps({'text': last_user_message})}}
                continue

            # 2. paste snippet redirect
            if 'paste' in user_lower:
                if name == 'text_editing' and is_snippet:
                    try:
                        args = json.loads(tc.get('function', {}).get('arguments', '{}'))
                        if args.get('operation') == 'paste':
                            tool_calls[i] = {'function': {'name': 'use_snippet', 'arguments': json.dumps({'text': last_user_message})}}
                    except (json.JSONDecodeError, ValueError):
                        pass
                elif name == 'clipboard_action' and is_snippet:
                    tool_calls[i] = {'function': {'name': 'use_snippet', 'arguments': json.dumps({'text': last_user_message})}}

            # 3. hallucinated "usesnippet"
            if name == 'usesnippet':
                text_arg = last_user_message
                try:
                    args = json.loads(tc.get('function', {}).get('arguments', '{}'))
                    text_arg = args.get('text', args.get('index', last_user_message))
                    if not isinstance(text_arg, str):
                        text_arg = str(text_arg)
                except (json.JSONDecodeError, ValueError):
                    pass
                tool_calls[i] = {'function': {'name': 'use_snippet', 'arguments': json.dumps({'text': text_arg})}}

            # 4. hallucinated "paste"
            if name == 'paste':
                if is_snippet:
                    tool_calls[i] = {'function': {'name': 'use_snippet', 'arguments': json.dumps({'text': last_user_message})}}
                else:
                    tool_calls[i] = {'function': {'name': 'text_editing', 'arguments': json.dumps({'operation': 'paste'})}}

            # 5. hallucinated "clipboardaction"
            if name == 'clipboardaction':
                action = 'get'
                try:
                    args = json.loads(tc.get('function', {}).get('arguments', '{}'))
                    action = args.get('action', 'get')
                except (json.JSONDecodeError, ValueError):
                    pass
                if is_snippet and ('paste' in user_lower or 'copy' in user_lower):
                    tool_calls[i] = {'function': {'name': 'use_snippet', 'arguments': json.dumps({'text': last_user_message})}}
                else:
                    tool_calls[i] = {'function': {'name': 'clipboard_action', 'arguments': json.dumps({'action': action})}}

    # ------------------------------------------------------------------
    # 6. Post-tool-execution handling
    # ------------------------------------------------------------------

    # Simple tools that just execute and say "Done"
    SIMPLE_TOOLS = frozenset([
        'text_editing', 'mouse_movement', 'mouse_actions', 'caret_movement',
        'special_key', 'function_key', 'media_control', 'open_window', 'open_file_menu',
        'keyboard_shortcut', 'oracle_control', 'oracle_globe', 'new_chat',
    ])

    async def _handle_post_tool_execution(self, tool_calls, tool_results, current_chat_id,
                                           full_response):
        """Handle results after tool execution. Returns (full_response, should_return).

        should_return=True means the caller should ``return`` immediately.
        """
        from distr.core.agent.libs import (
            TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
        )

        clipboard_tool_called = False
        clipboard_action_type = None

        for idx, tc in enumerate(tool_calls):
            if tc.get('function', {}).get('name') == 'clipboard_action':
                clipboard_tool_called = True
                try:
                    args = json.loads(tc['function'].get('arguments', '{}')) if isinstance(tc['function'].get('arguments'), str) else tc['function'].get('arguments', {})
                    clipboard_action_type = args.get('action', '')
                except (json.JSONDecodeError, ValueError):
                    pass
                if not clipboard_action_type and idx < len(tool_results):
                    r = tool_results[idx]
                    if isinstance(r, str):
                        if r.startswith("CLIPBOARD CONTENT:"):
                            clipboard_action_type = 'get'
                        elif "Processing explanation" in r:
                            clipboard_action_type = 'explain'
                        elif "Processing elaboration" in r:
                            clipboard_action_type = 'elaborate'
                        elif "Reading" in r and "characters" in r:
                            clipboard_action_type = 'read'
                break

        # --- clear_chat ---
        if any(tc.get('function', {}).get('name') == 'clear_chat' for tc in tool_calls):
            system_prompt = self._messages[0] if self._messages else None
            self._messages = [system_prompt] if system_prompt else []
            if not self._cancelled:
                if not getattr(self, '_is_telegram_request', False):
                    await self.push_frame(TextFrame(text="Done"))
                await self.push_frame(LLMFullResponseEndFrame())
            self._emit_typing_finished(current_chat_id)
            return full_response, True

        execute_code_called = any(tc.get('function', {}).get('name') == 'execute_code' for tc in tool_calls)
        file_ops_called = any(tc.get('function', {}).get('name') == 'file_operations' for tc in tool_calls)

        # --- add tool results to messages selectively ---
        for tc, result in zip(tool_calls, tool_results):
            name = tc.get('function', {}).get('name', '')

            if name == 'exit_app':
                resp = await self._handle_exit_app(result)
                if resp:
                    return full_response, True

            elif name == 'clipboard_action':
                try:
                    args = json.loads(tc['function'].get('arguments', '{}')) if isinstance(tc['function'].get('arguments'), str) else tc['function'].get('arguments', {})
                    action = args.get('action', '') or clipboard_action_type or ''
                    if action in ('explain', 'elaborate', 'get'):
                        self._messages.append({"role": "tool", "content": result, "name": name})
                except (json.JSONDecodeError, ValueError):
                    self._messages.append({"role": "tool", "content": result, "name": name})

            elif name == 'save_audio':
                await self._handle_save_audio_result(result, current_chat_id)
                return full_response, True

            elif name in self.SIMPLE_TOOLS:
                pass  # Don't add to messages

            elif name in ('execute_code', 'file_operations'):
                self._messages.append({"role": "tool", "content": result, "name": name})

            else:
                self._messages.append({"role": "tool", "content": result, "name": name})

        # --- decide what to say after tool execution ---
        if clipboard_tool_called:
            if clipboard_action_type in ('explain', 'elaborate', 'read'):
                return full_response, False  # tool already handled TTS
            elif clipboard_action_type == 'get':
                pass  # will trigger LLM follow-up below

        if execute_code_called or file_ops_called:
            return await self._trigger_llm_followup(full_response)

        # new_chat
        if any(tc.get('function', {}).get('name') == 'new_chat' for tc in tool_calls):
            return await self._handle_new_chat_result(tool_calls, tool_results, full_response)

        # exit_app already handled above
        if any(tc.get('function', {}).get('name') == 'exit_app' for tc in tool_calls):
            return full_response, True

        # web_search
        if any(tc.get('function', {}).get('name') == 'web_search' for tc in tool_calls):
            return await self._trigger_llm_followup(full_response)

        # google_workspace — results contain email/calendar/drive data that the
        # LLM needs to summarise and present to the user.  We re-query inline
        # (instead of _trigger_llm_followup which self-cancels) so the stream
        # stays alive and the UI remains in the busy state.
        if any(tc.get('function', {}).get('name') == 'google_workspace' for tc in tool_calls):
            followup_response = await self._inline_llm_followup(current_chat_id)
            if followup_response:
                full_response = followup_response
            return full_response, True

        # --- errors ---
        errors = [r for r in tool_results if isinstance(r, str) and (
            r.startswith("Error") or ("error" in r.lower() and ("failed" in r.lower() or "timeout" in r.lower() or "cannot" in r.lower()))
        )]
        if errors:
            return await self._speak_tool_errors(errors, current_chat_id)

        # --- default: say "Done" ---
        if any(tc.get('function', {}).get('name') == 'clear_chat' for tc in tool_calls):
            return full_response, True

        brief = self._determine_brief_confirmation(tool_calls, tool_results)
        if brief:
            full_response += brief
            if not self._cancelled:
                await self.push_frame(TextFrame(text=brief))
            self._save_and_signal(current_chat_id, brief)
        return full_response, False

    # --- sub-helpers for _handle_post_tool_execution ---

    async def _handle_exit_app(self, result):
        from distr.core.agent.libs import TextFrame, LLMFullResponseEndFrame
        goodbye = result or "Goodbye! It was great helping you today."
        if not self._cancelled:
            await self.push_frame(TextFrame(text=goodbye))
            await self.push_frame(LLMFullResponseEndFrame())
        import threading
        word_count = len(goodbye.split())
        wait = max(1.0, word_count / 2.0) + 0.5

        def _send():
            time.sleep(wait)
            if self.event_queue:
                self.event_queue.put(('exit_app', {}), block=False)
        threading.Thread(target=_send, daemon=True).start()
        return True

    async def _handle_save_audio_result(self, result, chat_id):
        from distr.core.agent.libs import TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame
        feedback = result if result and not result.startswith("Error:") else (result or "Audio saved successfully")
        if not self._cancelled:
            await self.push_frame(LLMFullResponseStartFrame())
            cleaned = clean_text_for_tts(feedback)
            if cleaned:
                await self.push_frame(TextFrame(text=cleaned))
            await self.push_frame(LLMFullResponseEndFrame())
        self._save_and_signal(chat_id, feedback)
        self._messages.append({"role": "assistant", "content": feedback})

    async def _trigger_llm_followup(self, full_response):
        """Cancel current generation and trigger a new one for tool result processing."""
        if self._generation_task and not self._generation_task.done():
            self._cancelled = True
            self._generation_task.cancel()
            try:
                await self._generation_task
            except asyncio.CancelledError:
                pass
            self._cancelled = False
        self._generation_task = asyncio.create_task(self._generate_response())
        return full_response, True

    async def _inline_llm_followup(self, current_chat_id):
        """Re-query the LLM with tool results already in self._messages.

        Unlike _trigger_llm_followup this does NOT cancel the current task.
        It streams the response inline so the UI stays in the busy state
        throughout.  Tools are included so the LLM can chain calls (e.g.
        read email → create ticket).  Returns the generated text (may be
        empty on error).
        """
        from distr.core.agent.libs import TextFrame

        full_response = ""
        try:
            current_model = self._resolve_current_model()

            # Determine last user message for tool filtering
            last_user_msg = ""
            for msg in reversed(self._messages):
                if msg.get("role") == "user":
                    last_user_msg = msg.get("content", "")
                    break

            # Include tools so the LLM can chain calls
            filtered_tools = self._get_filtered_tools(last_user_msg)
            ollama_tools = self._convert_tools_to_ollama_format(filtered_tools)

            # Validate messages so Ollama doesn't choke on orphan tool msgs
            system_msg = self._messages[0] if self._messages and self._messages[0].get("role") == "system" else None
            conv = [m for m in self._messages[1:] if m.get("role") != "system"] if system_msg else list(self._messages)
            conv = self._validate_messages_for_ollama(conv)
            validated = ([system_msg] + conv) if system_msg else conv

            chat_kwargs = {
                "model": current_model,
                "messages": validated,
                "stream": True,
                "options": {"keep_alive": -1, "num_ctx": 8192, "temperature": 0.7},
            }
            if ollama_tools:
                chat_kwargs["tools"] = ollama_tools

            stream = await self._ollama_client.chat(**chat_kwargs)

            tool_calls = []
            async for chunk in stream:
                if self._cancelled:
                    break
                msg = chunk.get("message", {})
                content = msg.get("content", "")
                if content:
                    cleaned = clean_text_for_tts(content, strip_whitespace=False)
                    if cleaned:
                        full_response += cleaned
                        if not self._cancelled:
                            try:
                                signal_manager.chat_stream_token.emit(cleaned)
                            except (RuntimeError, Exception):
                                pass
                            if self._speaker_enabled and not getattr(self, "_is_telegram_request", False):
                                await self.push_frame(TextFrame(text=cleaned))
                # Collect any tool calls from the follow-up
                if msg.get("tool_calls"):
                    tool_calls.extend(msg["tool_calls"])

            # If the LLM wants to chain another tool call, execute it and
            # do one more follow-up (non-recursive to avoid infinite loops).
            if tool_calls and not self._cancelled:
                self._intercept_tool_calls(tool_calls, last_user_msg)
                tool_results = await self._execute_tool_calls(tool_calls, last_user_message=last_user_msg)
                self._messages.append({"role": "assistant", "content": full_response or "", "tool_calls": tool_calls})
                for tc, result in zip(tool_calls, tool_results):
                    name = tc.get("function", {}).get("name", "")
                    self._messages.append({"role": "tool", "content": str(result), "name": name})

                # One more LLM pass to summarise the chained results (no tools
                # this time to prevent infinite chaining).
                conv2 = [m for m in self._messages[1:] if m.get("role") != "system"] if system_msg else list(self._messages)
                conv2 = self._validate_messages_for_ollama(conv2)
                validated2 = ([system_msg] + conv2) if system_msg else conv2

                chain_response = ""
                stream2 = await self._ollama_client.chat(
                    model=current_model,
                    messages=validated2,
                    stream=True,
                    options={"keep_alive": -1, "num_ctx": 8192, "temperature": 0.7},
                )
                async for chunk in stream2:
                    if self._cancelled:
                        break
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        cleaned = clean_text_for_tts(content, strip_whitespace=False)
                        if cleaned:
                            chain_response += cleaned
                            if not self._cancelled:
                                try:
                                    signal_manager.chat_stream_token.emit(cleaned)
                                except (RuntimeError, Exception):
                                    pass
                                if self._speaker_enabled and not getattr(self, "_is_telegram_request", False):
                                    await self.push_frame(TextFrame(text=cleaned))
                if chain_response:
                    full_response = chain_response  # final answer replaces intermediate

        except Exception as e:
            logger.error("LLM: inline followup error: %s", e, exc_info=True)

        if full_response:
            self._save_and_signal(current_chat_id, full_response)
            self._messages.append({"role": "assistant", "content": full_response})
        return full_response

    async def _handle_new_chat_result(self, tool_calls, tool_results, full_response):
        from distr.core.agent.libs import TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame

        new_chat_id = None
        for result in tool_results:
            try:
                d = json.loads(result) if isinstance(result, str) else result
                if isinstance(d, dict) and d.get('status') == 'success':
                    new_chat_id = d.get('chat_id')
                    break
            except (json.JSONDecodeError, TypeError):
                continue

        current = self.chat_manager.get_current_chat() if self.chat_manager else None
        if new_chat_id and current != new_chat_id:
            self.chat_manager.set_current_chat(new_chat_id)
        target = new_chat_id or current
        if target and self.event_queue:
            try:
                self.event_queue.put(('load_chat', {'chat_id': target}), block=False)
            except Exception:
                pass

        system_prompt = self._messages[0] if self._messages else None
        self._messages = [system_prompt] if system_prompt else []

        if not self._cancelled:
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(TextFrame(text="A new conversation has been created"))
            await self.push_frame(LLMFullResponseEndFrame())
        return full_response, True

    async def _speak_tool_errors(self, errors, chat_id):
        from distr.core.agent.libs import TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame
        error_msg = " ".join(errors).replace("Error: ", "").replace("Error executing task: ", "").strip()
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + "..."
        if not self._cancelled:
            await self.push_frame(LLMFullResponseStartFrame())
            cleaned = clean_text_for_tts(error_msg)
            if cleaned:
                await self.push_frame(TextFrame(text=cleaned))
            await self.push_frame(LLMFullResponseEndFrame())
        self._save_and_signal(chat_id, error_msg)
        return error_msg, True

    def _determine_brief_confirmation(self, tool_calls, tool_results):
        snippet_called = any(tc.get('function', {}).get('name') in ('create_snippet', 'use_snippet') for tc in tool_calls)
        if snippet_called:
            valid = [str(r) for r in tool_results if r and not str(r).startswith("Error:")]
            return " ".join(valid) if valid else "Done"
        exec_called = any(tc.get('function', {}).get('name') in ('execute_code', 'file_operations') for tc in tool_calls)
        if exec_called:
            return None  # LLM will respond
        return "Done"

    def _save_and_signal(self, chat_id, text):
        if self.chat_manager and chat_id:
            try:
                self.chat_manager.add_assistant_message(chat_id, text)
                signal_manager.chat_message_added.emit(chat_id, "assistant", text)
                signal_manager.chat_stream_finished.emit(chat_id)
            except (RuntimeError, Exception) as e:
                logger.warning("LLM: Could not save/signal: %s", e)

    def _emit_typing_finished(self, chat_id):
        if chat_id:
            try:
                signal_manager.typing_indicator_changed.emit(False)
                signal_manager.chat_stream_finished.emit(chat_id)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # 7. Handle tool-result follow-up (web_search, execute_code, etc.)
    # ------------------------------------------------------------------

    async def _handle_tool_result_followup(self, current_chat_id, full_response):
        """When we're processing a tool result and LLM didn't generate text, handle it.

        Returns (full_response, should_return).
        """
        from distr.core.agent.libs import TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame

        tool_msg = self._messages[-1] if self._messages and self._messages[-1].get('role') == 'tool' else None
        if not tool_msg:
            return full_response, False

        name = tool_msg.get('name', '')
        content = tool_msg.get('content', '')

        # web_search / summarize_clipboard — use result directly
        if name in ('web_search', 'summarize_clipboard') and content:
            full_response = content
            if not self._cancelled:
                await self.push_frame(LLMFullResponseStartFrame())
                cleaned = clean_text_for_tts(content)
                if cleaned:
                    await self.push_frame(TextFrame(text=cleaned))
                await self.push_frame(LLMFullResponseEndFrame())
            self._save_and_signal(current_chat_id, full_response)
            self._messages.append({"role": "assistant", "content": full_response})
            return full_response, True

        # execute_code / file_operations — generate fallback summary
        if name in ('execute_code', 'file_operations') and content:
            lines = [l.strip() for l in content.split('\n') if l.strip()]
            file_count = sum(1 for l in lines if any(ext in l.lower() for ext in ['.txt', '.pdf', '.jpg', '.png', '.zip', '.doc']))
            if file_count > 0:
                full_response = f"Found {file_count} file{'s' if file_count != 1 else ''} in your downloads folder."
            else:
                summary_lines = [l for l in lines if any(k in l.lower() for k in ['found', 'listed', 'files', 'completed', 'success'])]
                full_response = (summary_lines[0][:200] if summary_lines else
                                 "I've checked your downloads folder and found the files you asked about.")

            if not self._cancelled:
                await self.push_frame(LLMFullResponseStartFrame())
                cleaned = clean_text_for_tts(full_response)
                if cleaned:
                    await self.push_frame(TextFrame(text=cleaned))
                await self.push_frame(LLMFullResponseEndFrame())
            self._save_and_signal(current_chat_id, full_response)
            self._messages.append({"role": "assistant", "content": full_response})
            return full_response, True

        return full_response, False

    # ------------------------------------------------------------------
    # 8. Re-query without tools (conversational fallback)
    # ------------------------------------------------------------------

    async def _requery_without_tools(self, full_response):
        """Re-query Ollama without tools when LLM only produced tool calls for a question.

        Returns updated full_response.
        """
        from distr.core.agent.libs import TextFrame

        last_user_msg = self._messages.pop() if self._messages else None
        original_system = self._messages[0] if self._messages else {"role": "system", "content": self._get_full_system_prompt()}

        tools_desc = build_tools_description(self._tools)
        system_without_tools = original_system['content'].replace(
            tools_desc, "Respond conversationally and naturally. Keep responses concise and friendly."
        )
        self._messages[0] = {"role": "system", "content": system_without_tools}
        if last_user_msg:
            self._messages.append(last_user_msg)

        try:
            stream = await self._ollama_client.chat(
                model=self._model_name,
                messages=self._messages,
                stream=True,
                options={"keep_alive": -1, "temperature": 0.7},
            )
            async for chunk in stream:
                if self._cancelled:
                    break
                content = chunk.get('message', {}).get('content', '')
                if content:
                    cleaned = clean_text_for_tts(content, strip_whitespace=False)
                    if cleaned:
                        full_response += cleaned
                        if not self._cancelled and self._speaker_enabled and not getattr(self, '_is_telegram_request', False):
                            await self.push_frame(TextFrame(text=cleaned))
        except Exception as e:
            logger.error("LLM: Error re-querying without tools: %s", e)
        finally:
            self._messages[0] = original_system

        return full_response
