"""
OpenAI-Compatible LLM Service

Base for services using OpenAI-compatible APIs (Groq, OpenRouter, KiloCode, OpenAI).
Full feature parity:
- Telegram flag propagation + auto-send file
- System prompt refresh before generation
- Message validation + truncation
- Watchdog timer for hallucination loops
- Multi-round tool chaining
- TTS suppression for file paths
- Proper error handling with user-friendly messages

Subclasses must:
1. Set self.client (AsyncOpenAI instance with correct base_url) before calling super().__init__()
2. Set SERVICE_NAME and DEFAULT_MODEL
"""

import asyncio
import json
import logging
import re
import time
from typing import Optional

from distr.core.agent.libs import (
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    ErrorFrame,
)
from distr.core.agent.services.llm.tool_format import convert_tools_to_openai_format
from .base_service import BaseLLMService

logger = logging.getLogger(__name__)


class OpenAICompatibleLLMService(BaseLLMService):
    """
    Base for services using OpenAI-compatible APIs (Groq, OpenRouter, KiloCode).

    Subclasses must:
    1. Set self.client (AsyncOpenAI instance with correct base_url) before calling super().__init__()
    2. Set SERVICE_NAME and DEFAULT_MODEL
    """

    _MAX_GENERATION_TIME_WITHOUT_TOOL = 15.0

    async def _generate_response(self):
        """Orchestrator: stream → tool calls → follow-up → save."""
        import time as _time

        _t0 = _time.time()
        end_frame_sent = False
        full_content = ""
        follow_up_content = ""

        self._cancelled = False
        logger.info("%s: _generate_response() started (_speaker_enabled=%s)", self.SERVICE_NAME, self._speaker_enabled)

        self._propagate_telegram_flags()

        try:
            self._refresh_system_prompt_for_generation()
            logger.info("%s: [1] refresh_system_prompt: %.3fs", self.SERVICE_NAME, _time.time() - _t0)

            await self.push_frame(LLMFullResponseStartFrame())
            if self.event_queue:
                self.event_queue.put(('typing_indicator_changed', {'show': True}), block=False)
                current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                if current_chat_id:
                    self.event_queue.put(('chat_stream_started', {'chat_id': current_chat_id}), block=False)

            _t1 = _time.time()
            validated_messages = self._prepare_api_messages()

            last_user_msg = ""
            for msg in reversed(self._messages):
                if msg.get('role') == 'user':
                    last_user_msg = msg.get('content', '')
                    break
            filtered_tools = self._get_filtered_tools(last_user_msg)
            tools_list = convert_tools_to_openai_format(filtered_tools) if filtered_tools else None
            logger.info("%s: [2] prepare_messages+tools: %.3fs (%d msgs, %d tools)",
                        self.SERVICE_NAME, _time.time() - _t1, len(validated_messages),
                        len(tools_list) if tools_list else 0)

            _t2 = _time.time()
            stream = await self._call_stream(validated_messages, tools_list)
            logger.info("%s: [3] call_stream returned: %.3fs", self.SERVICE_NAME, _time.time() - _t2)

            _t3 = _time.time()
            full_content, tool_calls = await self._consume_stream(stream)
            logger.info("%s: [4] consume_stream: %.3fs (%d chars, %d tool_calls)",
                        self.SERVICE_NAME, _time.time() - _t3, len(full_content), len(tool_calls))

            if tool_calls:
                self._messages.append({
                    "role": "assistant",
                    "content": full_content or None,
                    "tool_calls": tool_calls,
                })
                await self._execute_tool_calls_with_chaining(tool_calls)

                auto_sent = await self._auto_send_file_to_telegram()
                if not auto_sent:
                    follow_up_content, follow_up_tool_calls = await self._process_follow_up()
                    if not follow_up_tool_calls:
                        await self._auto_send_file_to_telegram()
                    if follow_up_tool_calls:
                        await self._execute_chained_tools(follow_up_tool_calls, follow_up_content)
                    if follow_up_content:
                        self._handle_follow_up_content(follow_up_content)
                    else:
                        end_frame_sent = await self._send_done_after_tools()

            elif full_content and full_content.strip():
                self._save_assistant_message(full_content)
            elif not tool_calls:
                end_frame_sent = await self._handle_empty_response()

        except asyncio.CancelledError:
            logger.warning("%s: _generate_response cancelled (%.3fs)", self.SERVICE_NAME, _time.time() - _t0)
        except Exception as e:
            logger.error("Error in %s generation (%.3fs): %s", self.SERVICE_NAME, _time.time() - _t0, e, exc_info=True)
            await self._handle_generation_error(e)
        finally:
            logger.info("%s: total generation: %.3fs", self.SERVICE_NAME, _time.time() - _t0)
            self._emit_telegram_response(full_content, follow_up_content)
            needs_end_frame = self._cleanup_generation(end_frame_sent, full_content, follow_up_content)
            if needs_end_frame:
                await self.push_frame(LLMFullResponseEndFrame())

    def _refresh_system_prompt_for_generation(self):
        """Rebuild the first system message with latest dropped-files context."""
        current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
        try:
            system_msg = self._build_system_message(chat_id=current_chat_id, include_tools_description=False)
            if self._messages and self._messages[0].get('role') == 'system':
                self._messages[0] = system_msg
            else:
                self._messages.insert(0, system_msg)
        except Exception as e:
            logger.warning("%s: Failed to rebuild system prompt: %s", self.SERVICE_NAME, e)

    def _prepare_api_messages(self):
        """Return a validated, truncated copy of self._messages."""
        messages = self._messages.copy()
        if len(messages) > 35:
            logger.warning("Truncating conversation history from %d to 35 messages", len(messages))
            system = messages[0] if messages and messages[0].get('role') == 'system' else None
            recent = messages[-34:] if system else messages[-35:]
            messages = ([system] + recent) if system else recent
        return self._validate_messages(messages)

    async def _call_stream(self, messages, tools_list=None, max_retries=3):
        """Create a streaming chat completion with retry logic."""
        retry_delay = 1.0
        last_error = None
        use_tools = tools_list

        for attempt in range(max_retries):
            try:
                return await self.client.chat.completions.create(
                    model=self._model_name,
                    messages=messages,
                    tools=use_tools,
                    stream=True,
                )
            except Exception as e:
                last_error = e
                err = str(e).lower()

                if use_tools and ("context_length_exceeded" in err or "context length" in err or "8192" in err):
                    logger.warning("%s: context length exceeded — retrying without tools", self.SERVICE_NAME)
                    use_tools = None
                    continue

                if attempt == 0 and use_tools and ("404" in err or "not found" in err) and "tool use" in err:
                    logger.warning("%s: Model does not support tool use, retrying without tools", self.SERVICE_NAME)
                    use_tools = None
                    continue

                if self.SERVICE_NAME == "OpenRouterLLMService" and "no endpoints found matching your data policy" in err:
                    raise

                if (self.SERVICE_NAME == "OpenRouterLLMService" and "400" in err
                        and "developer instruction" in err and messages and messages[0].get("role") == "system"):
                    rest = messages[1:]
                    if not rest:
                        raise
                    messages = rest
                    logger.warning("%s: Model rejects system instruction, retrying without it", self.SERVICE_NAME)
                    continue

                if any(k in err for k in ('429', 'rate limit', 'rate-limited', 'connection', 'timeout', 'network')):
                    if attempt < max_retries - 1:
                        logger.warning("%s: attempt %d/%d failed: %s. Retrying in %.1fs…",
                                       self.SERVICE_NAME, attempt + 1, max_retries, e, retry_delay)
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                        continue

                raise

        raise last_error or Exception(f"Failed to create {self.SERVICE_NAME} stream")

    async def _consume_stream(self, stream):
        """Consume a streaming response. Returns (full_content, tool_calls)."""
        full_content = ""
        tool_calls = []
        current_tool_call = None
        chunk_count = 0
        content_chunks = 0
        tool_call_detected = False

        wd_content_len = [0]
        wd_chunk_count = [0]
        wd_content_chunks = [0]

        generation_start = time.time()
        self._last_tool_call_time = None

        async def watchdog():
            try:
                await asyncio.sleep(self._MAX_GENERATION_TIME_WITHOUT_TOOL)
                if not self._cancelled and self._last_tool_call_time is None:
                    elapsed = time.time() - generation_start
                    if wd_content_chunks[0] > 0 and (wd_chunk_count[0] > 50 or wd_content_len[0] > 500):
                        logger.error("WATCHDOG: Forcing stop — %d chunks, %d chars in %.1fs without tool calls",
                                     wd_chunk_count[0], wd_content_len[0], elapsed)
                        self._cancelled = True
            except asyncio.CancelledError:
                pass

        watchdog_task = asyncio.create_task(watchdog())

        try:
            async for chunk in stream:
                if self._cancelled:
                    break
                chunk_count += 1
                wd_chunk_count[0] = chunk_count

                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    full_content += delta.content
                    content_chunks += 1
                    wd_content_len[0] = len(full_content)
                    wd_content_chunks[0] = content_chunks

                    if not tool_call_detected:
                        if '<tool_call>' in full_content or '<think>' in full_content:
                            tool_call_detected = True

                    if self._speaker_enabled and not tool_call_detected and not getattr(self, '_is_telegram_request', False):
                        await self.push_frame(TextFrame(text=delta.content))
                    if self.event_queue and not tool_call_detected:
                        self.event_queue.put(('chat_stream_token', {'token': delta.content}), block=False)

                if delta.tool_calls:
                    tool_call_detected = True
                    self._last_tool_call_time = time.time()
                    for tc_chunk in delta.tool_calls:
                        if tc_chunk.id:
                            if current_tool_call:
                                tool_calls.append(current_tool_call)
                            current_tool_call = {
                                "id": tc_chunk.id,
                                "type": "function",
                                "function": {"name": tc_chunk.function.name, "arguments": ""},
                            }
                        elif current_tool_call and tc_chunk.function and tc_chunk.function.arguments:
                            current_tool_call["function"]["arguments"] += tc_chunk.function.arguments

            if current_tool_call:
                tool_calls.append(current_tool_call)
        finally:
            if not watchdog_task.done():
                watchdog_task.cancel()
                try:
                    await watchdog_task
                except asyncio.CancelledError:
                    pass

        logger.debug("%s stream: %d chunks, %d content, %d tool_calls, content_len=%d",
                     self.SERVICE_NAME, chunk_count, content_chunks, len(tool_calls), len(full_content))

        if not tool_calls and full_content and '<tool_call>' in full_content:
            parsed = self._parse_text_tool_calls(full_content)
            if parsed:
                tool_calls = parsed
                logger.info("%s: Parsed %d tool call(s) from <tool_call> text output", self.SERVICE_NAME, len(tool_calls))
            full_content = re.sub(r'<tool_call>\s*.*?\s*</tool_call>', '', full_content, flags=re.DOTALL).strip()
            full_content = re.sub(r'<tool_call>.*', '', full_content, flags=re.DOTALL).strip()

        if '<think>' in full_content:
            full_content = re.sub(r'<think>\s*.*?\s*</think>', '', full_content, flags=re.DOTALL).strip()
            full_content = re.sub(r'<think>.*', '', full_content, flags=re.DOTALL).strip()

        return full_content, tool_calls

    def _parse_text_tool_calls(self, content: str) -> list:
        """Parse tool calls emitted as <tool_call>JSON</tool_call> in the text stream."""
        tool_calls = []
        pattern = re.compile(r'<tool_call>\s*(.*?)\s*</tool_call>', re.DOTALL)
        for idx, match in enumerate(pattern.finditer(content)):
            raw = match.group(1).strip()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Failed to parse <tool_call> JSON: %s", raw[:200])
                continue

            name = parsed.get("name")
            if not name:
                logger.warning("Skipping <tool_call> without 'name': %s", raw[:200])
                continue

            args = parsed.get("arguments") or parsed.get("parameters") or {}
            if isinstance(args, dict):
                args_str = json.dumps(args)
            elif isinstance(args, str):
                args_str = args
            else:
                args_str = json.dumps(args)

            tool_calls.append({
                "id": f"text_tc_{idx}_{int(time.time()*1000)}",
                "type": "function",
                "function": {"name": name, "arguments": args_str},
            })

        return tool_calls

    async def _execute_tool_calls_with_chaining(self, tool_calls):
        """Execute tool calls, detect ACTION REQUIRED for Telegram chaining."""
        import threading

        last_user_message = ""
        for msg in reversed(self._messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                last_user_message = content if isinstance(content, str) else str(content)
                break

        for tc in tool_calls:
            func_name = tc["function"]["name"]
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                func_args = {}

            logger.info("🔧 Tool: %s", func_name)
            if last_user_message:
                func_args["last_user_message"] = last_user_message

            if func_name in self._tools_dict:
                tool = self._tools_dict[func_name]
                status = "completed"
                try:
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, lambda t=tool, a=func_args: t._run(**a)
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    result = f"Error executing tool: {e}"
                    status = "failed"
                    logger.error("Error executing tool %s: %s", func_name, e, exc_info=True)

                chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                from distr.core.agent.tool_audit import record_tool_execution
                record_tool_execution(chat_id, func_name, str(result), status, event_queue=self.event_queue)

                result_str = str(result)

                if "[ACTION REQUIRED" in result_str:
                    logger.debug("Tool %s returned [ACTION REQUIRED] — will auto-chain", func_name)
                    threading.current_thread().suppress_tts_for_tool_chain = True

                resp = {"tool_call_id": tc["id"], "role": "tool", "name": func_name, "content": result_str}
            else:
                resp = {"tool_call_id": tc["id"], "role": "tool", "name": func_name,
                        "content": f"Error: Tool '{func_name}' not found"}

            self._messages.append(resp)

    async def _process_follow_up(self):
        """Make a follow-up API call after tool execution. Returns (content, tool_calls)."""
        import threading

        messages = self._prepare_api_messages()
        stream = await self._call_stream(messages, tools_list=None, max_retries=3)

        content = ""
        tool_calls = []
        current_tc = None

        async def _inner():
            nonlocal content, tool_calls, current_tc
            tool_call_detected = False
            async for chunk in stream:
                if self._cancelled:
                    break
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.tool_calls:
                    tool_call_detected = True
                    for tc_chunk in delta.tool_calls:
                        if tc_chunk.id:
                            if current_tc:
                                tool_calls.append(current_tc)
                            current_tc = {
                                "id": tc_chunk.id, "type": "function",
                                "function": {"name": tc_chunk.function.name, "arguments": ""},
                            }
                        elif current_tc and tc_chunk.function:
                            if tc_chunk.function.name:
                                current_tc["function"]["name"] = tc_chunk.function.name
                            if tc_chunk.function.arguments:
                                current_tc["function"]["arguments"] += tc_chunk.function.arguments

                if delta.content:
                    c = delta.content
                    content += c
                    if not tool_call_detected and '<tool_call>' in content:
                        tool_call_detected = True
                    should_suppress = getattr(threading.current_thread(), 'suppress_tts_for_tool_chain', False)
                    if not should_suppress and not tool_call_detected and not getattr(self, '_is_telegram_request', False):
                        await self.push_frame(TextFrame(text=c))
                    if self.event_queue and not tool_call_detected:
                        self.event_queue.put(('chat_stream_token', {'token': c}), block=False)

        try:
            await asyncio.wait_for(_inner(), timeout=30.0)
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning("%s: Follow-up stream timed out after 30s", self.SERVICE_NAME)

        if current_tc:
            tool_calls.append(current_tc)

        if not tool_calls and content and '<tool_call>' in content:
            parsed = self._parse_text_tool_calls(content)
            if parsed:
                tool_calls = parsed
                content = re.sub(r'<tool_call>\s*.*?\s*</tool_call>', '', content, flags=re.DOTALL).strip()
                logger.info("%s: Parsed %d tool call(s) from follow-up <tool_call> text", self.SERVICE_NAME, len(tool_calls))

        return content, tool_calls

    async def _execute_chained_tools(self, tool_calls, follow_up_content):
        """Execute tool calls from a follow-up stream (tool chaining)."""
        import threading

        self._messages.append({
            "role": "assistant",
            "content": follow_up_content or None,
            "tool_calls": tool_calls,
        })

        last_user_message = ""
        for msg in reversed(self._messages):
            if msg.get("role") == "user":
                last_user_message = msg.get("content", "")
                break

        for tc in tool_calls:
            func_name = tc["function"]["name"]
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                func_args = {}

            if last_user_message:
                func_args["last_user_message"] = last_user_message

            if func_name in self._tools_dict:
                tool = self._tools_dict[func_name]
                try:
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, lambda t=tool, a=func_args: t._run(**a)
                    )
                    result_str = str(result)

                    if hasattr(threading.current_thread(), 'suppress_tts_for_tool_chain'):
                        threading.current_thread().suppress_tts_for_tool_chain = False

                    if func_name == 'send_file_to_telegram':
                        self._mark_telegram_file_sent()

                    self._messages.append({"tool_call_id": tc["id"], "role": "tool", "name": func_name, "content": result_str})
                except Exception as e:
                    logger.error("Chained tool %s failed: %s", func_name, e, exc_info=True)
                    self._messages.append({"tool_call_id": tc["id"], "role": "tool", "name": func_name, "content": f"Error: {e}"})
            else:
                self._messages.append({"tool_call_id": tc["id"], "role": "tool", "name": func_name,
                                       "content": f"Error: Tool '{func_name}' not found"})

    def _handle_follow_up_content(self, content):
        """Save follow-up content to history. Suppress TTS for file paths."""
        import threading

        if not content:
            return

        should_suppress = getattr(threading.current_thread(), 'suppress_tts_for_tool_chain', False)
        file_extensions = ('.jpg', '.png', '.pdf', '.mp4', '.mp3', '.doc', '.xls', '.jpeg', '.gif', '.webp', '.mov', '.avi')
        content_lower = content.lower().strip()
        looks_like_file_path = (
            content_lower.startswith('/') or content_lower.startswith('\\') or
            content_lower.startswith('result:') or
            any(content_lower.endswith(ext) for ext in file_extensions)
        )
        if looks_like_file_path:
            should_suppress = True

        self._messages.append({"role": "assistant", "content": content})
        if self.chat_manager:
            chat = self.chat_manager.get_current_chat()
            if chat:
                self.chat_manager.add_assistant_message(chat, content)

        if should_suppress:
            threading.current_thread().suppress_tts_for_tool_chain = False

    async def _send_done_after_tools(self):
        """Send 'Done' TTS frame and save to history. Returns True (end_frame sent)."""
        if not getattr(self, '_is_telegram_request', False):
            await self.push_frame(TextFrame(text="Done"))
        else:
            self._telegram_fallback_text = "Done"
        await self.push_frame(LLMFullResponseEndFrame())

        if self.chat_manager:
            chat = self.chat_manager.get_current_chat()
            if chat:
                self.chat_manager.add_assistant_message(chat, "Done")
        self._messages.append({"role": "assistant", "content": "Done"})
        return True

    async def _handle_empty_response(self):
        """Send fallback message when LLM returns nothing. Returns True if end_frame sent."""
        if not getattr(self, '_is_telegram_request', False):
            logger.warning("%s: Empty response for non-Telegram request", self.SERVICE_NAME)
            return False

        fallback = "I'm sorry, I didn't understand that. Could you please rephrase your question?"
        await self.push_frame(LLMFullResponseStartFrame())
        if not getattr(self, '_is_telegram_request', False):
            await self.push_frame(TextFrame(text=fallback))
        await self.push_frame(LLMFullResponseEndFrame())

        self._save_assistant_message(fallback)
        self._telegram_fallback_text = fallback
        return True

    async def _handle_generation_error(self, e):
        """Parse and handle API errors. Send user-friendly messages."""
        err_str = str(e).lower()
        error_code = getattr(e, 'status_code', None)

        is_rate_limit = any(k in err_str for k in ('429', 'rate_limit', 'rate-limited', 'insufficient_quota', 'exceeded your current quota'))

        if is_rate_limit:
            is_quota = 'insufficient_quota' in err_str or 'exceeded your current quota' in err_str
            if is_quota:
                user_msg = f"{self.SERVICE_NAME} API Quota Exceeded. Check your billing. Model: {self._model_name}"
            else:
                user_msg = f"{self.SERVICE_NAME} Rate Limit Exceeded. Model: {self._model_name}"
            await self.push_frame(ErrorFrame(error=user_msg))
            if getattr(self, '_is_telegram_request', False):
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(TextFrame(text="Sorry, the API is temporarily overloaded. Please try again in a moment."))
            if self.event_queue:
                chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                self.event_queue.put(('chat_stream_error', {'error': user_msg, 'chat_id': chat_id}), block=False)
        else:
            error_msg = str(e)
            if self.SERVICE_NAME == "OpenRouterLLMService":
                if "401" in err_str or "user not found" in err_str:
                    error_msg = "OpenRouter authentication failed (401). Check your OpenRouter key in Settings."
                elif "no endpoints found matching your data policy" in err_str:
                    error_msg = ("OpenRouter blocked this model: your privacy settings don't allow it. "
                                 "Open https://openrouter.ai/settings/privacy to adjust.")
                elif "developer instruction" in err_str:
                    error_msg = "This OpenRouter model does not support system instructions. Try another model."

            await self.push_frame(ErrorFrame(error=error_msg))
            if getattr(self, '_is_telegram_request', False):
                if 'connection' in err_str:
                    tg_err = "Sorry, I couldn't connect to the API."
                elif 'timeout' in err_str:
                    tg_err = "Sorry, the request timed out."
                else:
                    tg_err = "Sorry, something went wrong. Please try again."
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(TextFrame(text=tg_err))
            if self.event_queue:
                chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                fmt = f"Error code: {error_code} - {error_msg}" if error_code else error_msg
                self.event_queue.put(('chat_stream_error', {'error': fmt, 'chat_id': chat_id}), block=False)

    def _cleanup_generation(self, end_frame_sent, full_content="", follow_up_content=""):
        """Cleanup telegram flags, emit typing finished. Returns True if EndFrame needed."""
        self._cleanup_telegram_flags()

        if self.event_queue:
            self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
            chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
            if chat_id:
                result_text = follow_up_content or full_content or ""
                self.event_queue.put(
                    ('chat_stream_finished', {'chat_id': chat_id, 'response_text': result_text}),
                    block=False,
                )

        return not end_frame_sent

    def _check_fast_actions(self):
        """Check if the last message triggers a fast action."""
        from distr.core.agent.services.llm.fast_action_detector import detect_fast_action, ActionType
        if not self._messages:
            return None
        last_message = self._messages[-1].get("content", "")
        if not isinstance(last_message, str):
            return None
        fast_action = detect_fast_action(last_message)
        if fast_action and fast_action.action_type not in [ActionType.CONVERSATIONAL, ActionType.UNKNOWN]:
            return fast_action
        return None

    async def _generate_conversation_summary(self, conversation_messages: list) -> str:
        """Generate conversation summary using the OpenAI-compatible client."""
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
        try:
            response = await self.client.chat.completions.create(
                model=self._model_name,
                messages=[{"role": "user", "content": summary_prompt}],
                max_tokens=200,
            )
            summary = response.choices[0].message.content.strip()
            bad_words = ['tools', 'functions', 'actions', 'capabilities', 'features',
                         'f1-f12', 'function keys', 'oracle/globe', 'chatbot system']
            if summary and not any(w in summary.lower() for w in bad_words):
                return summary
        except Exception as e:
            logger.error("Error generating conversation summary via %s: %s", self.SERVICE_NAME, e, exc_info=True)

        user_messages = [m.get('content', '') for m in conversation_messages if m.get('role') == 'user']
        if user_messages:
            return f"We were talking about {user_messages[-1][:100].lower()}."
        return "We were having a conversation, but I can't provide a summary right now."

    async def _generate_welcome_summary(self, conversation_text: str, agent_name: str) -> str:
        """Generate welcome summary using the OpenAI-compatible client."""
        summary_prompt = (
            f"You are summarizing a previous conversation between you and the user.\n\n"
            f"IMPORTANT:\n"
            f"- Summarize what you and the user were TALKING ABOUT — topics, questions, stories, tasks.\n"
            f"- ALWAYS refer to the user as \"You\" (second person).\n"
            f"- ALWAYS refer to yourself as \"I\" (first person).\n\n"
            f"Conversation history:\n{conversation_text}\n\n"
            f"Provide a brief, natural summary (max 2 sentences). Just the summary:"
        )
        try:
            try:
                response = await self.client.chat.completions.create(
                    model=self._model_name,
                    messages=[{"role": "user", "content": summary_prompt}],
                    max_completion_tokens=200,
                )
            except Exception as e:
                if "max_completion_tokens" in str(e) or "unsupported_parameter" in str(e):
                    response = await self.client.chat.completions.create(
                        model=self._model_name,
                        messages=[{"role": "user", "content": summary_prompt}],
                        max_tokens=200,
                    )
                else:
                    raise
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Error generating welcome summary via %s: %s", self.SERVICE_NAME, e, exc_info=True)
            return ""

    def _validate_messages(self, messages: list) -> list:
        """Validate and fix message format for OpenAI-compatible APIs."""
        if not messages:
            return messages

        validated = []
        i = 0
        while i < len(messages):
            msg = messages[i]

            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                tool_call_ids = {tc.get('id') for tc in msg.get('tool_calls', [])}
                i += 1

                found_ids = set()
                tool_responses = []
                while i < len(messages) and messages[i].get('role') == 'tool':
                    tc_id = messages[i].get('tool_call_id')
                    if tc_id in tool_call_ids:
                        tool_responses.append(messages[i])
                        found_ids.add(tc_id)
                    i += 1

                if tool_call_ids - found_ids:
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
                    import time as _time
                    tool_name = msg.get('name', 'tool')
                    tool_content = msg.get('content', '')
                    tc_id = msg.get('tool_call_id',
                                    f"call_{int(_time.time() * 1000000)}_{hash(tool_name + str(tool_content)) % 1000000}")
                    validated.append({
                        "role": "assistant", "content": None,
                        "tool_calls": [{"id": tc_id, "type": "function",
                                        "function": {"name": tool_name, "arguments": "{}"}}],
                    })
                    fixed = msg.copy()
                    fixed['tool_call_id'] = tc_id
                    validated.append(fixed)
                else:
                    validated.append(msg)
                i += 1
            else:
                validated.append(msg)
                i += 1

        return validated
