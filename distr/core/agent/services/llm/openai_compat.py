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

from distr.core.agent.libs import (
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
)
from distr.core.agent.services.llm.tool_format import convert_tools_to_openai_format
from distr.core.agent.services.llm.computer_use_guard import build_computer_use_execution_decisions
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
    _TOOL_EXECUTION_TIMEOUT_SEC = 90.0
    _tool_execution_in_progress = False

    async def _run_tool_with_timeout(self, tool, func_args: dict, func_name: str):
        """Run a blocking tool in the executor with a hard timeout."""
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, lambda t=tool, a=func_args: t._run(**a)),
                timeout=self._TOOL_EXECUTION_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Tool '{func_name}' timed out after {self._TOOL_EXECUTION_TIMEOUT_SEC:.0f} seconds"
            ) from exc

    async def _push_pipeline_frame(self, frame):
        """Push a frame through the active pipeline direction."""
        await self.push_frame(frame, getattr(self, "_pipeline_direction", None))

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

            await self._push_pipeline_frame(LLMFullResponseStartFrame())
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
                if self._should_speak_acknowledgment(last_user_msg, tool_calls):
                    from distr.core.agent.tool_audio_timing import wait_before_tool_side_effects

                    ack = "Working on it."
                    await self._push_pipeline_frame(TextFrame(text=ack))
                    await self._push_pipeline_frame(LLMFullResponseEndFrame())
                    await wait_before_tool_side_effects(self, ack, end_current_utterance=False)
                else:
                    self._speak_acknowledgment(last_user_msg, tool_calls)
                # The acknowledgment's EndFrame closes the TTS transport session.
                # Push a new StartFrame so follow-up text (after tool execution)
                # is recognized as a new response by the transport/pipeline.
                await self._push_pipeline_frame(LLMFullResponseStartFrame())
            elif tool_calls and full_content.strip():
                from distr.core.agent.tool_audio_timing import wait_before_tool_side_effects

                await wait_before_tool_side_effects(self, full_content.strip())

            while tool_calls and round_num < MAX_TOOL_ROUNDS:
                round_num += 1
                logger.info("%s: Tool round %d/%d — executing %d tool call(s)",
                            self.SERVICE_NAME, round_num, MAX_TOOL_ROUNDS, len(tool_calls))

                # Save the assistant message with tool_calls
                tool_calls = self._sanitize_tool_calls(tool_calls)
                self._messages.append({
                    "role": "assistant",
                    "content": full_content or None,
                    "tool_calls": tool_calls,
                })

                # Execute all tool calls from this round
                self._tool_execution_in_progress = True
                try:
                    if round_num == 1:
                        await self._execute_tool_calls_with_chaining(tool_calls)
                    else:
                        await self._execute_chained_tools(tool_calls, full_content)
                finally:
                    self._tool_execution_in_progress = False

                # Auto-send file to Telegram if applicable
                auto_sent = await self._auto_send_file_to_telegram()

                # After round 1: check if this is a multi-step chain that should
                # run in the background, freeing the main conversation.
                if round_num == 1 and self._should_dispatch_to_background(tool_calls):
                    # Speak a descriptive acknowledgment if the LLM hasn't already
                    if not full_content.strip():
                        self._speak_acknowledgment(last_user_msg, tool_calls)
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
                    # Follow-up already pushes Start/Text/End for TTS — do not call
                    # _send_done_after_tools() afterwards or the user hears an extra \"Done\".
                    end_frame_sent = True
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
                await self._push_pipeline_frame(LLMFullResponseEndFrame())

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
        messages = self._apply_response_style_overrides(messages)
        if len(messages) > 35:
            logger.warning("Truncating conversation history from %d to 35 messages", len(messages))
            system = messages[0] if messages and messages[0].get('role') == 'system' else None
            recent = messages[-34:] if system else messages[-35:]
            messages = ([system] + recent) if system else recent
            self._messages = list(messages)
        return self._validate_messages(messages)

    def _apply_response_style_overrides(self, messages: list) -> list:
        """Apply lightweight style constraints based on explicit user wording.

        This helps instruction fidelity for requests like "quick summary"
        without changing normal responses.
        """
        last_user_msg = ""
        for msg in reversed(self._messages):
            if msg.get("role") == "user":
                last_user_msg = str(msg.get("content", "") or "")
                break

        text = last_user_msg.lower()
        wants_brief = (
            "quick" in text
            or "brief" in text
            or "short" in text
            or "concise" in text
            or "just an outline" in text
            or "outline summary" in text
            or "keep it short" in text
        )
        if not wants_brief:
            return messages

        brevity_instruction = (
            "Response style override from user instruction: be concise. "
            "Keep the reply to 3-5 bullets or <= 90 words, no preamble."
        )
        patched = list(messages)
        if patched and patched[0].get("role") == "system":
            current = str(patched[0].get("content", "") or "")
            if brevity_instruction not in current:
                patched[0] = {**patched[0], "content": f"{current}\n\n{brevity_instruction}"}
            return patched

        patched.insert(0, {"role": "system", "content": brevity_instruction})
        return patched

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

                if (
                    self.SERVICE_NAME == "NvidiaLLMService"
                    and use_tools
                    and "400" in err
                    and any(
                        token in err
                        for token in (
                            "unterminated string",
                            "property name enclosed in double quotes",
                            "badrequest",
                            "invalid json",
                        )
                    )
                ):
                    logger.warning(
                        "%s: malformed tool payload rejected — retrying without tools",
                        self.SERVICE_NAME,
                    )
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

                    display_chunk = delta.content
                    if not tool_call_detected:
                        from distr.core.agent.services.llm.text_utils import clean_model_text_for_chat
                        display_chunk = clean_model_text_for_chat(delta.content, strip_whitespace=False)

                    if self._speaker_enabled and not tool_call_detected and not getattr(self, '_is_telegram_request', False):
                        from distr.core.agent.services.llm.text_utils import clean_text_for_tts
                        # Keep boundary whitespace in streamed deltas; stripping each chunk
                        # can merge words across chunks (e.g. "step " + "one" -> "stepone").
                        tts_chunk = clean_text_for_tts(delta.content, strip_whitespace=False)
                        if tts_chunk:
                            await self._push_pipeline_frame(TextFrame(text=tts_chunk))
                    if self.event_queue and not tool_call_detected and display_chunk:
                        self.event_queue.put(('chat_stream_token', {'token': display_chunk}), block=False)

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

        from distr.core.agent.services.llm.text_utils import clean_model_text_for_chat

        return clean_model_text_for_chat(full_content), self._sanitize_tool_calls(tool_calls)

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

        decisions = build_computer_use_execution_decisions(tool_calls)
        for idx, tc in enumerate(tool_calls):
            func_name = tc["function"]["name"]
            decision = decisions[idx] if idx < len(decisions) else {"allow": True, "reason": "ok"}
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                func_args = {}

            logger.info("🔧 Tool: %s", func_name)
            if last_user_message:
                func_args["last_user_message"] = last_user_message
            if getattr(self, '_is_telegram_request', False):
                func_args.setdefault("is_telegram_request", True)

            # Hard guard: prevent generic mouse fallback when a recent visual-target
            # flow already reported target-not-found / unresolved coordinates.
            if func_name == "mouse_movement":
                recent_tool_msgs = [
                    m for m in reversed(self._messages[-12:])
                    if m.get("role") == "tool"
                ]
                blocked = False
                for m in recent_tool_msgs:
                    name = str(m.get("name", ""))
                    content = str(m.get("content", ""))
                    if name == "screenshot_analyzer" and (
                        "TARGET NOT FOUND" in content
                        or "No action executed because execute_action is false." in content
                        or "[ACTION REQUIRED] Do NOT call mouse_movement" in content
                    ):
                        blocked = True
                        break
                if blocked:
                    result_str = (
                        "Blocked unsafe fallback: visual target was not resolved yet. "
                        "Do NOT use mouse_movement for this step; retry with "
                        "accessibility tree or screenshot_analyzer."
                    )
                    resp = {"tool_call_id": tc["id"], "role": "tool", "name": func_name, "content": result_str}
                    self._messages.append(resp)
                    continue

            if not decision.get("allow", True):
                result_str = (
                    "Skipped by computer-use guard: only one actioning computer-use step "
                    "is executed per round. Re-run next step after observing updated context."
                )
                resp = {"tool_call_id": tc["id"], "role": "tool", "name": func_name, "content": result_str}
                self._messages.append(resp)
                continue

            if func_name in self._tools_dict:
                tool = self._tools_dict[func_name]
                status = "completed"
                try:
                    result = await self._run_tool_with_timeout(tool, func_args, func_name)
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
                    # Do not stream follow-up content directly to TTS here.
                    # Final TTS is handled once in _handle_follow_up_content()
                    # to avoid duplicate speech for tool follow-ups.
                    if self.event_queue and not tool_call_detected:
                        from distr.core.agent.services.llm.text_utils import clean_model_text_for_chat
                        display_chunk = clean_model_text_for_chat(c, strip_whitespace=False)
                        if display_chunk:
                            self.event_queue.put(('chat_stream_token', {'token': display_chunk}), block=False)

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

        tool_calls = self._sanitize_tool_calls(tool_calls)
        return content, tool_calls

    async def _execute_chained_tools(self, tool_calls, follow_up_content):
        """Execute tool calls from a follow-up stream (tool chaining)."""
        import threading

        tool_calls = self._sanitize_tool_calls(tool_calls)
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

        decisions = build_computer_use_execution_decisions(tool_calls)
        for idx, tc in enumerate(tool_calls):
            func_name = tc["function"]["name"]
            decision = decisions[idx] if idx < len(decisions) else {"allow": True, "reason": "ok"}
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                func_args = {}

            if last_user_message:
                func_args["last_user_message"] = last_user_message
            if getattr(self, '_is_telegram_request', False):
                func_args.setdefault("is_telegram_request", True)

            if not decision.get("allow", True):
                self._messages.append({
                    "tool_call_id": tc["id"],
                    "role": "tool",
                    "name": func_name,
                    "content": (
                        "Skipped by computer-use guard: only one actioning computer-use step "
                        "is executed per round. Re-run next step after observing updated context."
                    ),
                })
                continue

            if func_name in self._tools_dict:
                tool = self._tools_dict[func_name]
                try:
                    result = await self._run_tool_with_timeout(tool, func_args, func_name)
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

    @staticmethod
    def _should_speak_acknowledgment(last_user_message: str, tool_calls: list) -> bool:
        """Return True when a brief progress acknowledgment is actually useful.

        Heuristic:
        - Avoid repetitive acknowledgments for quick/direct commands.
        - Use acknowledgments for likely long-running requests so users are not left
          in silence while background work begins.
        """
        if not tool_calls:
            return False

        message = (last_user_message or "").strip().lower()
        words = [w for w in re.split(r"\s+", message) if w]

        # Short direct commands ("open chrome", "paste", "click here") should not
        # get an extra spoken preamble.
        if len(words) <= 5:
            return False

        long_running_tools = {
            "pi_agent",
            "run_workflow",
            "continue_workflow",
            "playwright_browser",
            "screenshot_analyzer",
        }
        called_names = {
            tc.get("function", {}).get("name", "")
            for tc in tool_calls
            if isinstance(tc, dict)
        }
        if called_names & long_running_tools:
            return True

        long_running_intent_markers = (
            "investigate",
            "analyze",
            "debug",
            "why",
            "find out",
            "figure out",
            "look into",
            "trace",
            "diagnose",
        )
        return any(marker in message for marker in long_running_intent_markers)

    def _speak_acknowledgment(self, last_user_message: str = "", tool_calls: list | None = None):
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

        if not self._should_speak_acknowledgment(last_user_message, tool_calls or []):
            return

        try:
            from distr.core.signals import speak_text_directly_event_queue
            speak_text_directly_event_queue("Working on it.")
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

        Chat/history store redacted paths (see ``redact_filesystem_paths_for_conversation``).
        TTS uses ``clean_text_for_tts`` which also redacts paths. Only the explicit tool-chain
        suppress flag skips speech here.
        """
        import threading

        if not content:
            return

        should_suppress = getattr(threading.current_thread(), 'suppress_tts_for_tool_chain', False)

        from distr.core.agent.services.llm.text_utils import (
            clean_model_text_for_chat,
            clean_text_for_tts,
        )

        display = clean_model_text_for_chat(content)
        self._messages.append({"role": "assistant", "content": display})
        if self.chat_manager:
            chat = self.chat_manager.get_current_chat()
            if chat:
                self.chat_manager.add_assistant_message(chat, display)

        # For Telegram requests, store as fallback since TextFrames aren't pushed to TTS
        if getattr(self, '_is_telegram_request', False):
            self._telegram_fallback_text = display

        # Push to TTS pipeline so the user hears the follow-up response.
        # Only suppress for raw file paths — normal conversational follow-ups
        # after tool calls (e.g. "Here's the transcription summary...") should be spoken.
        if not should_suppress and self._speaker_enabled and not getattr(self, '_is_telegram_request', False):
            import asyncio
            cleaned = clean_text_for_tts(content)
            if cleaned and cleaned.strip():
                try:
                    asyncio.ensure_future(self._push_pipeline_frame(LLMFullResponseStartFrame()))
                    asyncio.ensure_future(self._push_pipeline_frame(TextFrame(text=cleaned)))
                    asyncio.ensure_future(self._push_pipeline_frame(LLMFullResponseEndFrame()))
                except Exception as e:
                    logger.debug("Could not push follow-up TTS frame: %s", e)

        if should_suppress:
            threading.current_thread().suppress_tts_for_tool_chain = False

    async def _send_done_after_tools(self):
        """Send TTS response after tool execution, using tool results when meaningful. Returns True (end_frame sent)."""
        import json as _json

        from distr.core.agent.services.llm.text_utils import (
            brief_tool_completion_message,
            humanize_silent_navigation_json,
        )

        # Check if the last tool result requested silence (e.g. legacy open_page JSON with silent)
        is_silent = False
        tool_result_text = None
        last_tool_name = ""
        action_tool_spoke_directly = False
        action_ack_prefixes = ("running action ", "action stopped", "paused", "resumed", "done")
        action_tool_names = {"play_action", "stop_action", "pause_action", "resume_action"}
        for msg in reversed(self._messages):
            if msg.get("role") == "tool":
                tool_name = (msg.get("name") or "").strip()
                last_tool_name = tool_name
                content = msg.get("content", "")
                effective = content
                if content:
                    human_nav = humanize_silent_navigation_json(content)
                    if human_nav:
                        effective = human_nav
                    else:
                        try:
                            parsed = _json.loads(content)
                            if isinstance(parsed, dict) and parsed.get("silent"):
                                is_silent = True
                        except (ValueError, TypeError):
                            pass
                # Some action tools already announce status via speak_text_directly_event_queue.
                # Suppress LLM fallback speech for those acknowledgements to avoid repeats.
                if tool_name in action_tool_names and isinstance(content, str):
                    lowered = content.strip().lower()
                    if lowered.startswith(action_ack_prefixes):
                        action_tool_spoke_directly = True
                # Collect the last meaningful tool result for TTS/history
                eff_str = str(effective).strip() if effective else ""
                if (
                    eff_str
                    and "[ACTION REQUIRED" not in eff_str
                    and not eff_str.lower().startswith("error:")
                ):
                    tool_result_text = eff_str[:2000]  # Cap at 2000 chars
                break  # Only check the most recent tool result

        generic_ack = brief_tool_completion_message(last_tool_name)

        # Fast-action screenshot already saved/spoke a user-facing reply — do not
        # append a second assistant line or replay TTS from the raw tool payload.
        if last_tool_name == "screenshot_analyzer":
            for msg in reversed(self._messages):
                role = msg.get("role")
                if role == "assistant" and (msg.get("content") or "").strip():
                    await self._push_pipeline_frame(LLMFullResponseStartFrame())
                    await self._push_pipeline_frame(LLMFullResponseEndFrame())
                    return True
                if role == "tool":
                    break

        if getattr(self, '_is_telegram_request', False):
            # For Telegram, include the tool result as context
            fallback = tool_result_text or generic_ack
            self._telegram_fallback_text = fallback
        elif action_tool_spoke_directly:
            # Tool already spoke via speak_text_directly_event_queue -> command handler
            # and pushed its own Start/Text/End TTS frames. Do not emit extra frames
            # from the LLM fallback path, or we can suppress/close the player UI.
            fallback = tool_result_text or generic_ack
            if self.chat_manager:
                chat = self.chat_manager.get_current_chat()
                if chat:
                    self.chat_manager.add_assistant_message(chat, fallback)
            self._messages.append({"role": "assistant", "content": fallback})
            return True
        elif is_silent:
            # Tool asked for no voice UI — close the segment without speaking \"Done\".
            await self._push_pipeline_frame(LLMFullResponseStartFrame())
            await self._push_pipeline_frame(LLMFullResponseEndFrame())
            return True
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
            await self._push_pipeline_frame(LLMFullResponseStartFrame())
            await self._push_pipeline_frame(TextFrame(text=tts_text))
            fallback = tool_result_text
        else:
            await self._push_pipeline_frame(LLMFullResponseStartFrame())
            await self._push_pipeline_frame(TextFrame(text=generic_ack))
            fallback = generic_ack

        await self._push_pipeline_frame(LLMFullResponseEndFrame())

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
        await self._push_pipeline_frame(LLMFullResponseStartFrame())
        if not getattr(self, '_is_telegram_request', False):
            await self._push_pipeline_frame(TextFrame(text=fallback))
        await self._push_pipeline_frame(LLMFullResponseEndFrame())

        self._save_assistant_message(fallback)
        self._telegram_fallback_text = fallback
        return True

    async def _handle_generation_error(self, e):
        """Parse and handle API errors. Send user-friendly messages."""
        await self._surface_model_error(e, operation="generate a response")

    def _cleanup_generation(self, end_frame_sent, full_content="", follow_up_content=""):
        """Cleanup telegram flags, emit typing finished. Returns True if EndFrame needed."""
        self._cleanup_telegram_flags()

        automation_run_id = getattr(self, '_automation_subagent_run_id', None)
        if automation_run_id is not None:
            try:
                from distr.core.automation_subagent import finalize_automation_subagent_from_agent
                from distr.core.agent.services.llm.text_utils import clean_model_text_for_chat

                automation_name = getattr(self, '_automation_subagent_name', None) or 'Automation'
                result_text = clean_model_text_for_chat(follow_up_content or full_content or "")
                finalize_automation_subagent_from_agent(
                    int(automation_run_id),
                    automation_name=automation_name,
                    success=bool((result_text or '').strip()),
                    summary=(result_text or '').strip() or 'Automation finished with no reply.',
                    speech_text=(result_text or '').strip(),
                )
            except Exception as exc:
                logger.debug("Automation subagent finalize failed: %s", exc, exc_info=True)
            finally:
                self._automation_subagent_run_id = None
                self._automation_subagent_name = None

        # Don't turn off typing indicator if a background chain is still running
        bg_running = hasattr(self, '_background_chain') and self._background_chain and (
            self._background_chain.task and not self._background_chain.task.done()
        )

        if self.event_queue:
            if not bg_running:
                self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
            chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
            if chat_id:
                from distr.core.agent.services.llm.text_utils import (
                    clean_model_text_for_chat,
                )

                result_text = clean_model_text_for_chat(
                    follow_up_content or full_content or ""
                )
                self.event_queue.put(
                    ('chat_stream_finished', {'chat_id': chat_id, 'response_text': result_text}),
                    block=False,
                )

        return not end_frame_sent

    def _extract_chat_completion_text(self, response, *, log_context: str = "") -> str:
        """Return assistant text from a non-streaming chat completion, or '' if missing."""
        choices = getattr(response, "choices", None) or []
        if not choices:
            if log_context:
                logger.warning(
                    "%s via %s: empty choices (model=%s)",
                    log_context,
                    self.SERVICE_NAME,
                    self._model_name,
                )
            return ""

        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            if log_context:
                logger.warning(
                    "%s via %s: missing message (model=%s, finish_reason=%s)",
                    log_context,
                    self.SERVICE_NAME,
                    self._model_name,
                    getattr(choice, "finish_reason", None),
                )
            return ""

        content = getattr(message, "content", None)
        if content is not None:
            text = str(content).strip()
            if text:
                return text

        refusal = getattr(message, "refusal", None)
        if refusal:
            return str(refusal).strip()

        if log_context:
            logger.warning(
                "%s via %s: null message content (model=%s, finish_reason=%s)",
                log_context,
                self.SERVICE_NAME,
                self._model_name,
                getattr(choice, "finish_reason", None),
            )
        return ""

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
            summary = self._extract_chat_completion_text(
                response, log_context="Conversation summary"
            )
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
            used_max_completion_tokens = True
            try:
                response = await self.client.chat.completions.create(
                    model=self._model_name,
                    messages=[{"role": "user", "content": summary_prompt}],
                    max_completion_tokens=200,
                )
            except Exception as e:
                if "max_completion_tokens" in str(e) or "unsupported_parameter" in str(e):
                    used_max_completion_tokens = False
                    response = await self.client.chat.completions.create(
                        model=self._model_name,
                        messages=[{"role": "user", "content": summary_prompt}],
                        max_tokens=200,
                    )
                else:
                    raise
            summary = self._extract_chat_completion_text(
                response, log_context="Welcome summary"
            )
            if not summary and used_max_completion_tokens:
                response = await self.client.chat.completions.create(
                    model=self._model_name,
                    messages=[{"role": "user", "content": summary_prompt}],
                    max_tokens=200,
                )
                summary = self._extract_chat_completion_text(response)
            return summary
        except Exception as e:
            logger.error("Error generating welcome summary via %s: %s", self.SERVICE_NAME, e, exc_info=True)
            return ""

    @staticmethod
    def _sanitize_tool_calls(tool_calls: list | None) -> list:
        """Ensure tool-call argument strings are valid JSON before API replay."""
        if not tool_calls:
            return []

        sanitized = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function") or {}
            name = (func.get("name") or "").strip()
            if not name:
                continue

            raw_args = func.get("arguments", "")
            if raw_args is None:
                raw_args = ""
            if isinstance(raw_args, dict):
                args_obj = raw_args
            else:
                raw_text = str(raw_args).strip()
                if not raw_text:
                    args_obj = {}
                else:
                    try:
                        args_obj = json.loads(raw_text)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(
                            "Dropping malformed tool call arguments for %s: %r",
                            name,
                            raw_text[:120],
                        )
                        args_obj = {}

            if not isinstance(args_obj, dict):
                args_obj = {}

            tc_id = tc.get("id")
            if not tc_id:
                tc_id = f"call_{int(time.time() * 1000000)}_{hash(name) % 1000000}"

            sanitized.append({
                "id": tc_id,
                "type": tc.get("type") or "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args_obj, ensure_ascii=False),
                },
            })
        return sanitized

    def _validate_messages(self, messages: list) -> list:
        """Validate and fix message format for OpenAI-compatible APIs."""
        if not messages:
            return messages

        validated = []
        i = 0
        while i < len(messages):
            msg = messages[i]

            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                msg = {
                    **msg,
                    "tool_calls": self._sanitize_tool_calls(msg.get("tool_calls")),
                }
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
