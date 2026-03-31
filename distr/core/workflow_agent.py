"""
WorkflowAgent — lightweight, in-process LLM agent for workflow step execution.

Unlike the main pipecat voice agent, WorkflowAgent has:
- No STT/TTS pipeline
- No event_queue or signal_manager dependencies
- Its own isolated message history
- Synchronous LLM streaming via llm_factory.create_stream

This keeps workflow execution completely decoupled from the main agent,
allowing concurrent workflows and uninterrupted user interaction.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class WorkflowAgent:
    """Lightweight LLM agent dedicated to a single workflow execution.

    Each instance wraps an LLM provider (resolved from settings) with its own
    ``_messages`` list and tool set.  The async ``execute(instruction)`` method
    sends the instruction to the LLM, collects the full response, and returns
    the response text — no signals, no event queues, no shared state.
    """

    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        """Initialise the workflow agent.

        Parameters
        ----------
        settings : dict, optional
            Application settings dict.  When *None*, settings are loaded from
            the database via ``load_settings_from_db()``.
        """
        if settings is None:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()

        self._settings = settings

        # Resolve provider and model from settings
        from distr.core.llm_factory import resolve_settings_keys
        self._provider, self._model = resolve_settings_keys(settings)

        # Isolated message history — never touches the main agent's messages
        self._messages: List[Dict[str, str]] = []

        # Load tools (scoped to this agent — no event_queue/command_queue)
        self._tools: list = []
        self._tools_dict: Dict[str, Any] = {}
        self._load_tools()

        self._shutdown = False

        logger.info(
            "WorkflowAgent initialised: provider=%s model=%s tools=%d",
            self._provider, self._model, len(self._tools),
        )

    # ------------------------------------------------------------------
    #  Tool loading
    # ------------------------------------------------------------------

    def _load_tools(self) -> None:
        """Load the same tool set the main agent uses, scoped to this agent."""
        try:
            from distr.core.agent.tools import load_tools
            self._tools = load_tools(
                chat_manager=None,
                use_navigation_tools=True,
                llm_service=None,
                tts_service=None,
                llm_model=self._model,
                event_queue=None,
                command_queue=None,
                confirmation_results_dict=None,
            )
            self._tools_dict = {tool.name: tool for tool in self._tools}
            logger.debug("WorkflowAgent: loaded %d tools", len(self._tools))
        except Exception as exc:
            logger.warning("WorkflowAgent: failed to load tools: %s", exc)
            self._tools = []
            self._tools_dict = {}

    # ------------------------------------------------------------------
    #  Execution
    # ------------------------------------------------------------------

    async def execute(self, instruction: str) -> str:
        """Execute an instruction and return the LLM response text.

        The instruction is appended to ``_messages`` as a user message, the
        LLM is called via ``llm_factory.create_stream``, and the full
        response is collected, appended as an assistant message, and returned.

        Tool calls are handled inline: when the LLM returns tool-call JSON
        instead of plain text, the tools are executed and the results fed
        back for a follow-up generation.
        """
        if self._shutdown:
            raise RuntimeError("WorkflowAgent has been shut down")

        self._messages.append({"role": "user", "content": instruction})

        response_text = await self._generate()

        self._messages.append({"role": "assistant", "content": response_text})

        return response_text

    async def _generate(self) -> str:
        """Run the LLM generation in a thread executor (blocking I/O)."""
        from distr.core.llm_factory import create_stream

        loop = asyncio.get_running_loop()

        def _stream_sync() -> str:
            tokens: list = []
            try:
                stream = create_stream(
                    provider=self._provider,
                    model=self._model,
                    messages=list(self._messages),
                    settings=self._settings,
                )
                for token in stream:
                    tokens.append(token)
            except Exception as exc:
                logger.error("WorkflowAgent: LLM stream error: %s", exc, exc_info=True)
                return f"Error: {exc}"
            return "".join(tokens)

        response = await loop.run_in_executor(None, _stream_sync)
        return response

    # ------------------------------------------------------------------
    #  Tool execution helpers
    # ------------------------------------------------------------------

    def _execute_tool_call(self, tool_name: str, tool_args: dict) -> str:
        """Execute a single tool call synchronously and return the result."""
        tool = self._tools_dict.get(tool_name)
        if not tool:
            return f"Error: Tool '{tool_name}' not found"
        try:
            result = tool._run(**tool_args)
            return str(result)
        except Exception as exc:
            logger.error("WorkflowAgent: tool %s failed: %s", tool_name, exc, exc_info=True)
            return f"Error: {exc}"

    # ------------------------------------------------------------------
    #  Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Clean up resources held by this agent."""
        if self._shutdown:
            return
        self._shutdown = True
        self._messages.clear()
        self._tools.clear()
        self._tools_dict.clear()
        logger.info("WorkflowAgent shut down (provider=%s model=%s)", self._provider, self._model)

    # ------------------------------------------------------------------
    #  Introspection
    # ------------------------------------------------------------------

    @property
    def provider(self) -> str:
        """The resolved LLM provider name."""
        return self._provider

    @property
    def model(self) -> str:
        """The resolved LLM model name."""
        return self._model

    @property
    def messages(self) -> List[Dict[str, str]]:
        """Read-only view of the conversation history."""
        return list(self._messages)

    @property
    def tools(self) -> list:
        """The loaded tool instances."""
        return list(self._tools)

    def __repr__(self) -> str:
        return (
            f"WorkflowAgent(provider={self._provider!r}, model={self._model!r}, "
            f"messages={len(self._messages)}, tools={len(self._tools)}, "
            f"shutdown={self._shutdown})"
        )
