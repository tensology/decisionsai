"""
Ollama LLM Service

Thin provider layer on top of LLMSharedMixin (shared logic) and
OllamaResponseMixin (streaming / tool-call helpers).

Only contains:
- __init__ (Ollama-specific state)
- _validate_messages (Ollama message-format fixer)
- _get_full_system_prompt, _convert_tools_to_ollama_format
- _generate_response (orchestrator delegating to OllamaResponseMixin)
- _process_stream, _execute_tool_calls
- _should_skip_fast_action, _resolve_current_model, reset_conversation
- _format_vision_message (Ollama uses "images" array, not OpenAI format)
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE, LLMService,
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    ErrorFrame,
    ollama, OLLAMA_AVAILABLE,
)
from distr.core.agent.services.llm.tool_format import convert_tools_to_openai_format
from distr.core.agent.services.llm.prompt import (
    load_system_prompt_template, build_tools_description,
)
from distr.core.agent.services.llm.text_utils import (
    clean_text_for_tts, parse_tool_calls_from_content,
)
from distr.core.agent.services.llm.fast_action_detector import detect_fast_action, ActionType
from ..core_mixin import LLMSharedMixin
from ..mixins.ollama_response import OllamaResponseMixin
from distr.core.agent.tools import load_tools

logger = logging.getLogger(__name__)

# Suppress noisy Pipecat loggers
for _name in ("pipecat", "pipecat.transports", "pipecat.pipeline", "pipecat.utils"):
    logging.getLogger(_name).setLevel(logging.INFO)

from distr.core.signals import signal_manager


class OllamaLLMService(OllamaResponseMixin, LLMSharedMixin, LLMService):
    """Ollama-based LLM service using Pipecat."""

    def __init__(self, model_name: str = None, system_prompt: str = None,
                 event_queue=None, is_listening=True, chat_manager=None, tts_service=None,
                 agent_name: str = "Heart", command_queue=None, confirmation_results_dict=None, **kwargs):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for OllamaLLMService")
        if not OLLAMA_AVAILABLE:
            raise ImportError("ollama is required for OllamaLLMService")

        super().__init__(**kwargs)

        # Resolve model name: use provided, or fall back to RAM-based recommendation
        if not model_name or not model_name.strip():
            try:
                from distr.core.system_resources import recommend_model
                model_name = recommend_model()
            except Exception:
                model_name = "qwen3:0.6b"  # absolute fallback — smallest model

        # --- Common state ---
        self._model_name = model_name
        self._is_hands_free = False
        self._is_listening = is_listening
        self._is_dictating = False
        self._hands_free_before_dictation = False
        self.event_queue = event_queue
        self.chat_manager = chat_manager
        self._tts_service = tts_service
        self._agent_name = agent_name
        self._speaker_enabled = True
        self.command_queue = command_queue
        self.confirmation_results_dict = confirmation_results_dict

        # Load tools
        try:
            self._tools = load_tools(
                chat_manager=chat_manager, use_navigation_tools=True,
                llm_service=self, tts_service=tts_service, llm_model=model_name,
                event_queue=event_queue, command_queue=command_queue,
                confirmation_results_dict=confirmation_results_dict,
            )
            self._tools_dict = {tool.name: tool for tool in self._tools}
            # Build semantic tool router index in background
            self._build_tool_router_async()
        except Exception as e:
            logger.warning("Failed to load tools: %s", e)
            self._tools = []
            self._tools_dict = {}

        # Connect signals (session orchestrates chat switches — no current_chat_changed listener)
        if self.chat_manager:
            self.chat_manager.on("chat_deleted", self.on_chat_deleted)
            try:
                signal_manager.chat_cleared.connect(self.on_chat_cleared)
            except RuntimeError:
                pass  # signal_manager GC'd in spawned child process — no QApplication
        try:
            signal_manager.files_indexed.connect(self._on_files_indexed)
        except (RuntimeError, Exception):
            pass

        # Username + system prompt (shared helpers)
        self._username = self._get_username()

        # Build system prompt
        self._default_template_raw = load_system_prompt_template()
        self._persona = system_prompt if system_prompt else None
        current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
        system_message = self._build_system_message(chat_id=current_chat_id)
        self._messages = [system_message]

        # Generation state
        self._generation_task = None
        self._pipeline_direction = None
        self._cancelled = False
        self._processed_fast_actions = set()

        # Reusable Ollama client — avoids connection setup overhead per request
        self._ollama_client = ollama.AsyncClient(timeout=120.0)

        logger.info("OllamaLLMService initialized with model: %s", model_name)

    # ------------------------------------------------------------------
    #  Ollama-specific helpers
    # ------------------------------------------------------------------

    def _get_full_system_prompt(self, template=None):
        """Build the full system prompt including persona."""
        content = template if template else self.default_template
        if hasattr(self, '_persona') and self._persona:
            return f"{self._persona}\n\n{content}"
        return content

    def _convert_tools_to_ollama_format(self, tools_list=None):
        """Convert tools to OpenAI/Ollama function calling format."""
        tools = tools_list if tools_list is not None else self._tools
        return convert_tools_to_openai_format(tools) if tools else []

    def _validate_messages(self, messages: list) -> list:
        """Validate and fix message format for Ollama API."""
        return self._validate_messages_for_ollama(messages)

    def _validate_messages_for_ollama(self, messages: list) -> list:
        if not messages:
            return messages

        validated = []
        i = 0
        while i < len(messages):
            msg = messages[i]

            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                tool_names = set()
                for tc in msg.get('tool_calls', []):
                    if isinstance(tc, dict):
                        func = tc.get('function', {})
                        if isinstance(func, dict) and func.get('name'):
                            tool_names.add(func['name'])
                i += 1

                found_names = set()
                tool_responses = []
                while i < len(messages) and messages[i].get('role') == 'tool':
                    name = messages[i].get('name', '')
                    if name in tool_names:
                        tool_responses.append(messages[i])
                        found_names.add(name)
                    i += 1

                if tool_names - found_names:
                    validated.append({
                        "role": "assistant",
                        "content": msg.get('content') or "Tool execution was interrupted.",
                    })
                else:
                    validated.append(msg)
                    validated.extend(tool_responses)

            elif msg.get('role') == 'tool':
                prev = validated[-1] if validated else None
                if not (prev and prev.get('role') == 'assistant' and prev.get('tool_calls')):
                    tool_name = msg.get('name', 'tool')
                    validated.append({
                        "role": "assistant", "content": "",
                        "tool_calls": [{"function": {"name": tool_name, "arguments": "{}"}}],
                    })
                    validated.append(msg)
                else:
                    validated.append(msg)
                i += 1
            else:
                validated.append(msg)
                i += 1

        return validated

    def _format_vision_message(self, text: str, base64_image: str, mime_type: str):
        """Ollama vision format uses 'images' array — return dict instead of list."""
        return {"content": text, "images": [base64_image]}

    async def process_chat_input(self, text: str, is_telegram: bool = False,
                                  uploaded_image_path: str = None, speaker_enabled=None):
        """Override to handle Ollama's special image format in messages."""
        t_start = time.time()
        from distr.core.agent.services.llm.image_utils import (
            get_image_path_from_context, convert_image_to_base64,
            check_vision_model_support, analyze_image_with_vision_llm,
        )

        self._cancelled = False
        await asyncio.sleep(0.05)
        if speaker_enabled is not None:
            self._speaker_enabled = bool(speaker_enabled)

        self._is_telegram_request = is_telegram
        self._uploaded_image_path = uploaded_image_path

        self._ensure_user_message_persisted(text)

        # Vision handling
        image_path = get_image_path_from_context(uploaded_image_path)
        if image_path and os.path.exists(image_path):
            vision_supported = check_vision_model_support(self._model_name, "Ollama")
            if vision_supported:
                try:
                    base64_image, mime_type = convert_image_to_base64(image_path)
                    self._messages.append({"role": "user", "content": text, "images": [base64_image]})
                except Exception as e:
                    logger.error("Failed to include image: %s", e, exc_info=True)
                    self._messages.append({"role": "user", "content": text})
            else:
                analysis = await analyze_image_with_vision_llm(image_path, text)
                if analysis:
                    self._messages.append({"role": "user", "content": f"[Image analysis: {analysis}]\n\nUser's question: {text}"})
                else:
                    self._messages.append({"role": "user", "content": text})
        elif image_path:
            self._messages.append({"role": "user", "content": text})
        else:
            self._messages.append({"role": "user", "content": text})

        # Cancel previous generation
        if self._generation_task and not self._generation_task.done():
            self._cancelled = True
            self._generation_task.cancel()
            try:
                await self._generation_task
            except asyncio.CancelledError:
                pass
            self._cancelled = False

        logger.info("LLM: process_chat_input -> _generate_response in %.3fs", time.time() - t_start)
        self._generation_task = asyncio.create_task(self._generate_response())

    # ------------------------------------------------------------------
    #  _generate_response — orchestrator (delegates to OllamaResponseMixin)
    # ------------------------------------------------------------------

    async def _generate_response(self):
        """Generate LLM response — orchestrator."""
        start_time = time.time()
        self._cancelled = False

        import threading as _threading
        if getattr(self, '_is_telegram_request', False):
            _threading.current_thread().telegram_request = True
            if hasattr(self, '_uploaded_image_path') and self._uploaded_image_path and os.path.exists(self._uploaded_image_path):
                _threading.current_thread().telegram_uploaded_image = self._uploaded_image_path

        current_chat_id = None
        full_response = ""

        try:
            # 1. Load history & rebuild system prompt
            t0 = time.time()
            current_chat_id, is_processing_tool_result, last_user_content = (
                self._prepare_messages_and_prompt()
            )
            logger.info("LLM: [1] prepare_messages: %.3fs (%d msgs)", time.time() - t0, len(self._messages))
            if self._cancelled:
                return
            await self.push_frame(LLMFullResponseStartFrame())
            if self._cancelled:
                return
            current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None

            # 2. Fast action detection
            last_user_message = None
            for msg in reversed(self._messages):
                if msg.get('role') == 'user':
                    last_user_message = msg.get('content', '')
                    break
            if last_user_message and not is_processing_tool_result:
                if not self._should_skip_fast_action(last_user_message):
                    t1 = time.time()
                    has_clipboard = any(
                        'CLIPBOARD CONTENT:' in m.get('content', '')
                        for m in self._messages[-5:] if m.get('role') == 'tool'
                    )
                    fast_action = detect_fast_action(last_user_message, has_clipboard)
                    logger.info("LLM: [2] fast_action_detect: %.3fs (action=%s conf=%.2f)",
                                time.time() - t1, fast_action.action_type.name, fast_action.confidence)
                    if (fast_action.confidence >= 0.9
                            and fast_action.action_type not in (ActionType.UNKNOWN, ActionType.CONVERSATIONAL)):
                        if await self._execute_fast_action(fast_action, current_chat_id):
                            return

            # 3. Prepare tools via semantic router (single source of truth)
            t2 = time.time()
            filtered_tools = self._get_filtered_tools(last_user_message)
            ollama_tools = self._convert_tools_to_ollama_format(filtered_tools)
            # If filter returned empty, it was classified as conversational.
            # No need to call detect_request_type again.
            allow_tools = bool(ollama_tools)
            request_type = 'conversational' if not allow_tools else 'question_with_tools'
            logger.info("LLM: [3] tools+routing: %.3fs (%d tools, type=%s)",
                        time.time() - t2, len(ollama_tools), request_type)

            # 4. Resolve model & check provider mismatch
            current_model = self._resolve_current_model()
            if self._check_provider_mismatch(current_model, current_chat_id):
                return

            # 5. Call Ollama streaming API (reuse client)
            sys_len = len(self._messages[0].get('content', '')) if self._messages else 0
            logger.info("LLM: [5] calling ollama model=%s msgs=%d sys_prompt_len=%d chars/%d tokens(est) tools=%d",
                        current_model, len(self._messages), sys_len, sys_len // 4, len(ollama_tools))
            t3 = time.time()
            # Adaptive context window: smaller models don't need 8192 and
            # prompt eval is proportional to num_ctx.  When tools are passed
            # via the API the model needs room for tool schemas (~2k tokens).
            num_ctx = 8192
            chat_kwargs = {
                "model": current_model,
                "messages": self._messages,
                "stream": True,
                "options": {"keep_alive": -1, "num_ctx": num_ctx, "temperature": 0.7},
            }
            if ollama_tools:
                chat_kwargs["tools"] = ollama_tools
            if current_chat_id:
                try:
                    signal_manager.chat_stream_started.emit(current_chat_id)
                    signal_manager.typing_indicator_changed.emit(True)
                except (RuntimeError, Exception):
                    pass
            try:
                stream = await self._ollama_client.chat(**chat_kwargs)
            except Exception as _tool_err:
                _err_str = str(_tool_err).lower()
                if "does not support tools" in _err_str or ("400" in _err_str and "tool" in _err_str):
                    # Model doesn't support native tool calling — retry without tools
                    logger.warning(
                        "LLM: Model '%s' does not support tools. Retrying without tools (conversation-only mode).",
                        current_model,
                    )
                    chat_kwargs.pop("tools", None)
                    stream = await self._ollama_client.chat(**chat_kwargs)
                    allow_tools = False
                    ollama_tools = []
                else:
                    raise
            logger.info("LLM: [5] ollama.chat() returned stream: %.3fs", time.time() - t3)

            # 6. Process streaming chunks
            t4 = time.time()
            full_response, tool_calls, final_message = await self._process_stream(
                stream, current_chat_id, allow_tools, is_processing_tool_result, start_time,
            )
            logger.info("LLM: [6] stream done: %.3fs (%d chars, %d tool_calls)",
                        time.time() - t4, len(full_response), len(tool_calls))

            # 7. Extract tool calls from final message
            if not tool_calls and final_message:
                tool_calls.extend(
                    self._extract_tool_calls_from_final_message(final_message, is_processing_tool_result)
                )
            if not tool_calls and last_user_message and 'clear' in last_user_message.lower() and 'chat' in last_user_message.lower():
                parsed = parse_tool_calls_from_content(full_response)
                if parsed:
                    tool_calls = [{'function': {'name': p.get('name', 'clear_chat'), 'arguments': p.get('arguments', '{"confirm": true}')}} for p in parsed]
                    full_response = ""
                elif 'clear' in full_response.lower():
                    tool_calls = [{'function': {'name': 'clear_chat', 'arguments': '{"confirm": true}'}}]
                    full_response = ""

            # 8. (Removed) — conversational filtering is handled by the semantic
            #    router in step 3.  No second-guessing needed.

            # 9. Intercept hallucinated tool names
            self._intercept_tool_calls(tool_calls, last_user_message)

            # 10. Execute tools (router already filtered; if tools survived, execute them)
            if tool_calls and allow_tools and not is_processing_tool_result:
                tool_results = await self._execute_tool_calls(tool_calls, last_user_message=last_user_message)
                self._messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})

                # Log routing telemetry
                routing_path = f"fast_action→no | router→{request_type} | tools={len(ollama_tools)}"
                for tc in tool_calls:
                    t_name = tc.get('function', {}).get('name', '')
                    chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                    from distr.core.agent.tool_audit import record_tool_execution
                    record_tool_execution(
                        chat_id, t_name,
                        f"routing: {routing_path}",
                        "routed",
                        instruction_hint=f"[{routing_path}] {(last_user_message or '')[:120]}",
                        event_queue=self.event_queue,
                    )

                full_response, should_return = await self._handle_post_tool_execution(
                    tool_calls, tool_results, current_chat_id, full_response,
                )
                if should_return:
                    return

            # 11. Handle pending tool result follow-up
            if is_processing_tool_result and not full_response:
                full_response, should_return = await self._handle_tool_result_followup(current_chat_id, full_response)
                if should_return:
                    return

            # 12. Re-query without tools if no response
            if not full_response and not tool_calls and not is_processing_tool_result:
                full_response = await self._requery_without_tools(full_response)

            # 13. Save response
            if current_chat_id and full_response:
                if tool_calls and any(tc.get('function', {}).get('name') == 'clear_chat' for tc in tool_calls):
                    return
                try:
                    self.chat_manager.add_assistant_message(current_chat_id, full_response)
                    signal_manager.chat_stream_finished.emit(current_chat_id)
                    signal_manager.typing_indicator_changed.emit(False)
                except (RuntimeError, Exception) as e:
                    logger.warning("Could not save/signal: %s", e)
            if full_response:
                self._messages.append({"role": "assistant", "content": full_response})

        except asyncio.CancelledError:
            logger.info("LLM: cancelled after %.3fs", time.time() - start_time)
            if current_chat_id and full_response:
                try:
                    self.chat_manager.add_assistant_message(current_chat_id, full_response.strip())
                except Exception:
                    pass
            self._emit_interruption_cleanup()
        except Exception as e:
            logger.error("LLM Error (%.3fs): %s", time.time() - start_time, e, exc_info=True)
            await self.push_frame(ErrorFrame(error=str(e)))
        finally:
            logger.info("LLM: total generation: %.3fs", time.time() - start_time)
            self._emit_telegram_response(full_response)
            if not self._cancelled:
                await self.push_frame(LLMFullResponseEndFrame())
                # Safety net: always emit chat_stream_finished so the Step
                # Runner (and any other listener) never gets stuck waiting.
                if current_chat_id:
                    try:
                        signal_manager.typing_indicator_changed.emit(False)
                        signal_manager.chat_stream_finished.emit(current_chat_id)
                    except RuntimeError:
                        pass
            self._cleanup_telegram_flags()

    # ------------------------------------------------------------------
    #  Stream processing & tool execution (Ollama-specific)
    # ------------------------------------------------------------------

    def _should_skip_fast_action(self, last_user_message):
        """Return True if fast action detection should be skipped for this message."""
        if last_user_message in self._processed_fast_actions:
            return True
        for i in range(len(self._messages) - 1, -1, -1):
            msg = self._messages[i]
            if msg.get('role') == 'user' and msg.get('content') == last_user_message:
                if (i + 2 < len(self._messages)
                        and self._messages[i + 1].get('role') == 'assistant'
                        and self._messages[i + 1].get('tool_calls')
                        and self._messages[i + 2].get('role') == 'tool'):
                    return True
                break
        return False

    def _resolve_current_model(self):
        """Determine the model to use for this request.

        If the resolved model is not installed in Ollama, falls back to the
        first available model and logs a warning so the user knows.
        """
        model = self._model_name
        if self.chat_manager and hasattr(self.chat_manager, 'current_model') and self.chat_manager.current_model:
            mgr = self.chat_manager.current_model
            if mgr not in ("Ollama", "OpenAI", "Anthropic"):
                model = mgr
            cid = self.chat_manager.get_current_chat()
            if cid:
                try:
                    from distr.core.db import get_session, Chat
                    session = get_session()
                    chat = session.get(Chat, cid)
                    if chat and chat.model_name and chat.model_name not in ("Ollama", "OpenAI", "Anthropic"):
                        model = chat.model_name
                    session.close()
                except Exception:
                    pass

        # Validate the model is actually installed in Ollama
        try:
            import ollama as _ollama
            installed = {m.get("name", m.get("model", "")) for m in _ollama.list().get("models", [])}
            # Ollama list returns names like "qwen3:8b" — normalize for comparison
            # Also check without the ":latest" suffix
            model_variants = {model, f"{model}:latest"}
            if not model_variants & installed:
                # Model not installed — try to find any available model
                if installed:
                    fallback = next(iter(installed))
                    logger.warning(
                        "Model '%s' not found in Ollama. Available: %s. Falling back to '%s'. "
                        "Please start a new chat with the correct model, or run: ollama pull %s",
                        model, ", ".join(sorted(installed)[:5]), fallback, model,
                    )
                    model = fallback
                else:
                    logger.error("No Ollama models installed. Please run: ollama pull <model_name>")
        except Exception as e:
            logger.debug("Could not validate Ollama model availability: %s", e)

        return model

    async def _process_stream(self, stream, current_chat_id,
                              allow_tools, is_processing_tool_result,
                              start_time):
        """Consume the Ollama streaming response. Returns (full_response, tool_calls, final_message)."""
        full_response = ""
        tool_calls = []
        final_message = None
        raw_accumulator = ""
        first_token = False

        async for chunk in stream:
            if self._cancelled:
                break

            message = chunk.get('message', {})
            final_message = message
            content = message.get('content', '')

            if content:
                if len(raw_accumulator) < 10000:
                    raw_accumulator += content
                parsed = parse_tool_calls_from_content(raw_accumulator)
                if parsed:
                    for ptc in parsed:
                        tool_calls.append({'function': {'name': ptc.get('name', ''), 'arguments': ptc.get('arguments', '{}')}})
                    content = re.sub(r'\{[^{}]*"name"[^{}]*\}', '', content, flags=re.DOTALL).strip()
                    if not content:
                        message['content'] = ''

            if 'tool_calls' in message and message['tool_calls']:
                tool_calls.extend(message['tool_calls'])

            # If tools arrived and we're allowed to use them, break to execute.
            # If we're processing a prior tool result, ignore new tool calls.
            # If tools aren't allowed (conversational), drop them and keep streaming text.
            if tool_calls and allow_tools and not is_processing_tool_result:
                break
            elif tool_calls and is_processing_tool_result:
                tool_calls = []
            elif tool_calls and not allow_tools and content:
                tool_calls = []

            if content and not (tool_calls and allow_tools):
                if not first_token:
                    first_token = True
                    logger.debug("LLM: TTFT: %.2fs", time.time() - start_time)

                cleaned = clean_text_for_tts(content, strip_whitespace=False)
                for cmd in ['startlistening', 'start listening', 'stoplistening', 'stop listening',
                            'stopspeaking', 'stop speaking']:
                    cleaned = cleaned.replace(cmd, '').replace(cmd.replace(' ', ''), '')

                if cleaned and cleaned.strip():
                    full_response += cleaned
                    if current_chat_id:
                        try:
                            signal_manager.chat_stream_token.emit(cleaned)
                        except (RuntimeError, Exception):
                            pass
                    if self._cancelled:
                        break
                    if self._speaker_enabled and not getattr(self, '_is_telegram_request', False):
                        await self.push_frame(TextFrame(text=cleaned))

        return full_response, tool_calls, final_message

    async def _execute_tool_calls(self, tool_calls, last_user_message=None):
        """Execute tool calls and return results."""
        results = []
        for tool_call in tool_calls:
            function = tool_call.get('function', {})
            tool_name = function.get('name', '')
            arguments = function.get('arguments', '{}')

            try:
                args_dict = json.loads(arguments) if isinstance(arguments, str) else arguments
                if isinstance(args_dict, str):
                    args_dict = {'text': args_dict}
            except (json.JSONDecodeError, ValueError, TypeError):
                args_dict = {}

            if tool_name in self._tools_dict:
                tool = self._tools_dict[tool_name]
                try:
                    # Inject context for tools that need it
                    if tool_name in ('oracle_globe', 'clipboard_action', 'mouse_movement') and not args_dict.get('text'):
                        if last_user_message:
                            args_dict['text'] = last_user_message
                        elif self._messages:
                            for msg in reversed(self._messages):
                                if msg.get('role') == 'user':
                                    args_dict['text'] = msg.get('content', '')
                                    break

                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, lambda t=tool, a=args_dict: t._run(**a)
                    )
                    results.append(result)

                    chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                    from distr.core.agent.tool_audit import record_tool_execution
                    record_tool_execution(chat_id, tool_name, str(result), "completed", event_queue=self.event_queue)
                except Exception as e:
                    error_msg = f"Error executing tool {tool_name}: {e}"
                    logger.error(error_msg, exc_info=True)
                    results.append(error_msg)
                    chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                    from distr.core.agent.tool_audit import record_tool_execution
                    record_tool_execution(chat_id, tool_name, error_msg, "failed", event_queue=self.event_queue)
            else:
                results.append(f"Tool '{tool_name}' not found. Available: {', '.join(list(self._tools_dict.keys())[:5])}...")

        return results

    def reset_conversation(self):
        """Reset the conversation history."""
        self._messages = [self._messages[0]]  # Keep system message
