"""
Background Chain Runner — executes multi-step tool chains off the main conversation.

When the LLM calls tools that produce [ACTION REQUIRED] (indicating a chain),
the first round executes inline. If the chain needs more rounds, we dispatch
the remaining work to a background asyncio task. This frees the main conversation
so the user can continue talking while the chain runs.

Architecture:
  1. _generate_response() executes round 1 inline (fast — just tool execution)
  2. If [ACTION REQUIRED] detected → dispatch remaining rounds to BackgroundChainRunner
  3. Main generation returns immediately (user can talk again)
  4. BackgroundChainRunner runs the tool loop in isolation
  5. On completion: announces result via TTS, appends to chat history
  6. On cancellation: stops cleanly, no partial state

The runner works on a COPY of messages — it does not mutate self._messages during
execution. Results are only merged back when the chain completes successfully.
"""

import asyncio
import json
import logging
import threading
import time
from typing import Optional, Any

logger = logging.getLogger(__name__)


class BackgroundChainRunner:
    """Runs a multi-step tool chain in the background, isolated from the main conversation."""

    MAX_ROUNDS = 10

    def __init__(self, service, messages_snapshot: list, tools_list: list,
                 chat_id: Optional[int] = None, event_queue=None):
        """
        Args:
            service: The LLM service instance (for tool access, API calls, etc.)
            messages_snapshot: Copy of the message history at the time of dispatch
            tools_list: OpenAI-format tool definitions for LLM follow-up calls
            chat_id: Current chat ID for audit/history
            event_queue: Event queue for UI updates
        """
        self.service = service
        self.messages = messages_snapshot  # Our own copy — we mutate this
        self.tools_list = tools_list
        self.chat_id = chat_id
        self.event_queue = event_queue
        self.task: Optional[asyncio.Task] = None
        self.cancelled = False
        self._round_num = 0

    async def run(self, initial_content: str, initial_tool_results: list):
        """Execute the chain starting from round 2 (round 1 already ran inline).

        Args:
            initial_content: The LLM's initial response text (may be empty)
            initial_tool_results: List of tool result messages from round 1
        """
        self._round_num = 1  # Round 1 was inline

        # Tool results from round 1 are already in self.messages
        full_content = initial_content or ""

        logger.info("BackgroundChain: Starting (round 1 was inline, resuming from round 2)")

        while self._round_num < self.MAX_ROUNDS and not self.cancelled:
            self._round_num += 1
            logger.info("BackgroundChain: Round %d/%d", self._round_num, self.MAX_ROUNDS)

            # Feed tool results back to LLM
            follow_up_content, follow_up_tool_calls = await self._process_follow_up()

            full_content = follow_up_content or ""

            # If LLM returned text but no more tool calls — we're done
            if not follow_up_tool_calls and full_content:
                await self._announce_result(full_content)
                return

            # If LLM returned nothing — we're done
            if not follow_up_tool_calls and not full_content:
                await self._announce_result("Done")
                return

            # Execute the next round of tool calls
            self.messages.append({
                "role": "assistant",
                "content": full_content or None,
                "tool_calls": follow_up_tool_calls,
            })

            tool_results = await self._execute_tools(follow_up_tool_calls, full_content)

            # Check for auto-send to Telegram
            await self._auto_send_file_to_telegram()

            # Update for next iteration
            # tool results are already appended in _execute_tools
            full_content = ""
            # Loop continues...

        # Hit MAX_ROUNDS or cancelled
        if self._round_num >= self.MAX_ROUNDS:
            logger.warning("BackgroundChain: Hit MAX_ROUNDS (%d)", self.MAX_ROUNDS)
            await self._announce_result(full_content or "Chain completed (max rounds reached)")

    async def _process_follow_up(self):
        """Make a follow-up LLM call with tool results. Returns (content, tool_calls)."""
        if self.cancelled:
            return "", []

        # Use the service's _prepare_api_messages with our message list
        # We temporarily swap self._messages so _prepare_api_messages uses ours.
        # This is safe because: (1) the swap is in a try/finally, (2) background chains
        # are cancelled in process_chat_input before any new generation starts,
        # so the main agent never reads our swapped messages.
        original_messages = self.service._messages
        self.service._messages = self.messages
        try:
            validated_messages = self.service._prepare_api_messages()
        finally:
            self.service._messages = original_messages

        stream = await self.service._call_stream(validated_messages, tools_list=self.tools_list, max_retries=3)

        content = ""
        tool_calls = []
        current_tc = None

        async def _inner():
            nonlocal content, tool_calls, current_tc
            tool_call_detected = False
            async for chunk in stream:
                if self.cancelled:
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
                    content += delta.content
                    # Don't stream to TTS during background chain — we'll announce at the end

        try:
            await asyncio.wait_for(_inner(), timeout=60.0)
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning("BackgroundChain: Follow-up stream timed out after 60s")

        if current_tc:
            tool_calls.append(current_tc)

        # Parse text-encoded tool calls
        if not tool_calls and content and '<tool_call>' in content:
            parsed = self.service._parse_text_tool_calls(content)
            if parsed:
                tool_calls = parsed
                content = content # Keep content for context

        return content, tool_calls

    async def _execute_tools(self, tool_calls, full_content: str):
        """Execute tool calls and append results to self.messages."""
        if self.cancelled:
            return []

        last_user_message = ""
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                last_user_message = content if isinstance(content, str) else str(content)
                break

        results = []
        for tc in tool_calls:
            func_name = tc["function"]["name"]
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                func_args = {}

            logger.info("🔧 BackgroundChain tool: %s", func_name)

            if last_user_message:
                func_args["last_user_message"] = last_user_message

            # Execute the tool
            result = None
            if func_name in self.service._tools_dict:
                tool = self.service._tools_dict[func_name]
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
                    logger.error("BackgroundChain tool %s error: %s", func_name, e, exc_info=True)

                # Record audit
                if self.chat_id:
                    from distr.core.agent.tool_audit import record_tool_execution
                    record_tool_execution(
                        self.chat_id, func_name, str(result)[:2000], status,
                        event_queue=self.event_queue
                    )
            else:
                # Try fuzzy match
                matched = self.service._fuzzy_match_tool(func_name) if hasattr(self.service, '_fuzzy_match_tool') else None
                if matched:
                    try:
                        result = matched._run(**json.loads(tc["function"].get("arguments", "{}")))
                    except Exception as e:
                        result = f"Error: {e}"
                else:
                    result = f"Error: Tool '{func_name}' not found'"

            result_str = str(result) if result is not None else ""
            resp = {"tool_call_id": tc["id"], "role": "tool", "name": func_name, "content": result_str}
            self.messages.append(resp)
            results.append(result)

        return results

    async def _auto_send_file_to_telegram(self):
        """Try auto-sending file to Telegram if applicable."""
        if not getattr(self.service, '_is_telegram_request', False):
            return False
        try:
            return await self.service._auto_send_file_to_telegram()
        except Exception as e:
            logger.debug("BackgroundChain auto-send error: %s", e)
            return False

    async def _announce_result(self, content: str):
        """Announce the chain result via TTS and save to chat history."""
        if self.cancelled:
            return

        if not content or not content.strip():
            content = "Done"

        logger.info("BackgroundChain: Completed — announcing result (%d chars)", len(content))

        # Clean content for TTS
        from distr.core.agent.services.llm.text_utils import clean_text_for_tts
        cleaned = clean_text_for_tts(content)

        # 1. Announce via TTS
        # Must use event_queue (not signal_manager) because the agent runs in a
        # subprocess. Qt signals don't cross process boundaries, and cross-thread
        # signal delivery requires a running Qt event loop (agent uses asyncio.run).
        if self.service._speaker_enabled and not getattr(self.service, '_is_telegram_request', False):
            try:
                from distr.core.signals import speak_text_directly_event_queue
                speak_text_directly_event_queue(cleaned[:500])  # Cap TTS at 500 chars
            except Exception as e:
                logger.debug("BackgroundChain TTS announcement error: %s", e)

        # 2. Add to chat history
        if self.service.chat_manager:
            try:
                chat_id = self.chat_id or (self.service.chat_manager.get_current_chat() if self.service.chat_manager else None)
                if chat_id:
                    self.service.chat_manager.add_assistant_message(chat_id, cleaned)
                    # Notify UI
                    from distr.core.signals import signal_manager
                    signal_manager.chat_message_added.emit(chat_id, "assistant", cleaned)
                    signal_manager.chat_updated.emit(chat_id)
            except Exception as e:
                logger.debug("BackgroundChain chat history error: %s", e)

        # 3. Merge our messages into the main service's message history
        # Only append the final assistant message — intermediate tool chatter is noise
        original_messages = self.service._messages
        # Find if the last message in main history is an assistant message we can update,
        # otherwise append anew
        self.service._messages.append({"role": "assistant", "content": cleaned})

        # 4. Handle Telegram response
        if getattr(self.service, '_is_telegram_request', False):
            self.service._telegram_fallback_text = content[:2000]

        # 5. Send typing indicator off
        if self.event_queue:
            try:
                self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
                chat_id = self.chat_id or (self.service.chat_manager.get_current_chat() if self.service.chat_manager else None)
                if chat_id:
                    self.event_queue.put(
                        ('chat_stream_finished', {'chat_id': chat_id, 'response_text': cleaned}),
                        block=False,
                    )
            except Exception:
                pass

    def cancel(self):
        """Cancel the background chain."""
        self.cancelled = True
        if self.task and not self.task.done():
            self.task.cancel()
        logger.info("BackgroundChain: Cancelled at round %d", self._round_num)

    @staticmethod
    def has_action_required(messages: list) -> bool:
        """Check if any recent tool result contains [ACTION REQUIRED]."""
        for msg in reversed(messages):
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if "[ACTION REQUIRED" in content:
                    return True
        return False

    @staticmethod
    def is_multi_tool_chain(tool_calls: list) -> bool:
        """Heuristic: does this look like a multi-step chain?

        Triggers:
        - Tools known to produce chains (screenshot_analyzer with capture_only,
          execute_code, pi_agent)
        - Multiple tool calls that suggest a sequence
        """
        chain_tool_names = {
            'execute_code', 'pi_agent', 'file_converter', 'kanban_ticket',
        }
        for tc in tool_calls:
            func_name = tc.get("function", {}).get("name", "")
            if func_name in chain_tool_names:
                return True
            # screenshot_analyzer is chain-prone ONLY in capture_only mode
            # (screenshot artifact needs routing to follow-up tools). Normal
            # vision interactions should stay inline so users get precise
            # action feedback instead of generic background "Done."
            if func_name == 'screenshot_analyzer':
                try:
                    args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                    if args.get("capture_only"):
                        return True
                except (json.JSONDecodeError, TypeError):
                    pass
        return len(tool_calls) > 1