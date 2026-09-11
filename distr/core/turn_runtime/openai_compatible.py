"""Streaming OpenAI-compatible model adapter for native Development turns."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from distr.core.turn_runtime.contracts import ModelResponse, ModelToolCall


_PROVIDERS = {
    "openai": ("openai_key", None),
    "groq": ("groq_key", "https://api.groq.com/openai/v1"),
    "openrouter": ("openrouter_key", "https://openrouter.ai/api/v1"),
    "kilocode": ("kilo_key", "https://api.kilo.ai/api/gateway"),
    "gemini": ("gemini_key", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    "nvidia": ("nvidia_key", "https://integrate.api.nvidia.com/v1"),
    "ollama": (None, "http://127.0.0.1:11434/v1"),
}


def normalize_openai_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    aliases = {
        "kilo": "kilocode",
        "kilo code": "kilocode",
        "google": "gemini",
        "google gemini": "gemini",
    }
    return aliases.get(value, value)


def supports_openai_compatible_provider(provider: str) -> bool:
    return normalize_openai_provider(provider) in _PROVIDERS


class OpenAICompatibleModelAdapter:
    """Normalize streaming text and tool calls from OpenAI-compatible providers."""

    adapter_id = "openai_compatible"

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        settings: dict[str, Any],
        client: Any | None = None,
    ) -> None:
        self.provider = normalize_openai_provider(provider)
        self.model = str(model or "").strip()
        if self.provider not in _PROVIDERS:
            raise ValueError(f"Unsupported native model provider: {provider}")
        if not self.model or self.model == "auto":
            raise ValueError("Native model execution requires a concrete model.")
        key_name, base_url = _PROVIDERS[self.provider]
        api_key = "ollama" if key_name is None else str(settings.get(key_name) or "").strip()
        if not api_key:
            raise ValueError(f"No API key is configured for {self.provider}.")
        if client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = OpenAI(**kwargs)
        self._client = client

    @staticmethod
    def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            if role == "assistant" and message.get("tool_calls"):
                normalized.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": [
                            {
                                "id": str(call.get("id") or ""),
                                "type": "function",
                                "function": {
                                    "name": str(call.get("name") or ""),
                                    "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                                },
                            }
                            for call in message.get("tool_calls") or []
                        ],
                    }
                )
            elif role == "tool":
                normalized.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(message.get("tool_call_id") or ""),
                        "content": str(message.get("content") or ""),
                    }
                )
            else:
                normalized.append({"role": role, "content": message.get("content") or ""})
        return normalized

    @staticmethod
    def _token_parameter(model: str) -> tuple[str, int]:
        lower = model.lower()
        key = "max_completion_tokens" if lower.startswith(("gpt-5", "o1", "o3", "o4")) else "max_tokens"
        return key, 8192

    def _complete_sync(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], None] | None,
    ) -> ModelResponse:
        token_key, token_value = self._token_parameter(self.model)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(messages),
            "stream": True,
            token_key: token_value,
        }
        if tools:
            kwargs["tools"] = tools
        stream = self._client.chat.completions.create(**kwargs)
        text_parts: list[str] = []
        pending_calls: dict[int, dict[str, str]] = {}
        finish_reason = "stop"
        usage: dict[str, Any] = {}

        for chunk in stream:
            raw_usage = getattr(chunk, "usage", None)
            if raw_usage:
                if hasattr(raw_usage, "model_dump"):
                    usage = dict(raw_usage.model_dump() or {})
                elif isinstance(raw_usage, dict):
                    usage = dict(raw_usage)
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = str(choice.finish_reason)
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None) or ""
            if content:
                text_parts.append(str(content))
                if on_delta:
                    on_delta(str(content))
            for raw_call in getattr(delta, "tool_calls", None) or []:
                index = int(getattr(raw_call, "index", 0) or 0)
                entry = pending_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if getattr(raw_call, "id", None):
                    entry["id"] = str(raw_call.id)
                function = getattr(raw_call, "function", None)
                if function is not None:
                    if getattr(function, "name", None):
                        entry["name"] += str(function.name)
                    if getattr(function, "arguments", None):
                        entry["arguments"] += str(function.arguments)

        tool_calls: list[ModelToolCall] = []
        for index, call in sorted(pending_calls.items()):
            raw_arguments = call["arguments"].strip()
            try:
                arguments = json.loads(raw_arguments) if raw_arguments else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                arguments = {"_raw": raw_arguments}
            tool_calls.append(
                ModelToolCall(
                    call_id=call["id"] or f"call-{index}",
                    name=call["name"],
                    arguments=arguments if isinstance(arguments, dict) else {"value": arguments},
                )
            )
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tuple(tool_calls),
            finish_reason=finish_reason,
            usage=usage,
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        return await asyncio.to_thread(self._complete_sync, messages, tools, on_delta)
