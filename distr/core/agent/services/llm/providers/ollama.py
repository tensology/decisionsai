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
from distr.core.agent.services.llm.computer_use_guard import build_computer_use_execution_decisions
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
                model_name = "deepseek-v4-pro:cloud"  # absolute fallback — cloud default

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

        # Load tools (use cache when available to avoid re-instantiation)
        try:
            from distr.core.agent.tools.loader import _tool_cache, get_warmed_tools_list
            if _tool_cache:
                self._tools = get_warmed_tools_list()
            else:
                self._tools = load_tools(
                    chat_manager=chat_manager, use_navigation_tools=True,
                    llm_service=self, tts_service=tts_service, llm_model=model_name,
                    event_queue=event_queue, command_queue=command_queue,
                    confirmation_results_dict=confirmation_results_dict,
                )
            self._tools_dict = {tool.name: tool for tool in self._tools}
            # Build semantic tool retriever index in background
            self._build_tool_index_async()
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
        self._default_template_raw = load_system_prompt_template(
            model_name=self._model_name,
            provider_name="ollama",
        )
        self._persona = system_prompt if system_prompt else None
        current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
        system_message = self._build_system_message(chat_id=current_chat_id)
        self._messages = [system_message]

        # Generation state
        self._generation_task = None
        self._pipeline_direction = None
        self._cancelled = False
        self._processed_fast_actions = set()

        # Reusable Ollama client — generous timeout for slow/memory-constrained machines
        # where model loading can take minutes due to disk swapping
        self._ollama_client = ollama.AsyncClient(timeout=300.0)

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

    @staticmethod
    def _normalize_tool_call_arguments(tool_calls: list) -> list:
        """Convert string `arguments` to dicts for Ollama Pydantic v2 validation.

        The ollama Python client v0.6.1+ uses Pydantic v2 ``Message.model_validate()``
        which requires ``tool_calls[].function.arguments`` to be a dict
        (``Mapping[str, Any]``), NOT a JSON string.  Internal code and older
        conversation history often stored arguments as strings like
        ``'{"confirm": true}'``.  Passing a string where a dict is expected
        triggers::

            ValidationError: Input should be a valid dictionary
                [type=dict_type, input_value='{}', input_type=str]

        This helper normalises every tool-call's ``arguments`` to a dict
        before messages are handed to ``ollama.AsyncClient.chat()``.
        """
        normalized = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                normalized.append(tc)
                continue
            func = tc.get('function')
            if not isinstance(func, dict):
                normalized.append(tc)
                continue
            args = func.get('arguments')
            if isinstance(args, str):
                try:
                    func = {**func, 'arguments': json.loads(args) if args else {}}
                except (json.JSONDecodeError, ValueError):
                    func = {**func, 'arguments': {}}
            elif args is None:
                func = {**func, 'arguments': {}}
            normalized.append({**tc, 'function': func})
        return normalized

    def _normalize_messages_arguments_inplace(self):
        """Ensure all assistant messages with tool_calls have dict arguments.

        Safety net — normalises ``self._messages`` in-place right before they
        are sent to ``ollama.AsyncClient.chat()``.  Catches any tool_calls
        that were appended to ``self._messages`` after the last
        ``_validate_messages_for_ollama()`` pass (e.g. from
        ``_execute_tool_calls`` → ``_handle_post_tool_execution`` →
        ``_trigger_llm_followup`` cycles).
        """
        for msg in self._messages:
            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                msg['tool_calls'] = self._normalize_tool_call_arguments(msg['tool_calls'])

    def _validate_messages_for_ollama(self, messages: list) -> list:
        if not messages:
            return messages

        validated = []
        i = 0
        while i < len(messages):
            msg = messages[i]

            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                # Normalize tool_call arguments from strings to dicts (Ollama v0.6.1+)
                normalized_tc = OllamaLLMService._normalize_tool_call_arguments(msg['tool_calls'])
                tool_names = set()
                for tc in normalized_tc:
                    if isinstance(tc, dict):
                        func = tc.get('function', {})
                        if isinstance(func, dict) and func.get('name'):
                            tool_names.add(func['name'])
                msg = {**msg, 'tool_calls': normalized_tc}
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
                        "tool_calls": [{"function": {"name": tool_name, "arguments": {}}}],
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
                                  uploaded_image_path: str = None, speaker_enabled=None,
                                  telegram_input_type: str = None):
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
        self._telegram_input_type = telegram_input_type if telegram_input_type in ("text", "voice") else None
        if is_telegram and self._telegram_input_type:
            import threading
            threading.current_thread().telegram_input_type = self._telegram_input_type

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

        # Strip any stale Tool_Hint_Block unconditionally — ensures clean state
        # even on early-exit paths or when model switches to a non-small tier.
        self._strip_tool_hint()

        import threading as _threading
        if getattr(self, '_is_telegram_request', False):
            _threading.current_thread().telegram_request = True
            if hasattr(self, '_uploaded_image_path') and self._uploaded_image_path and os.path.exists(self._uploaded_image_path):
                _threading.current_thread().telegram_uploaded_image = self._uploaded_image_path

        current_chat_id = None
        full_response = ""
        _signals_emitted = False  # Track whether stream-finished signals were emitted

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
                fast_action = self._check_fast_actions()
                if fast_action:
                    logger.info("LLM: ⚡ Fast action detected: %s (conf=%.2f)",
                                fast_action.action_type.name, fast_action.confidence)
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

            # 4b. Tier check — route small models to Text_Tool_Extraction path
            from distr.core.agent.tool_retriever import ToolRetriever as _ToolRetriever
            tier = _ToolRetriever.classify_model_tier(current_model)

            if tier == "small":
                logger.info(
                    "LLM: [small-path] model=%s tier=%s tools=%d",
                    current_model, tier, len(filtered_tools),
                )
                # Build and inject hint block if tools are available
                hint_block = self._build_tool_hint_block(filtered_tools)
                if hint_block:
                    self._inject_tool_hint(hint_block)

                # Build chat_kwargs WITHOUT a "tools" key (Req 2.4, 6.4)
                sys_len = len(self._messages[0].get('content', '')) if self._messages else 0
                logger.info(
                    "LLM: [small-path] calling ollama model=%s msgs=%d sys_prompt_len=%d",
                    current_model, len(self._messages), sys_len,
                )
                num_ctx = 8192
                # Normalize tool_calls arguments (Ollama v0.6.1+ Pydantic v2 requires dicts)
                self._normalize_messages_arguments_inplace()
                small_chat_kwargs = {
                    "model": current_model,
                    "messages": self._messages,
                    "stream": True,
                    "options": {"keep_alive": -1, "num_ctx": num_ctx, "temperature": 0.7},
                }
                # Explicitly no "tools" key
                if current_chat_id:
                    try:
                        signal_manager.chat_stream_started.emit(current_chat_id)
                        signal_manager.typing_indicator_changed.emit(True)
                    except (RuntimeError, Exception):
                        pass
                try:
                    small_stream = await self._ollama_client.chat(**small_chat_kwargs)
                    t_small = time.time()
                    full_response, _tc, _fm = await self._process_stream(
                        small_stream,
                        current_chat_id,
                        False,
                        is_processing_tool_result,
                        start_time,
                        last_user_message,
                    )
                    logger.info("LLM: [small-path] stream done: %.3fs (%d chars)", time.time() - t_small, len(full_response))
                except Exception as _small_err:
                    logger.error("LLM: [small-path] API error: %s", _small_err, exc_info=True)
                    await self.push_frame(ErrorFrame(error=str(_small_err)))
                    return

                # Safety net: if small-path also returns empty, emit fallback
                if not full_response and not _tc:
                    logger.error("LLM: [small-path] returned empty response. Emitting fallback message.")
                    full_response = "I'm having trouble connecting right now. Please try again in a moment."
                    # _process_stream emits TTS frames from streamed tokens.
                    # This fallback is assigned after streaming, so push it explicitly.
                    if self._speaker_enabled and not getattr(self, '_is_telegram_request', False):
                        await self.push_frame(TextFrame(text=full_response))
                    try:
                        signal_manager.typing_indicator_changed.emit(True)
                    except RuntimeError:
                        pass

                # Prompt injection check on last user message (Req 9.1, 9.2, 9.3)
                _skip_parse = False
                if last_user_message and self._check_prompt_injection(last_user_message):
                    _skip_parse = True

                _tool_executed = False
                if not _skip_parse and hint_block:
                    # Only attempt parsing when a hint was injected
                    _parse_start = time.time()
                    try:
                        from distr.core.agent.services.llm.intent_parser import parse as _intent_parse
                        _offered_names = [getattr(t, "name", "") for t in filtered_tools]
                        _parse_result = _intent_parse(full_response, _offered_names)
                        _parse_elapsed_ms = (time.time() - _parse_start) * 1000
                        # Req 10.2: warn if parsing exceeded 50ms for ≤2000 char response
                        if _parse_elapsed_ms > 50 and len(full_response) <= 2000:
                            logger.warning(
                                "LLM: [small-path] IntentParser exceeded 50ms: %.1fms for %d chars",
                                _parse_elapsed_ms, len(full_response),
                            )
                    except Exception as _parse_exc:
                        logger.warning(
                            "LLM: [small-path] IntentParser raised exception: %s", _parse_exc,
                        )
                        _parse_result = None

                    if _parse_result is not None:
                        _tool_name, _args = _parse_result

                        # Resolve positional fallback: parser returns {"_arg0": value}
                        # when the model used a positional arg instead of key=value.
                        # Map it to the first required parameter from the tool schema.
                        if "_arg0" in _args and len(_args) == 1:
                            from distr.core.agent.services.llm.tool_format import get_tool_parameters, convert_tools_to_openai_format
                            _pos_val = _args["_arg0"]
                            _pos_params = get_tool_parameters(_tool_name)
                            if not _pos_params and _tool_name in self._tools_dict:
                                _pos_schemas = convert_tools_to_openai_format([self._tools_dict[_tool_name]])
                                if _pos_schemas:
                                    _pos_params = _pos_schemas[0].get("function", {}).get("parameters", {})
                            _required = (_pos_params or {}).get("required", [])
                            _first_param = _required[0] if _required else None
                            if not _first_param:
                                _all_props = list((_pos_params or {}).get("properties", {}).keys())
                                _first_param = _all_props[0] if _all_props else None
                            if _first_param:
                                _args = {_first_param: _pos_val}
                            else:
                                # Can't map positional — treat as plain text
                                logger.warning("LLM: [small-path] positional arg for %r but no params in schema", _tool_name)
                                _parse_result = None
                        # Validate tool is in _tools_dict — try fuzzy match if not exact
                        if _tool_name not in self._tools_dict:
                            _fuzzy = self._fuzzy_match_tool(_tool_name)
                            if _fuzzy and _fuzzy.name in {getattr(t, "name", "") for t in filtered_tools}:
                                logger.info("LLM: [small-path] fuzzy matched %r → %r", _tool_name, _fuzzy.name)
                                _tool_name = _fuzzy.name
                            else:
                                logger.warning(
                                    "LLM: [small-path] extracted tool %r not in _tools_dict (fuzzy: %s). Response: %r",
                                    _tool_name, _fuzzy.name if _fuzzy else "none", full_response[:200],
                                )
                                _tool_name = None
                        # Validate tool was in the offered set — try fuzzy match if hallucinated
                        elif _tool_name not in {getattr(t, "name", "") for t in filtered_tools}:
                            _fuzzy = self._fuzzy_match_tool(_tool_name)
                            if _fuzzy and _fuzzy.name in {getattr(t, "name", "") for t in filtered_tools}:
                                logger.info("LLM: [small-path] fuzzy matched hallucinated %r → %r", _tool_name, _fuzzy.name)
                                _tool_name = _fuzzy.name
                            else:
                                logger.warning(
                                    "LLM: [small-path] hallucinated tool %r not in offered set %r. Response: %r",
                                    _tool_name, _offered_names, full_response[:200],
                                )
                                _tool_name = None
                        if _tool_name is not None:
                            logger.info(
                                "LLM: [small-path] extracted tool=%r snippet=%r",
                                _tool_name, full_response[:80],
                            )
                            _coerced = self._coerce_args_to_schema_types(_tool_name, _args)
                            _tool_calls = [{"function": {"name": _tool_name, "arguments": _coerced}}]
                            _tool_results = await self._execute_tool_calls(_tool_calls, last_user_message=last_user_message)
                            self._messages.append({"role": "assistant", "content": "", "tool_calls": _tool_calls})

                            # Record with routing_hint="text_extraction" (Req 5.4)
                            _chat_id_for_audit = self.chat_manager.get_current_chat() if self.chat_manager else None
                            from distr.core.agent.tool_audit import record_tool_execution
                            record_tool_execution(
                                _chat_id_for_audit, _tool_name,
                                str(_tool_results[0]) if _tool_results else "",
                                "completed",
                                instruction_hint=f"[text_extraction] {(last_user_message or '')[:120]}",
                                event_queue=self.event_queue,
                                routing_hint="text_extraction",
                            )

                            # Follow existing post-execution flow (Req 5.5)
                            full_response, should_return = await self._handle_post_tool_execution(
                                _tool_calls, _tool_results, current_chat_id, full_response,
                            )
                            _tool_executed = True
                            if should_return:
                                return
                    else:
                        # No tool intent found — log DEBUG (Req 7.3)
                        logger.debug("LLM: [small-path] no tool intent found in response")

                # Save plain-text response if no tool was executed
                if not _tool_executed:
                    if full_response:
                        full_response = clean_text_for_tts(full_response, strip_whitespace=True)
                    if current_chat_id and full_response:
                        # DB save is Python-only — should never fail due to Qt lifecycle
                        try:
                            self.chat_manager.add_assistant_message(current_chat_id, full_response)
                        except Exception as e:
                            logger.warning("Could not save assistant message: %s", e)
                        # Signal emissions may fail in agent subprocess (Qt C++ object deleted)
                        try:
                            signal_manager.chat_stream_finished.emit(current_chat_id)
                            signal_manager.typing_indicator_changed.emit(False)
                            _signals_emitted = True
                        except RuntimeError:
                            pass  # Expected in agent subprocess where Qt objects are GC'd
                    if full_response:
                        self._messages.append({"role": "assistant", "content": full_response})
                # Return early — do NOT fall through to the standard path
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
            # Normalize self._messages before sending: ensure all tool_calls[].function.arguments
            # are dicts (not strings).  Ollama v0.6.1+ Pydantic v2 validation rejects strings.
            self._normalize_messages_arguments_inplace()
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
                stream,
                current_chat_id,
                allow_tools,
                is_processing_tool_result,
                start_time,
                last_user_message,
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
                    tool_calls = [{'function': {'name': p.get('name', 'clear_chat'), 'arguments': {'confirm': True} if isinstance(p.get('arguments'), str) else (p.get('arguments') or {'confirm': True})}} for p in parsed]
                    full_response = ""
                elif 'clear' in full_response.lower():
                    tool_calls = [{'function': {'name': 'clear_chat', 'arguments': {'confirm': True}}}]
                    full_response = ""

            # 7b. Models often emit spurious mouse/oracle tools on "what do you do" — strip before execute.
            if (
                tool_calls
                and allow_tools
                and last_user_message
                and self._should_drop_tool_calls_for_meta_question(last_user_message, tool_calls)
            ):
                logger.info(
                    "LLM: stripping tool_calls after stream for meta assistant question: %s",
                    [tc.get("function", {}).get("name") for tc in tool_calls],
                )
                tool_calls.clear()

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

            # 12. Re-query without tools if no response (retry up to 2 times for empty responses)
            if not full_response and not tool_calls and not is_processing_tool_result:
                for _retry in range(2):
                    logger.warning("LLM: Empty response (0 chars, 0 tool_calls). Re-querying without tools (attempt %d)...", _retry + 1)
                    await asyncio.sleep(0.5 * _retry)  # Brief delay between retries
                    full_response = await self._requery_without_tools(full_response)
                    if full_response:
                        break
                    logger.warning("LLM: Re-query also returned empty response (attempt %d)", _retry + 1)

            # 12b. Final safety net — if still empty, emit a fallback message
            if not full_response and not tool_calls:
                logger.error("LLM: All attempts returned empty. Emitting fallback message.")
                full_response = "I'm having trouble connecting right now. Please try again in a moment."
                # _process_stream emits TTS frames from streamed tokens.
                # This fallback is assigned after streaming, so push it explicitly.
                if self._speaker_enabled and not getattr(self, '_is_telegram_request', False):
                    await self.push_frame(TextFrame(text=full_response))
                try:
                    signal_manager.typing_indicator_changed.emit(True)
                except RuntimeError:
                    pass

            # 13. Save response (clean markdown for display)
            if full_response:
                full_response = clean_text_for_tts(full_response, strip_whitespace=True)
            if current_chat_id and full_response:
                if tool_calls and any(tc.get('function', {}).get('name') == 'clear_chat' for tc in tool_calls):
                    return
                # DB save is Python-only — should never fail due to Qt lifecycle
                try:
                    self.chat_manager.add_assistant_message(current_chat_id, full_response)
                except Exception as e:
                    logger.warning("Could not save assistant message: %s", e)
                # Signal emissions may fail in agent subprocess (Qt C++ object deleted)
                try:
                    signal_manager.chat_stream_finished.emit(current_chat_id)
                    signal_manager.typing_indicator_changed.emit(False)
                    _signals_emitted = True
                except RuntimeError:
                    pass
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
            # Don't emit telegram response for cancelled tasks, but stop typing indicator
            if self.event_queue:
                self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
            _signals_emitted = True  # cancellation handles its own cleanup
            return
        except Exception as e:
            logger.error("LLM Error (%.3fs): %s", time.time() - start_time, e, exc_info=True)
            await self.push_frame(ErrorFrame(error=str(e)))
        finally:
            logger.info("LLM: total generation: %.3fs", time.time() - start_time)
            self._emit_telegram_response(full_response)
            if not self._cancelled:
                await self.push_frame(LLMFullResponseEndFrame())
                # Safety net: emit chat_stream_finished only if not already emitted
                # so workflow listeners (and other subscribers) never get stuck waiting.
                if current_chat_id and not _signals_emitted:
                    try:
                        signal_manager.typing_indicator_changed.emit(False)
                        signal_manager.chat_stream_finished.emit(current_chat_id)
                    except RuntimeError:
                        pass  # Expected in agent subprocess
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

    async def _process_stream(
        self,
        stream,
        current_chat_id,
        allow_tools,
        is_processing_tool_result,
        start_time,
        last_user_message=None,
    ):
        """Consume the Ollama streaming response. Returns (full_response, tool_calls, final_message)."""
        full_response = ""
        tool_calls = []
        final_message = None
        raw_accumulator = ""
        first_token = False
        tts_text_frames = 0

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
                        # Ensure arguments is a dict (Ollama Pydantic v2 requires dict, not string)
                        raw_args = ptc.get('arguments', '{}')
                        if isinstance(raw_args, str):
                            try:
                                raw_args = json.loads(raw_args) if raw_args else {}
                            except (json.JSONDecodeError, ValueError):
                                raw_args = {}
                        tool_calls.append({'function': {'name': ptc.get('name', ''), 'arguments': raw_args}})
                    content = re.sub(r'\{[^{}]*"name"[^{}]*\}', '', content, flags=re.DOTALL).strip()
                    if not content:
                        message['content'] = ''

            if 'tool_calls' in message and message['tool_calls']:
                tool_calls.extend(message['tool_calls'])

            # If tools arrived and we're allowed to use them, break to execute.
            # If we're processing a prior tool result, ignore new tool calls.
            # If tools aren't allowed (conversational), drop them and keep streaming text.
            if tool_calls and allow_tools and not is_processing_tool_result:
                if last_user_message and self._should_drop_tool_calls_for_meta_question(
                    last_user_message, tool_calls
                ):
                    logger.info(
                        "LLM: ignoring tool_calls on meta assistant question (would steal stream): %s",
                        [tc.get("function", {}).get("name") for tc in tool_calls],
                    )
                    tool_calls.clear()
                    raw_accumulator = ""
                    continue
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
                        tts_text_frames += 1

        if not first_token and not tool_calls:
            logger.warning("LLM: _process_stream received no content and no tool_calls from model (empty stream)")

        is_tg = bool(getattr(self, "_is_telegram_request", False))
        spk = bool(getattr(self, "_speaker_enabled", True))
        # One grep-friendly line per stream: UI can show tokens while TTS gets zero TextFrames.
        logger.info(
            "TTS_PIPELINE stream_done chat_id=%s speaker_enabled=%s is_telegram=%s "
            "chars=%s tts_text_frames=%s",
            current_chat_id,
            spk,
            is_tg,
            len(full_response),
            tts_text_frames,
        )
        if full_response and tts_text_frames == 0 and not is_tg and spk:
            logger.warning(
                "TTS_PIPELINE speaker enabled but zero TextFrames — UI may still show tokens; "
                "check TTS service / clean_text_for_tts stripping (chars=%s).",
                len(full_response),
            )

        return full_response, tool_calls, final_message

    async def _execute_tool_calls(self, tool_calls, last_user_message=None):
        """Execute tool calls and return results."""
        results = []
        decisions = build_computer_use_execution_decisions(tool_calls)
        for idx, tool_call in enumerate(tool_calls):
            function = tool_call.get('function', {})
            tool_name = function.get('name', '')
            arguments = function.get('arguments', {})
            decision = decisions[idx] if idx < len(decisions) else {"allow": True, "reason": "ok"}

            try:
                args_dict = arguments if isinstance(arguments, dict) else json.loads(arguments) if isinstance(arguments, str) else {}
                if isinstance(args_dict, str):
                    args_dict = {'text': args_dict}
            except (json.JSONDecodeError, ValueError, TypeError):
                args_dict = {}

            if not decision.get("allow", True):
                results.append(
                    "Skipped by computer-use guard: only one actioning computer-use step "
                    "is executed per round. Re-run next step after observing updated context."
                )
                continue

            if tool_name in self._tools_dict:
                tool = self._tools_dict[tool_name]
                try:
                    # Inject context for tools that need it
                    if getattr(self, '_is_telegram_request', False):
                        args_dict.setdefault("is_telegram_request", True)
                    if last_user_message:
                        args_dict.setdefault("last_user_message", last_user_message)
                    if tool_name in ('oracle_globe', 'clipboard_action', 'mouse_movement', 'send_file_to_telegram') and not args_dict.get('text'):
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
                # Fuzzy match — LLM may have hallucinated a tool name
                matched = self._fuzzy_match_tool(tool_name)
                if matched:
                    try:
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(
                            None, lambda t=matched, a=args_dict: t._run(**a)
                        )
                        results.append(result)
                        chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                        from distr.core.agent.tool_audit import record_tool_execution
                        record_tool_execution(chat_id, matched.name, str(result), "completed", event_queue=self.event_queue)
                    except Exception as e:
                        results.append(f"Error executing tool {matched.name}: {e}")
                else:
                    results.append(f"Tool '{tool_name}' not found. Available: {', '.join(list(self._tools_dict.keys())[:5])}...")

        return results

    def reset_conversation(self):
        """Reset the conversation history."""
        self._messages = [self._messages[0]]  # Keep system message

    # ------------------------------------------------------------------
    #  Tool hint block helpers (Text_Tool_Extraction path)
    # ------------------------------------------------------------------

    def _build_tool_hint_block(self, filtered_tools: list) -> str:
        """Build the plain-text Tool_Hint_Block for small models.

        Returns empty string if filtered_tools is empty.
        """
        if not filtered_tools:
            return ""

        from distr.core.agent.services.llm.tool_format import convert_tools_to_openai_format

        openai_schemas = convert_tools_to_openai_format(filtered_tools)
        schema_by_name = {s["function"]["name"]: s["function"] for s in openai_schemas if "function" in s}

        tool_lines = []
        for tool in filtered_tools:
            name = tool.name
            func = schema_by_name.get(name, {})
            description = (func.get("description") or getattr(tool, "description", "") or "")[:100]
            params = list((func.get("parameters") or {}).get("properties", {}).keys())
            param_str = ", ".join(f'{p}="..."' for p in params[:3])
            tool_lines.append(f'TOOL: {name}({param_str})  # {description}')

        lines = [
            "[TOOL USE]",
            "If you need to use a tool, output ONLY this on its own line (replace values in <>):",
            "",
        ] + [f"  {tl}" for tl in tool_lines] + [
            "",
            "Output the TOOL: line alone. No other text before or after it on that line.",
            "Do not use print(), do not use code blocks, do not add extra words.",
            "[/TOOL USE]",
        ]
        return "\n".join(lines)

    def _inject_tool_hint(self, hint_block: str) -> None:
        """Append hint_block to self._messages[0]['content'] (the system message)."""
        if not hint_block or not self._messages:
            return
        self._messages[0]["content"] = self._messages[0].get("content", "") + "\n\n" + hint_block

    def _strip_tool_hint(self) -> None:
        """Remove any previously injected Tool_Hint_Block from self._messages[0]['content'].

        Idempotent — safe to call multiple times.
        """
        if not self._messages:
            return
        content = self._messages[0].get("content", "")
        # Strip all known hint block formats
        for pattern in [
            r"\s*\[TOOL USE\].*?\[/TOOL USE\]\s*",
            r"\s*=== TOOL USE INSTRUCTIONS ===.*?=== END TOOL USE INSTRUCTIONS ===\s*",
            r"\s*--- Available Tools ---.*?--- End Tools ---\s*",
        ]:
            content = re.sub(pattern, "", content, flags=re.DOTALL)
        self._messages[0]["content"] = content

    def _check_prompt_injection(self, user_message: str) -> bool:
        """Return True and log WARNING if user_message contains a valid TOOL: pattern.

        A valid TOOL: pattern is a line of the form:
            TOOL: <tool_name>(...)
        where <tool_name> matches ^[a-zA-Z_][a-zA-Z0-9_]*$ and the line ends with ')'.

        Bare occurrences of 'TOOL:' not followed by a valid tool name and argument
        list do NOT trigger the warning.
        """
        # Pattern: line starting with TOOL: (case-insensitive), valid tool name, parens ending with )
        _INJECTION_PATTERN = re.compile(
            r"^tool:\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(.*\)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        if _INJECTION_PATTERN.search(user_message):
            logger.warning(
                "Potential prompt injection detected in user message: %r",
                user_message[:200],
            )
            return True
        return False

    def _coerce_args_to_schema_types(self, tool_name: str, args: dict) -> dict:
        """Coerce string arg values to the types declared in the tool's JSON schema."""
        tool = self._tools_dict.get(tool_name)
        if tool is None:
            return dict(args)

        # Extract properties from args_schema (Pydantic) or hardcoded params
        from distr.core.agent.services.llm.tool_format import get_tool_parameters, convert_tools_to_openai_format
        parameters = get_tool_parameters(tool_name)
        if not parameters:
            schemas = convert_tools_to_openai_format([tool])
            if schemas:
                parameters = schemas[0].get("function", {}).get("parameters", {})
        properties = (parameters or {}).get("properties", {})

        coerced = {}
        for key, value in args.items():
            declared_type = properties.get(key, {}).get("type", "string")
            try:
                if declared_type == "integer":
                    coerced[key] = int(value)
                elif declared_type == "number":
                    coerced[key] = float(value)
                elif declared_type == "boolean":
                    coerced[key] = (str(value).lower() == "true")
                else:
                    coerced[key] = str(value)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Coercion failed for arg %r (tool=%r, declared_type=%r, value=%r): %s",
                    key, tool_name, declared_type, value, exc,
                )
                coerced[key] = str(value)
        return coerced
