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
        # Match main-session tool availability: warm cache if startup has not run yet
        # (isolated workflow steps, tests, or early WorkflowAgent construction).
        try:
            from distr.core.agent.tools.loader import ensure_tool_cache_warmed_if_empty

            ensure_tool_cache_warmed_if_empty()
        except Exception as exc:
            logger.debug("WorkflowAgent: tool cache warmup skipped: %s", exc)
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
        """Execute an instruction with a full tool-call loop.

        Uses the provider's native tool-calling API (same credentials and
        format converters as the main chat) so the LLM can actually invoke
        tools. Loops until the LLM produces a final text response or hits
        the iteration cap.
        """
        if self._shutdown:
            raise RuntimeError("WorkflowAgent has been shut down")

        # Inject system prompt on first call
        if not self._messages or self._messages[0].get("role") != "system":
            self._messages.insert(0, {"role": "system", "content": (
                "You are a workflow step executor. Execute the given instruction using "
                "the tools available to you. Do not explain what you would do — actually "
                "do it by calling the appropriate tools. When done, provide a brief summary."
            )})

        self._messages.append({"role": "user", "content": instruction})

        max_iterations = 25

        for iteration in range(max_iterations):
            text, tool_calls = await self._call_llm_with_tools()

            if not tool_calls:
                if text:
                    self._messages.append({"role": "assistant", "content": text})
                return text or "Step completed."

            # Execute tool calls and feed results back
            self._append_assistant_with_tool_calls(text, tool_calls)

            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("arguments", {})
                if isinstance(tool_args, str):
                    try:
                        tool_args = json.loads(tool_args)
                    except (json.JSONDecodeError, ValueError):
                        tool_args = {}

                logger.info("WorkflowAgent: tool %s (iteration %d/%d)", tool_name, iteration + 1, max_iterations)
                result = self._execute_tool_call(tool_name, tool_args)
                self._append_tool_result(tc, tool_name, result)

        # Hit cap — force final
        self._messages.append({"role": "user", "content": "Provide your final answer now."})
        text, _ = await self._call_llm_with_tools()
        if text:
            self._messages.append({"role": "assistant", "content": text})
        return text or "Step completed (iteration limit)."

    # ------------------------------------------------------------------
    #  LLM call with tools — dispatches to the right provider
    # ------------------------------------------------------------------

    async def _call_llm_with_tools(self) -> tuple:
        """Call the LLM with tool definitions. Returns (text, tool_calls).

        Uses the same clients and credential resolution as the main chat
        providers, but synchronous (run in executor) since WorkflowAgent
        doesn't have a pipecat pipeline.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._call_llm_sync)

    def _call_llm_sync(self) -> tuple:
        """Synchronous LLM call with tools. Returns (text, tool_calls_list)."""
        prov = self._provider_lower

        try:
            if prov == "anthropic":
                return self._call_anthropic()
            elif prov == "ollama":
                return self._call_ollama()
            else:
                # OpenAI-compatible: openai, groq, openrouter, kilocode, gemini
                return self._call_openai_compat()
        except Exception as exc:
            logger.error("WorkflowAgent: LLM call failed (%s/%s): %s", prov, self._model, exc, exc_info=True)
            return f"Error: {exc}", []

    @property
    def _provider_lower(self) -> str:
        return (self._provider or "ollama").strip().lower()

    # ------------------------------------------------------------------
    #  OpenAI-compatible providers (OpenAI, Groq, OpenRouter, KiloCode, Gemini)
    # ------------------------------------------------------------------

    def _call_openai_compat(self) -> tuple:
        from openai import OpenAI

        api_key, base_url = self._resolve_openai_creds()
        if not api_key and self._provider_lower != "ollama":
            return f"Error: No API key configured for {self._provider}. Check Settings → Third Party.", []

        kwargs = {"api_key": api_key or "ollama"}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)

        tools_list = self._get_openai_tools()

        call_kwargs = {
            "model": self._model,
            "messages": self._validated_messages_openai(),
            "max_tokens": 4096,
        }
        if tools_list:
            call_kwargs["tools"] = tools_list

        try:
            resp = client.chat.completions.create(**call_kwargs)
        except Exception as e:
            err = str(e).lower()
            # Model doesn't support tools — retry without
            if tools_list and ("tool" in err and ("not support" in err or "404" in err or "not found" in err)):
                logger.warning("WorkflowAgent: model %s doesn't support tools, retrying without", self._model)
                call_kwargs.pop("tools", None)
                resp = client.chat.completions.create(**call_kwargs)
            else:
                raise

        choice = resp.choices[0] if resp.choices else None
        if not choice:
            return "", []

        text = choice.message.content or ""
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                })
        return text, tool_calls

    def _resolve_openai_creds(self) -> tuple:
        """Resolve (api_key, base_url) for the current OpenAI-compatible provider."""
        s = self._settings
        prov = self._provider_lower
        creds = {
            "openai":       (s.get("openai_key", ""),       None),
            "groq":         (s.get("groq_key", ""),         "https://api.groq.com/openai/v1"),
            "openrouter":   (s.get("openrouter_key", ""),   "https://openrouter.ai/api/v1"),
            "kilocode":     (s.get("kilo_key", ""),         "https://api.kilo.ai/api/gateway"),
            "gemini":       (s.get("gemini_key", ""),       "https://generativelanguage.googleapis.com/v1beta/openai/"),
            "google gemini":(s.get("gemini_key", ""),       "https://generativelanguage.googleapis.com/v1beta/openai/"),
        }
        key, url = creds.get(prov, ("", None))
        return (key or "").strip(), url

    def _validated_messages_openai(self) -> list:
        """Return messages in OpenAI-compatible format."""
        validated = []
        for m in self._messages:
            role = m.get("role")
            if role == "tool":
                validated.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", "unknown"),
                    "content": m.get("content", ""),
                })
            elif role == "assistant" and "tool_calls" in m:
                validated.append(m)
            else:
                validated.append({"role": role, "content": m.get("content", "") or ""})
        return validated

    def _validated_messages_for_ollama(self) -> list:
        """Build chat messages for Ollama; tool ``function.arguments`` must be dict.

        History is stored OpenAI-style with ``arguments`` as JSON strings.
        Ollama's client (Pydantic v2) rejects strings and requires Mapping — mirror
        ``OllamaLLMService._normalize_tool_call_arguments``.
        """
        validated: List[Dict[str, Any]] = []
        for m in self._messages:
            role = m.get("role")
            if role == "tool":
                validated.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", "unknown"),
                    "content": m.get("content", ""),
                })
            elif role == "assistant" and "tool_calls" in m:
                fixed_calls: List[Dict[str, Any]] = []
                for tc in m["tool_calls"]:
                    if not isinstance(tc, dict):
                        fixed_calls.append(tc)
                        continue
                    func = tc.get("function")
                    if not isinstance(func, dict):
                        fixed_calls.append(tc)
                        continue
                    args = func.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args.strip() else {}
                        except (json.JSONDecodeError, ValueError, TypeError):
                            args = {}
                    elif args is None:
                        args = {}
                    fixed_calls.append({
                        **tc,
                        "function": {**func, "arguments": args},
                    })
                validated.append({**m, "tool_calls": fixed_calls})
            else:
                validated.append({"role": role, "content": m.get("content", "") or ""})
        return validated

    # ------------------------------------------------------------------
    #  Ollama
    # ------------------------------------------------------------------

    def _call_ollama(self) -> tuple:
        from ollama import Client as OllamaClient

        ollama_url = self._settings.get("ollama_url", "http://localhost:11434/")
        client = OllamaClient(host=ollama_url, timeout=300.0)

        tools_list = self._get_openai_tools()

        # Build messages — same shape as OpenAI but arguments must be dicts, not JSON strings.
        messages = self._validated_messages_for_ollama()

        call_kwargs = {
            "model": self._model,
            "messages": messages,
            "options": {"num_ctx": 8192, "temperature": 0.7},
        }
        if tools_list:
            call_kwargs["tools"] = tools_list

        try:
            resp = client.chat(**call_kwargs)
        except Exception as e:
            err = str(e).lower()
            if tools_list and ("does not support tools" in err or ("400" in err and "tool" in err)):
                logger.warning("WorkflowAgent: Ollama model %s doesn't support tools, retrying without", self._model)
                call_kwargs.pop("tools", None)
                resp = client.chat(**call_kwargs)
            else:
                raise

        text = resp.get("message", {}).get("content", "") if isinstance(resp, dict) else getattr(resp, "message", {}).get("content", "")
        tool_calls = []

        # Ollama returns tool_calls in the message
        msg = resp.get("message", {}) if isinstance(resp, dict) else getattr(resp, "message", {})
        raw_calls = msg.get("tool_calls", []) if isinstance(msg, dict) else getattr(msg, "tool_calls", [])
        for tc in (raw_calls or []):
            if isinstance(tc, dict):
                func = tc.get("function", {})
                tool_calls.append({
                    "id": f"ollama_{id(tc)}",
                    "name": func.get("name", ""),
                    "arguments": func.get("arguments", {}),
                })
            else:
                # ollama library object
                func = getattr(tc, "function", None)
                if func:
                    tool_calls.append({
                        "id": f"ollama_{id(tc)}",
                        "name": getattr(func, "name", ""),
                        "arguments": getattr(func, "arguments", {}),
                    })

        return text, tool_calls

    # ------------------------------------------------------------------
    #  Anthropic
    # ------------------------------------------------------------------

    def _call_anthropic(self) -> tuple:
        from anthropic import Anthropic

        api_key = (self._settings.get("anthropic_key") or "").strip()
        if not api_key:
            return "Error: Anthropic API key not configured", []

        client = Anthropic(api_key=api_key)
        tools_list = self._get_anthropic_tools()

        # Build messages — separate system from conversation
        system_text = ""
        anthropic_messages = []
        for m in self._messages:
            role = m.get("role")
            if role == "system":
                system_text = m.get("content", "")
            elif role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", "unknown"), "content": m.get("content", "")}],
                })
            elif role == "assistant" and "tool_calls" in m:
                content = []
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    args = tc.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, ValueError):
                            args = {}
                    content.append({"type": "tool_use", "id": tc.get("id", "unknown"), "name": tc["name"], "input": args})
                anthropic_messages.append({"role": "assistant", "content": content})
            else:
                anthropic_messages.append({"role": role, "content": m.get("content", "") or ""})

        call_kwargs = {"model": self._model, "max_tokens": 4096, "messages": anthropic_messages}
        if system_text:
            call_kwargs["system"] = system_text
        if tools_list:
            call_kwargs["tools"] = tools_list

        resp = client.messages.create(**call_kwargs)

        text_parts = []
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "arguments": block.input})

        return "\n".join(text_parts), tool_calls

    def _get_anthropic_tools(self) -> list:
        """Convert tools to Anthropic format."""
        if not self._tools:
            return []
        try:
            from distr.core.agent.services.llm.providers.anthropic import convert_tools_to_anthropic_format
            return convert_tools_to_anthropic_format(self._tools) or []
        except Exception as exc:
            logger.warning("WorkflowAgent: could not convert tools to Anthropic format: %s", exc)
            return []

    # ------------------------------------------------------------------
    #  Shared tool format helpers
    # ------------------------------------------------------------------

    def _get_openai_tools(self) -> list:
        """Convert tools to OpenAI function-calling format."""
        if not self._tools:
            return []
        try:
            from distr.core.agent.services.llm.tool_format import convert_tools_to_openai_format
            return convert_tools_to_openai_format(self._tools) or []
        except Exception as exc:
            logger.warning("WorkflowAgent: could not convert tools: %s", exc)
            return []

    def _append_assistant_with_tool_calls(self, text: str, tool_calls: list) -> None:
        """Append assistant message with tool calls in the right format for the provider."""
        prov = self._provider_lower
        if prov == "anthropic":
            content = []
            if text:
                content.append({"type": "text", "text": text})
            for tc in tool_calls:
                args = tc.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, ValueError):
                        args = {}
                content.append({"type": "tool_use", "id": tc.get("id", "unknown"), "name": tc["name"], "input": args})
            self._messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        else:
            # OpenAI-compatible format
            msg = {"role": "assistant", "content": text or ""}
            formatted_calls = []
            for tc in tool_calls:
                args = tc.get("arguments", {})
                if isinstance(args, dict):
                    args = json.dumps(args)
                formatted_calls.append({
                    "id": tc.get("id", f"call_{id(tc)}"),
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": args},
                })
            msg["tool_calls"] = formatted_calls
            self._messages.append(msg)

    def _append_tool_result(self, tc: dict, tool_name: str, result: str) -> None:
        """Append tool result in the right format for the provider."""
        self._messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id", "unknown"),
            "name": tool_name,
            "content": str(result),
        })

    # ------------------------------------------------------------------
    #  Tool execution helpers
    # ------------------------------------------------------------------

    def _execute_tool_call(self, tool_name: str, tool_args: dict) -> str:
        """Execute a single tool call synchronously and return the result."""
        tool = self._tools_dict.get(tool_name)
        if not tool:
            return f"Error: Tool '{tool_name}' not found"
        try:
            # Strip LLM-hallucinated extra keys not in the tool's schema to prevent
            # Pydantic validation errors ("Input should be a valid dictionary" / extra fields).
            filtered_args = tool_args
            if hasattr(tool, "args_schema") and tool.args_schema is not None:
                try:
                    schema_fields = set(tool.args_schema.model_fields.keys())
                    filtered_args = {k: v for k, v in tool_args.items() if k in schema_fields}
                except Exception:
                    pass
            result = tool._run(**filtered_args)
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
