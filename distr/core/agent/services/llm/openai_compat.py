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
        """Orchestrator: stream → tool calls → feed results back → repeat until done.

        Runs an agentic loop: the LLM can call tools, and the results are fed back
        for as many rounds as needed (like pi's agent loop). Stops when the LLM
        responds with text only (no tool calls) or hits MAX_TOOL_ROUNDS.
        """
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

            # Fast action detection — bypass LLM for simple commands
            fast_action = self._check_fast_actions()
            if fast_action:
                current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                if await self._execute_fast_action(fast_action, current_chat_id):
                    return

            await self.push_frame(LLMFullResponseStartFrame())
            current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
            if self.event_queue:
                self.event_queue.put(('typing_indicator_changed', {'show': True}), block=False)
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

            # ── Agentic tool loop: keep calling LLM while it returns tool_calls ──
            MAX_TOOL_ROUNDS = 10
            round_num = 0

            # If the LLM jumped straight to tool calls with no acknowledgment text,
            # speak a brief acknowledgment so the user isn't left in silence during
            # what could be a long chain. (If the LLM already wrote text before
            # the tool calls, that was already streamed in _consume_stream above.)
            if tool_calls and not full_content.strip():
                self._speak_acknowledgment()
                # The acknowledgment's EndFrame closes the TTS transport session.
                # Push a new StartFrame so follow-up text (after tool execution)
                # is recognized as a new response by the transport/pipeline.
                await self.push_frame(LLMFullResponseStartFrame())

            while tool_calls and round_num < MAX_TOOL_ROUNDS:
                round_num += 1
                logger.info("%s: Tool round %d/%d — executing %d tool call(s)",
                            self.SERVICE_NAME, round_num, MAX_TOOL_ROUNDS, len(tool_calls))

                # Save the assistant message with tool_calls
                self._messages.append({
                    "role": "assistant",
                    "content": full_content or None,
                    "tool_calls": tool_calls,
                })

                # Execute all tool calls from this round
                if round_num == 1:
                    await self._execute_tool_calls_with_chaining(tool_calls)
                else:
                    await self._execute_chained_tools(tool_calls, full_content)

                # Auto-send file to Telegram if applicable
                auto_sent = await self._auto_send_file_to_telegram()

                # After round 1: check if this is a multi-step chain that should
                # run in the background, freeing the main conversation.
                if round_num == 1 and self._should_dispatch_to_background(tool_calls):
                    # Speak a descriptive acknowledgment if the LLM hasn't already
                    if not full_content.strip():
                        self._speak_acknowledgment()
                    dispatched = await self._dispatch_chain_to_background(
                        full_content, tools_list, current_chat_id
                    )
                    if dispatched:
                        # Main generation is done — background chain takes over
                        end_frame_sent = True
                        tool_calls = []  # Clear so final handling knows we're done
                        break

                # Feed tool results back to the LLM for the next round
                # Pass tools so the LLM can keep calling tools if needed
                follow_up_content, follow_up_tool_calls = await self._process_follow_up(tools_list=tools_list)
                if not follow_up_tool_calls:
                    await self._auto_send_file_to_telegram()

                # Update for next loop iteration
                full_content = follow_up_content or ""
                tool_calls = follow_up_tool_calls or []

                # If the LLM returned text but no more tool calls, we're done
                if not tool_calls and full_content:
                    self._handle_follow_up_content(full_content)
                    break

                # If LLM returned neither text nor tool calls, we're done
                if not tool_calls and not full_content:
                    end_frame_sent = await self._send_done_after_tools()
                    break

            # Log if we hit the limit
            if round_num >= MAX_TOOL_ROUNDS and tool_calls:
                logger.warning("%s: Hit MAX_TOOL_ROUNDS (%d) — stopping tool loop with %d pending tool calls",
                               self.SERVICE_NAME, MAX_TOOL_ROUNDS, len(tool_calls))

            # ── Final handling (no more tool calls) ──
            if not tool_calls:
                if full_content and full_content.strip() and round_num == 0:
                    # Round 0 = LLM returned text only, no tools at all
                    self._save_assistant_message(full_content)
                elif not end_frame_sent and round_num > 0:
                    # Tool loop finished, make sure we send Done
                    end_frame_sent = await self._send_done_after_tools()
                elif not end_frame_sent:
                    end_frame_sent = await self._handle_empty_response()

            elif full_content and full_content.strip():
                self._save_assistant_message(full_content)

        except asyncio.CancelledError:
            logger.warning("%s: _generate_response cancelled (%.3fs)", self.SERVICE_NAME, _time.time() - _t0)
            # Don't emit telegram response for cancelled tasks —
            # a new generation is about to start and will handle its own telegram state.
            # But DO stop the typing indicator so Telegram doesn't get stuck.
            if self.event_queue:
                self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
            return
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
                matched = self._fuzzy_match_tool(func_name) if hasattr(self, '_fuzzy_match_tool') else None
                if matched:
                    try:
                        result = matched._run(**json.loads(tc["function"].get("arguments", "{}")))
                        status = "completed"
                    except Exception as e:
                        result = f"Error: {e}"
                        status = "failed"
                    chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                    from distr.core.agent.tool_audit import record_tool_execution
                    record_tool_execution(chat_id, matched.name, str(result), status, event_queue=self.event_queue)
                    resp = {"tool_call_id": tc["id"], "role": "tool", "name": matched.name, "content": str(result)}
                else:
                    resp = {"tool_call_id": tc["id"], "role": "tool", "name": func_name,
                            "content": f"Error: Tool '{func_name}' not found"}

            self._messages.append(resp)

    async def _process_follow_up(self, tools_list=None):
        """Make a follow-up API call after tool execution. Returns (content, tool_calls).
        
        If tools_list is provided, the LLM can call more tools in the next round.
        """
        import threading

        messages = self._prepare_api_messages()
        stream = await self._call_stream(messages, tools_list=tools_list, max_retries=3)

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

    def _speak_acknowledgment(self):
        """Speak a brief acknowledgment when the LLM jumps straight to tool calls
        without any preceding text. Prevents the user sitting in silence during
        a long tool chain.

        The LLM may already have written acknowledgment text before the tool calls
        (which was streamed in _consume_stream). This method only fires when the
        LLM went straight to tool calls with no text at all.
        """
        import threading
        if getattr(self, '_is_telegram_request', False) or not self._speaker_enabled:
            return

        # Don't acknowledge if TTS is already suppressed (e.g. nested chain)
        if getattr(threading.current_thread(), 'suppress_tts_for_tool_chain', False):
            return

        try:
            from distr.core.signals import speak_text_directly_event_queue
            speak_text_directly_event_queue("On it.")
        except Exception:
            pass

    def _should_dispatch_to_background(self, tool_calls: list) -> bool:
        """Determine if a tool chain should run in the background.

        Multi-step chains (screenshot → save → attach, etc.) should run in the
        background so the user can continue the conversation. Single fast tool
        calls stay inline.

        Triggers:
        - Any tool returned [ACTION REQUIRED] (explicit chain signal)
        - Tools known to produce multi-step chains (screenshot_analyzer with
          capture_only, pi_agent)

        NOT dispatched to background for Telegram requests (response must go
        back to the Telegram API synchronously).
        """
        # Telegram requests need synchronous response — don't dispatch
        if getattr(self, '_is_telegram_request', False):
            return False
        # Check for [ACTION REQUIRED] in tool results — definitive chain signal
        for msg in self._messages:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if "[ACTION REQUIRED" in content:
                    return True

        # Check tool names for chain-prone tools
        from distr.core.agent.services.llm.background_chain import BackgroundChainRunner
        if BackgroundChainRunner.is_multi_tool_chain(tool_calls):
            return True

        return False

    async def _dispatch_chain_to_background(self, initial_content: str,
                                             tools_list: list,
                                             chat_id: int = None) -> bool:
        """Dispatch the remaining tool chain to a background task.

        Takes a snapshot of current messages so the background chain works
        in isolation. The main generation returns (user can keep talking).
        When the background chain completes, it announces the result via TTS.

        Returns True if dispatch succeeded, False if it should stay inline.
        """
        from distr.core.agent.services.llm.background_chain import BackgroundChainRunner

        # Cancel any existing background chain
        if hasattr(self, '_background_chain') and self._background_chain:
            self._background_chain.cancel()

        # Take a snapshot of messages for the background chain
        messages_snapshot = list(self._messages)

        runner = BackgroundChainRunner(
            service=self,
            messages_snapshot=messages_snapshot,
            tools_list=tools_list,
            chat_id=chat_id,
            event_queue=self.event_queue,
        )
        self._background_chain = runner

        logger.info("%s: Dispatching tool chain to background (round 1 done inline)",
                    self.SERVICE_NAME)

        # Keep typing indicator on so the user knows work is happening
        if self.event_queue:
            self.event_queue.put(('typing_indicator_changed', {'show': True}), block=False)

        # Create the background task
        runner.task = asyncio.create_task(
            self._run_background_chain(runner, initial_content),
            name=f"{self.SERVICE_NAME}_background_chain"
        )

        return True

    async def _run_background_chain(self, runner: 'BackgroundChainRunner',
                                     initial_content: str):
        """Wrapper that runs a BackgroundChainRunner and handles completion/cleanup."""
        try:
            await runner.run(initial_content=initial_content, initial_tool_results=[])
        except asyncio.CancelledError:
            logger.info("%s: Background chain cancelled", self.SERVICE_NAME)
        except Exception as e:
            logger.error("%s: Background chain error: %s", self.SERVICE_NAME, e, exc_info=True)
            # Announce error via TTS
            if getattr(self, '_is_telegram_request', False) or not self._speaker_enabled:
                return
            try:
                from distr.core.signals import speak_text_directly_event_queue
                speak_text_directly_event_queue("Sorry, something went wrong with that task.")
            except Exception:
                pass
        finally:
            # Clear the reference
            if hasattr(self, '_background_chain') and self._background_chain is runner:
                self._background_chain = None

    def _handle_follow_up_content(self, content):
        """Save follow-up content to history and speak it via TTS.

        TTS is suppressed only for raw file paths (e.g. tool returned a path
        like '/Users/paul/file.mp4') — not for normal conversational responses
        that happen to follow a tool call.
        """
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
                from distr.core.agent.services.llm.text_utils import clean_text_for_tts
                self.chat_manager.add_assistant_message(chat, clean_text_for_tts(content))

        # For Telegram requests, store as fallback since TextFrames aren't pushed to TTS
        if getattr(self, '_is_telegram_request', False):
            self._telegram_fallback_text = content

        # Push to TTS pipeline so the user hears the follow-up response.
        # Only suppress for raw file paths — normal conversational follow-ups
        # after tool calls (e.g. "Here's the transcription summary...") should be spoken.
        if not should_suppress and self._speaker_enabled and not getattr(self, '_is_telegram_request', False):
            import asyncio
            from distr.core.agent.services.llm.text_utils import clean_text_for_tts
            cleaned = clean_text_for_tts(content)
            if cleaned and cleaned.strip():
                try:
                    asyncio.ensure_future(self.push_frame(LLMFullResponseStartFrame()))
                    asyncio.ensure_future(self.push_frame(TextFrame(text=cleaned)))
                    asyncio.ensure_future(self.push_frame(LLMFullResponseEndFrame()))
                except Exception as e:
                    logger.debug("Could not push follow-up TTS frame: %s", e)

        if should_suppress:
            threading.current_thread().suppress_tts_for_tool_chain = False

    async def _send_done_after_tools(self):
        """Send TTS response after tool execution, using tool results when meaningful. Returns True (end_frame sent)."""
        import json as _json

        # Check if the last tool result requested silence (e.g. open_page returns {"silent": True})
        is_silent = False
        tool_result_text = None
        for msg in reversed(self._messages):
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if content:
                    try:
                        parsed = _json.loads(content)
                        if isinstance(parsed, dict) and parsed.get("silent"):
                            is_silent = True
                    except (ValueError, TypeError):
                        pass
                # Collect the last meaningful tool result for TTS/history
                if content and len(content) > 5 and "[ACTION REQUIRED" not in content:
                    tool_result_text = content[:2000]  # Cap at 2000 chars
                break  # Only check the most recent tool result

        if getattr(self, '_is_telegram_request', False):
            # For Telegram, include the tool result as context
            fallback = tool_result_text or "Done"
            self._telegram_fallback_text = fallback
        elif is_silent:
            fallback = "Done"
        elif tool_result_text:
            # We have a meaningful tool result — use it as the response instead of "Done"
            # so the user actually knows what happened.
            # Truncate long results for TTS, keeping the full text for history.
            tts_text = tool_result_text
            if len(tts_text) > 500:
                # For very long results, speak a brief summary
                first_line = tts_text.split('\n')[0]
                if len(first_line) > 200:
                    tts_text = first_line[:200] + "..."
                else:
                    line_count = tts_text.count('\n') + 1
                    tts_text = f"{first_line} ... and {line_count - 1} more lines."
            from distr.core.agent.libs import LLMFullResponseStartFrame
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(TextFrame(text=tts_text))
            fallback = tool_result_text
        else:
            from distr.core.agent.libs import LLMFullResponseStartFrame
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(TextFrame(text="Done"))
            fallback = "Done"

        await self.push_frame(LLMFullResponseEndFrame())

        if self.chat_manager:
            chat = self.chat_manager.get_current_chat()
            if chat:
                self.chat_manager.add_assistant_message(chat, fallback)
        self._messages.append({"role": "assistant", "content": fallback})
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

        # Don't turn off typing indicator if a background chain is still running
        bg_running = hasattr(self, '_background_chain') and self._background_chain and (
            self._background_chain.task and not self._background_chain.task.done()
        )

        if self.event_queue:
            if not bg_running:
                self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
            chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
            if chat_id:
                result_text = follow_up_content or full_content or ""
                self.event_queue.put(
                    ('chat_stream_finished', {'chat_id': chat_id, 'response_text': result_text}),
                    block=False,
                )

        return not end_frame_sent

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
