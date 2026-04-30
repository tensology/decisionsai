"""
Base LLM Service

Shared logic for all LLM providers (OpenAI, Anthropic, Groq, OpenRouter, KiloCode, Google Gemini, Ollama).
Provider subclasses only need to implement client initialization and _generate_response().

BaseLLMService inherits from LLMSharedMixin which provides:
- process_frame (voice commands, dictation, fast actions, etc.)
- process_chat_input (vision, provider verification)
- send_welcome_message (with conversation summaries)
- on_chat_changed / on_chat_deleted / on_chat_cleared
- set_hands_free / set_speaker_enabled / set_agent_name / set_listening
- set_tts_service (with tool reload)
- _setup_system_prompt / _build_system_message
- _execute_fast_action (full implementation)
- _ensure_user_message_persisted (no auto-create)
- Dictation, voice commands, Telegram helpers
"""

import asyncio
import json
import logging

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE, LLMService,
)
from distr.core.agent.services.llm.prompt import load_system_prompt_template
from distr.core.agent.tools import load_tools
from distr.core.agent.services.llm.computer_use_guard import build_computer_use_execution_decisions
from distr.core.signals import signal_manager
from .core_mixin import LLMSharedMixin

logger = logging.getLogger(__name__)


class BaseLLMService(LLMSharedMixin, LLMService):
    """
    Base class for all LLM service providers.

    Subclasses must:
    1. Set SERVICE_NAME and DEFAULT_MODEL class attributes
    2. Initialize self.client in their __init__ before calling super().__init__()
    3. Override _generate_response() with provider-specific API call logic
    """

    SERVICE_NAME = "BaseLLM"
    DEFAULT_MODEL = "unknown"

    def __init__(self, api_key: str, model_name: str = None, system_prompt: str = None,
                 event_queue=None, is_listening=True, chat_manager=None, tts_service=None,
                 agent_name: str = "Heart", command_queue=None, confirmation_results_dict=None, **kwargs):
        if not PIPECAT_AVAILABLE:
            raise ImportError(f"Pipecat is required for {self.SERVICE_NAME}")

        super().__init__(**kwargs)

        self._api_key = api_key
        self._model_name = model_name or self.DEFAULT_MODEL
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

        from pipecat.processors.frame_processor import FrameDirection
        self._pipeline_direction = FrameDirection.DOWNSTREAM

        self._load_tools(chat_manager, tts_service, model_name, event_queue, command_queue, confirmation_results_dict)

        if self.chat_manager:
            self.chat_manager.on("current_chat_changed", self.on_chat_changed)
            self.chat_manager.on("chat_deleted", self.on_chat_deleted)
            self.chat_manager.on("chat_cleared", self.on_chat_cleared)
            try:
                signal_manager.chat_cleared.connect(self.on_chat_cleared)
            except RuntimeError:
                pass  # signal_manager may be deleted in subprocess
        try:
            signal_manager.files_indexed.connect(self._on_files_indexed)
        except (RuntimeError, Exception):
            pass

        self._username = self._get_username()
        self._setup_system_prompt(system_prompt)

        self._generation_task = None
        self._cancelled = False
        self._processed_fast_actions = set()
        self._generation_requested_at = 0.0
        self._background_chain = None

        # Initialize message context from the currently selected chat so the first
        # post-restart utterance has real history instead of a fresh-session view.
        if self.chat_manager:
            try:
                self.on_chat_changed(self.chat_manager.get_current_chat())
            except Exception as e:
                logger.warning("%s: Failed to initialize chat context: %s", self.SERVICE_NAME, e)

        logger.info("%s initialized with model: %s", self.SERVICE_NAME, self._model_name)

    def _load_tools(self, chat_manager, tts_service, model_name, event_queue, command_queue, confirmation_results_dict):
        """Load tools for the LLM service.

        Uses the module-level tool cache when available (populated by
        ``warm_tool_cache`` at startup) to avoid re-instantiating all tools.
        Applies model-tier filtering so micro/small models don't receive the
        full 80+ tool surface (they'd hallucinate).
        Falls back to full instantiation if the cache is empty.
        """
        try:
            from distr.core.agent.tools.loader import _tool_cache
            if _tool_cache:
                # Apply model-tier filtering from ToolRetriever
                from distr.core.agent.tool_retriever import get_tool_retriever
                tier = get_tool_retriever().classify_model_tier(model_name or "")
                if tier == "micro":
                    # Micro models: only always-on core + request_tool
                    from distr.core.agent.tool_retriever import ALWAYS_ON_NAMES
                    micro_names = ALWAYS_ON_NAMES | {"request_tool"}
                    self._tools = [
                        t for t in _tool_cache.values()
                        if t.name in micro_names
                    ]
                    logger.info(
                        "%s: Micro-tier model %r - %d tools (always-on + request_tool)",
                        self.SERVICE_NAME, model_name, len(self._tools),
                    )
                else:
                    # Standard / small: full set - per-request _get_filtered_tools()
                    # trims further based on user_message semantics.
                    self._tools = list(_tool_cache.values())
            else:
                self._tools = load_tools(
                    chat_manager=chat_manager,
                    use_navigation_tools=True,
                    llm_service=self,
                    tts_service=tts_service,
                    llm_model=model_name,
                    event_queue=event_queue,
                    command_queue=command_queue,
                    confirmation_results_dict=confirmation_results_dict
                )
            # dict[str, BaseTool] keyed by tool.name — used by _get_filtered_tools
            # and tool execution lookups across all provider subclasses.
            self._tools_dict = {tool.name: tool for tool in self._tools}
            logger.debug("%s: Loaded %d tools", self.SERVICE_NAME, len(self._tools))
            self._build_tool_index_async()
        except Exception as e:
            logger.error("Error loading tools for %s: %s", self.SERVICE_NAME, e)
            self._tools = []
            self._tools_dict = {}

    async def _generate_response(self):
        """Generate LLM response. Override in subclass for provider-specific API calls."""
        raise NotImplementedError(f"{self.SERVICE_NAME} must implement _generate_response()")

    def _check_fast_actions(self):
        """Check if the last user message triggers a fast action (bypasses LLM).

        Returns a DetectedAction if a fast action is found, or None.
        Shared across all providers.
        """
        from distr.core.agent.services.llm.fast_action_detector import detect_fast_action, ActionType
        if not self._messages:
            return None
        last_message = self._messages[-1].get("content", "")
        if not isinstance(last_message, str):
            return None
        # Skip if we already processed this exact message as a fast action
        if last_message in self._processed_fast_actions:
            return None
        fast_action = detect_fast_action(last_message)
        if fast_action and fast_action.confidence >= 0.9 and fast_action.action_type not in (ActionType.CONVERSATIONAL, ActionType.UNKNOWN):
            return fast_action
        return None

    async def _execute_tool_calls(self, tool_calls: list) -> list:
        """Execute tool calls and return results. Works for OpenAI-compatible format."""
        results = []
        decisions = build_computer_use_execution_decisions(tool_calls)
        for idx, tool_call in enumerate(tool_calls):
            decision = decisions[idx] if idx < len(decisions) else {"allow": True, "reason": "ok"}
            tool_name = tool_call["function"]["name"]
            if not decision.get("allow", True):
                results.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": tool_name,
                    "content": (
                        "Skipped by computer-use guard: only one actioning computer-use step "
                        "is executed per round. Re-run next step after observing updated context."
                    ),
                })
                continue
            try:
                tool_args = json.loads(tool_call["function"]["arguments"])
            except json.JSONDecodeError:
                logger.error("Invalid JSON in tool arguments: %s", tool_call['function']['arguments'])
                tool_args = {}

            tool = self._tools_dict.get(tool_name) or next((t for t in self._tools if t.name == tool_name), None)
            if tool:
                try:
                    # Self-reflection: check for failure loops before re-issuing
                    reflection_prompt = None
                    if hasattr(self, 'check_before_tool_call'):
                        try:
                            reflection_prompt = self.check_before_tool_call(tool_name, tool_args)
                        except RuntimeError as re:
                            # Loop-break: too many identical failures — escalate
                            logger.warning("Tool loop-break triggered: %s", re)
                            loop = asyncio.get_running_loop()
                            result = await loop.run_in_executor(
                                None, lambda t=tool, a=tool_args: {
                                    "output": f"Stopped: {re}", "passed": False
                                } if hasattr(t, '_run') else f"Stopped: {re}"
                            )
                            results.append({
                                "role": "tool",
                                "tool_call_id": tool_call["id"],
                                "name": tool_name,
                                "content": str(result)
                            })
                            chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                            from distr.core.agent.tool_audit import record_tool_execution
                            record_tool_execution(chat_id, tool_name, str(result), "failed", event_queue=self.event_queue)
                            # Record the loop-break outcome for reflection tracking
                            if hasattr(self, 'record_tool_attempt'):
                                self.record_tool_attempt(tool_name, tool_args, "failure", str(result))
                            continue

                    # Inject reflection context if the LLM should reconsider
                    if reflection_prompt and hasattr(self, '_messages') and self._messages:
                        self._messages.append({
                            "role": "system",
                            "content": reflection_prompt,
                        })

                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, lambda t=tool, a=tool_args: t._run(**a)
                    )
                    results.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": tool_name,
                        "content": str(result)
                    })
                    chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                    from distr.core.agent.tool_audit import record_tool_execution
                    record_tool_execution(chat_id, tool_name, str(result), "completed", event_queue=self.event_queue)
                    # Record successful execution for self-reflection
                    if hasattr(self, 'record_tool_attempt'):
                        self.record_tool_attempt(tool_name, tool_args, "success", str(result))
                except Exception as e:
                    logger.error("Error executing tool %s: %s", tool_name, e, exc_info=True)
                    err_content = f"Error: {str(e)}"
                    results.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": tool_name,
                        "content": err_content
                    })
                    chat_id = self.chat_manager.get_current_chat() if self.chat_manager else None
                    from distr.core.agent.tool_audit import record_tool_execution
                    record_tool_execution(chat_id, tool_name, err_content, "failed", event_queue=self.event_queue)
                    # Record failed execution for self-reflection
                    if hasattr(self, 'record_tool_attempt'):
                        self.record_tool_attempt(tool_name, tool_args, "failure", err_content)
            else:
                logger.warning("Tool not found: %s", tool_name)
                results.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": tool_name,
                    "content": f"Error: Tool '{tool_name}' not found"
                })
        return results

    def _save_assistant_message(self, content: str):
        """Save assistant message to history and chat manager."""
        self._messages.append({"role": "assistant", "content": content})
        if self.chat_manager:
            current_chat = self.chat_manager.get_current_chat()
            if current_chat:
                self.chat_manager.add_assistant_message(current_chat, content)

    def _get_provider_name(self) -> str:
        """Return the provider name. Override in subclass."""
        return self.SERVICE_NAME.replace("LLMService", "")
