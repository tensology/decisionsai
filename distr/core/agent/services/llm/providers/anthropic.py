"""
Anthropic LLM Service

Thin provider layer on top of BaseLLMService.

Only contains:
- convert_tools_to_anthropic_format (module-level helper)
- __init__ (Anthropic client setup — delegates to BaseLLMService)
- _setup_system_prompt (Anthropic uses system as a string, not a message)
- _generate_response (Anthropic streaming + tool use)
- _format_vision_message (Anthropic image format)
- _generate_welcome_summary (Anthropic-specific API call)
"""

import asyncio
import json
import logging
import os
from typing import List, Dict, Any, Optional

from distr.core.agent.libs import (
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    ErrorFrame,
)
from distr.core.agent.services.llm.prompt import (
    load_system_prompt_template, build_tools_description,
)
from distr.core.agent.services.llm.fast_action_detector import detect_fast_action, ActionType
from ..base_service import BaseLLMService

logger = logging.getLogger(__name__)

try:
    from anthropic import AsyncAnthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    AsyncAnthropic = None
    ANTHROPIC_AVAILABLE = False
    logger.warning("Anthropic library not available")

from distr.core.signals import signal_manager


def convert_tools_to_anthropic_format(tools: List) -> List[Dict[str, Any]]:
    """Convert LangChain tools to Anthropic tool format."""
    anthropic_tools = []
    for tool in tools:
        try:
            if hasattr(tool, 'args_schema') and tool.args_schema:
                schema = tool.args_schema.schema()
                properties = schema.get('properties', {})
                required = schema.get('required', [])
            else:
                properties = {}
                required = []

            anthropic_tools.append({
                "name": tool.name,
                "description": tool.description or f"Tool: {tool.name}",
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            })
        except Exception as e:
            logger.warning("Error converting tool %s to Anthropic format: %s", tool.name, e)
    return anthropic_tools


class AnthropicLLMService(BaseLLMService):
    """Anthropic Claude LLM service using Pipecat.

    Inherits common init, tool loading, signal wiring, and generation state
    from BaseLLMService.  Only overrides _setup_system_prompt (Anthropic uses
    system as a string, not a message), _generate_response (Anthropic streaming
    API), and provider-specific helpers.
    """

    SERVICE_NAME = "AnthropicLLMService"
    DEFAULT_MODEL = "claude-3-5-sonnet-20241022"

    def __init__(self, api_key: str, model_name: str = "claude-3-5-sonnet-20241022",
                 system_prompt: str = None, event_queue=None, is_listening=True,
                 chat_manager=None, tts_service=None, agent_name: str = "Heart",
                 command_queue=None, confirmation_results_dict=None, **kwargs):
        if not ANTHROPIC_AVAILABLE:
            raise ImportError("anthropic library is required for AnthropicLLMService")

        # Set up the Anthropic client BEFORE calling super().__init__()
        self.client = AsyncAnthropic(api_key=api_key)

        super().__init__(
            api_key=api_key, model_name=model_name, system_prompt=system_prompt,
            event_queue=event_queue, is_listening=is_listening,
            chat_manager=chat_manager, tts_service=tts_service,
            agent_name=agent_name, command_queue=command_queue,
            confirmation_results_dict=confirmation_results_dict, **kwargs,
        )

    # ------------------------------------------------------------------
    #  Anthropic-specific overrides
    # ------------------------------------------------------------------

    def _setup_system_prompt(self, system_prompt: Optional[str] = None):
        """Setup system prompt. Anthropic uses system as a string parameter, not a message."""
        template = load_system_prompt_template()
        template = template.replace("{username}", self._username)
        template = template.replace("{agent_name}", self._agent_name)
        template = template.replace("{model_name}", self._model_name)

        if self._tools:
            template = template.replace("{tools_description}", build_tools_description(self._tools))
        else:
            template = template.replace("{tools_description}", "No tools available.")

        current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
        template = template.replace("{dropped_files_context}", self._get_dropped_files_context(chat_id=current_chat_id))

        # Also fill in folder paths to avoid KeyError on {desktop_path} etc.
        try:
            from distr.core.settings import get_system_folder_paths
            folder_paths = get_system_folder_paths()
            home_path = os.path.expanduser("~")
            template = template.format(
                agent_name=self._agent_name, username=self._username,
                tools_description=build_tools_description(self._tools) if self._tools else "No tools available.",
                model_name=self._model_name,
                desktop_path=folder_paths.get("Desktop", os.path.join(home_path, "Desktop")),
                documents_path=folder_paths.get("Documents", os.path.join(home_path, "Documents")),
                downloads_path=folder_paths.get("Downloads", os.path.join(home_path, "Downloads")),
                pictures_path=folder_paths.get("Pictures", os.path.join(home_path, "Pictures")),
                music_path=folder_paths.get("Music", os.path.join(home_path, "Music")),
                videos_path=folder_paths.get("Videos", os.path.join(home_path, "Videos")),
                home_path=home_path,
                dropped_files_context=self._get_dropped_files_context(chat_id=current_chat_id),
            )
        except Exception:
            pass  # Template may already be resolved from .replace() calls above

        self.default_template = template
        self._persona = system_prompt if system_prompt else None
        self._system_prompt = f"{system_prompt}\n\n{template}" if system_prompt else template

        # Also set _default_template_raw for shared mixin compatibility
        self._default_template_raw = load_system_prompt_template()

        # Initialize _messages (Anthropic uses system as a separate param, not a message)
        self._messages = []

    def _format_vision_message(self, text: str, base64_image: str, mime_type: str):
        """Anthropic vision format."""
        return [
            {"type": "text", "text": text},
            {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": base64_image}},
        ]

    async def _generate_welcome_summary(self, conversation_text: str, agent_name: str) -> str:
        """Generate welcome summary using Anthropic API."""
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
            response = await self.client.messages.create(
                model=self._model_name,
                max_tokens=200,
                messages=[{"role": "user", "content": summary_prompt}],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error("Error generating welcome summary via Anthropic: %s", e, exc_info=True)
            return ""

    # ------------------------------------------------------------------
    #  _generate_response — Anthropic streaming + tool use
    # ------------------------------------------------------------------

    async def _generate_response(self):
        """Generate LLM response using Anthropic streaming API."""
        full_content = ""
        follow_up_content = ""
        try:
            await self.push_frame(LLMFullResponseStartFrame(), self._pipeline_direction)

            if self.event_queue:
                self.event_queue.put(('typing_indicator_changed', {'show': True}), block=False)
                current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                if current_chat_id:
                    self.event_queue.put(('chat_stream_started', {'chat_id': current_chat_id}), block=False)

            # Fast action check
            if self._messages:
                last_msg = self._messages[-1].get("content", "")
                fast_action = detect_fast_action(last_msg)
                if fast_action and fast_action.action_type not in [ActionType.CONVERSATIONAL, ActionType.UNKNOWN]:
                    handled = await self._execute_fast_action(fast_action, None)
                    if handled:
                        await self.push_frame(LLMFullResponseEndFrame(), self._pipeline_direction)
                        if self.event_queue:
                            self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
                            cid = self.chat_manager.get_current_chat() if self.chat_manager else None
                            if cid:
                                self.event_queue.put(('chat_stream_finished', {'chat_id': cid, 'response_text': ''}), block=False)
                        return

            # Build Anthropic messages (system is separate)
            anthropic_messages = []
            for msg in self._messages:
                role = msg.get("role")
                content = msg.get("content")
                if role == "system":
                    continue
                elif role == "user":
                    anthropic_messages.append({"role": "user", "content": content})
                elif role == "assistant":
                    anthropic_messages.append({"role": "assistant", "content": content or ""})
                elif role == "tool":
                    anthropic_messages.append({
                        "role": "user",
                        "content": f"[Tool {msg.get('name', '')} result: {content}]",
                    })

            # Truncate if too long
            if len(anthropic_messages) > 50:
                anthropic_messages = anthropic_messages[-50:]

            anthropic_tools = convert_tools_to_anthropic_format(
                self._get_filtered_tools(
                    anthropic_messages[-1].get('content', '') if anthropic_messages else None
                )
            ) if self._tools else None

            # Ensure tools is a valid list or None — Anthropic rejects non-list values
            if not isinstance(anthropic_tools, list) or not anthropic_tools:
                anthropic_tools = None

            create_kwargs = dict(
                model=self._model_name, max_tokens=4096,
                system=self._system_prompt, messages=anthropic_messages,
                stream=True,
            )
            if anthropic_tools:
                create_kwargs["tools"] = anthropic_tools

            stream = await self.client.messages.create(**create_kwargs)

            full_content = ""
            tool_use_blocks = []
            current_tool_use = None
            tool_use_detected = False

            async for event in stream:
                if self._cancelled:
                    break

                if event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        tool_use_detected = True
                        current_tool_use = {
                            "id": event.content_block.id,
                            "name": event.content_block.name,
                            "input_json": "",
                        }
                        tool_use_blocks.append(current_tool_use)

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        full_content += delta.text
                        if self._speaker_enabled and not tool_use_detected and not getattr(self, '_is_telegram_request', False):
                            await self.push_frame(TextFrame(text=delta.text), self._pipeline_direction)
                        if self.event_queue:
                            self.event_queue.put(('chat_stream_token', {'token': delta.text}), block=False)
                    elif delta.type == "input_json_delta" and current_tool_use:
                        current_tool_use["input_json"] += delta.partial_json

                elif event.type == "content_block_stop":
                    current_tool_use = None

            await self.push_frame(LLMFullResponseEndFrame(), self._pipeline_direction)

            # Handle tool use
            if tool_use_blocks and not self._cancelled:
                for tu in tool_use_blocks:
                    try:
                        tu["input"] = json.loads(tu.get("input_json", "{}"))
                    except json.JSONDecodeError:
                        tu["input"] = {}

                self._messages.append({"role": "assistant", "content": full_content if full_content else None})

                tool_results = []
                for tu in tool_use_blocks:
                    tool = self._tools_dict.get(tu["name"])
                    if tool:
                        try:
                            loop = asyncio.get_running_loop()
                            result = await loop.run_in_executor(
                                None, lambda t=tool, inp=tu["input"]: t._run(**inp)
                            )
                            tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": str(result)})
                            chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                            from distr.core.agent.tool_audit import record_tool_execution
                            record_tool_execution(chat_id, tu["name"], str(result), "completed", event_queue=self.event_queue)
                        except Exception as e:
                            err_content = f"Error: {e}"
                            tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": err_content})
                            chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                            from distr.core.agent.tool_audit import record_tool_execution
                            record_tool_execution(chat_id, tu["name"], err_content, "failed", event_queue=self.event_queue)
                    else:
                        tool_results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": f"Tool '{tu['name']}' not found"})

                # Follow-up with tool results
                follow_up_messages = anthropic_messages.copy()
                assistant_content = []
                if full_content:
                    assistant_content.append({"type": "text", "text": full_content})
                for tu in tool_use_blocks:
                    assistant_content.append({"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tu["input"]})
                follow_up_messages.append({"role": "assistant", "content": assistant_content})
                follow_up_messages.append({"role": "user", "content": tool_results})

                # Collect tool result text for fallback
                tool_result_texts = [tr.get("content", "") for tr in tool_results if tr.get("content")]

                # Up to 2 rounds of follow-up (in case model chains tool calls)
                for follow_up_round in range(2):
                    try:
                        follow_up_stream = await asyncio.wait_for(
                            self.client.messages.create(
                                model=self._model_name, max_tokens=4096,
                                system=self._system_prompt, messages=follow_up_messages, stream=True,
                            ),
                            timeout=30.0,
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        logger.warning("Anthropic follow-up timed out (round %d)", follow_up_round)
                        break
                    except Exception as e:
                        logger.error("Anthropic follow-up API call failed (round %d): %s", follow_up_round, e, exc_info=True)
                        break

                    follow_up_content = ""
                    follow_up_tool_blocks = []
                    current_follow_up_tool = None

                    async for event in follow_up_stream:
                        if self._cancelled:
                            logger.warning("Anthropic follow-up cancelled during streaming (round %d)", follow_up_round)
                            break
                        if event.type == "content_block_start":
                            if hasattr(event, 'content_block') and event.content_block.type == "tool_use":
                                current_follow_up_tool = {
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input_json": "",
                                }
                                follow_up_tool_blocks.append(current_follow_up_tool)
                                logger.info("Anthropic follow-up wants tool call (round %d): %s", follow_up_round, event.content_block.name)
                        elif event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                follow_up_content += event.delta.text
                                if self._speaker_enabled and not getattr(self, '_is_telegram_request', False):
                                    await self.push_frame(TextFrame(text=event.delta.text), self._pipeline_direction)
                                if self.event_queue:
                                    self.event_queue.put(('chat_stream_token', {'token': event.delta.text}), block=False)
                            elif event.delta.type == "input_json_delta" and current_follow_up_tool:
                                current_follow_up_tool["input_json"] += event.delta.partial_json
                        elif event.type == "content_block_stop":
                            current_follow_up_tool = None

                    # If we got text content, we're done
                    if follow_up_content:
                        break

                    # If model wants more tool calls, execute them and loop
                    if follow_up_tool_blocks and not self._cancelled:
                        for ftu in follow_up_tool_blocks:
                            try:
                                ftu["input"] = json.loads(ftu.get("input_json", "{}"))
                            except json.JSONDecodeError:
                                ftu["input"] = {}

                        chained_assistant = []
                        if follow_up_content:
                            chained_assistant.append({"type": "text", "text": follow_up_content})
                        for ftu in follow_up_tool_blocks:
                            chained_assistant.append({"type": "tool_use", "id": ftu["id"], "name": ftu["name"], "input": ftu["input"]})
                        follow_up_messages.append({"role": "assistant", "content": chained_assistant})

                        chained_results = []
                        for ftu in follow_up_tool_blocks:
                            tool = self._tools_dict.get(ftu["name"])
                            if tool:
                                try:
                                    loop = asyncio.get_running_loop()
                                    result = await loop.run_in_executor(
                                        None, lambda t=tool, inp=ftu["input"]: t._run(**inp)
                                    )
                                    chained_results.append({"type": "tool_result", "tool_use_id": ftu["id"], "content": str(result)})
                                    tool_result_texts.append(str(result))
                                except Exception as e:
                                    chained_results.append({"type": "tool_result", "tool_use_id": ftu["id"], "content": f"Error: {e}"})
                            else:
                                chained_results.append({"type": "tool_result", "tool_use_id": ftu["id"], "content": f"Tool '{ftu['name']}' not found"})
                        follow_up_messages.append({"role": "user", "content": chained_results})
                        continue

                    # No text and no tool calls — break
                    logger.warning("Anthropic follow-up produced no content and no tool calls (round %d, cancelled=%s)", follow_up_round, self._cancelled)
                    break

                if follow_up_content:
                    self._messages.append({"role": "assistant", "content": follow_up_content})
                    if self.chat_manager:
                        cid = self.chat_manager.get_current_chat()
                        if cid:
                            self.chat_manager.add_assistant_message(cid, follow_up_content)
                else:
                    # Synthesize a response from tool results so the user isn't left hanging
                    if tool_result_texts:
                        fallback = "Here's what I found:\n\n" + "\n".join(tool_result_texts[:3])
                        self._messages.append({"role": "assistant", "content": fallback})
                        if self.chat_manager:
                            cid = self.chat_manager.get_current_chat()
                            if cid:
                                self.chat_manager.add_assistant_message(cid, fallback)
                        if self._speaker_enabled and not getattr(self, '_is_telegram_request', False):
                            await self.push_frame(TextFrame(text=fallback), self._pipeline_direction)
                        if self.event_queue:
                            self.event_queue.put(('chat_stream_token', {'token': fallback}), block=False)
                        logger.info("Anthropic: used fallback response from tool results")
                    else:
                        logger.warning("Anthropic follow-up produced no content and no tool results to fall back on")

            elif full_content:
                self._messages.append({"role": "assistant", "content": full_content})
                if self.chat_manager:
                    cid = self.chat_manager.get_current_chat()
                    if cid:
                        self.chat_manager.add_assistant_message(cid, full_content)

        except Exception as e:
            logger.error("Error in AnthropicLLMService._generate_response: %s", e, exc_info=True)
            await self.push_frame(ErrorFrame(error=str(e)), self._pipeline_direction)
        finally:
            self._emit_telegram_response(full_content, follow_up_content)
            self._cleanup_telegram_flags()
            # Always signal the UI that generation is done
            if self.event_queue:
                self.event_queue.put(('typing_indicator_changed', {'show': False}), block=False)
                current_chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                if current_chat_id:
                    self.event_queue.put(('chat_stream_finished', {
                        'chat_id': current_chat_id,
                        'response_text': follow_up_content or full_content or '',
                    }), block=False)
