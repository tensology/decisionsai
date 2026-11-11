"""Streaming Anthropic adapter for native Development turns."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Callable

from distr.core.turn_runtime.contracts import ModelResponse, ModelToolCall


FABLE_52_PROGRESS_INSTRUCTION = (
    "Before each batch of tool calls, send a brief user-facing progress update in ordinary response text. "
    "After the final tool result, follow the requested response format."
)


def is_fable_52(model: str) -> bool:
    return bool(re.fullmatch(r"(?:anthropic/)?claude-fable-5[-.]2(?:-\d{8})?", str(model).strip().lower()))


def fable_52_effort(value: str = "") -> str:
    return value if value in {"low", "medium", "high", "xhigh", "max"} else "medium"


class AnthropicModelAdapter:
    adapter_id = "anthropic"

    def __init__(
        self, *, model: str, settings: dict[str, Any], client: Any | None = None,
        provider: str = "anthropic", reasoning_effort: str = "",
    ) -> None:
        self.model = str(model or "").strip()
        if not self.model or self.model == "auto":
            raise ValueError("Native Anthropic execution requires a concrete model.")
        self.fable_52 = is_fable_52(self.model)
        self.effort = fable_52_effort(reasoning_effort)
        self.openrouter = provider == "openrouter"
        key_setting = "openrouter_key" if self.openrouter else "anthropic_key"
        key_env = "OPENROUTER_API_KEY" if self.openrouter else "ANTHROPIC_API_KEY"
        api_key = str(settings.get(key_setting) or os.getenv(key_env) or "").strip()
        if not api_key:
            raise ValueError(f"No API key is configured for {provider}.")
        if client is None:
            from anthropic import Anthropic

            if self.openrouter:
                client = Anthropic(auth_token=api_key, base_url="https://openrouter.ai/api", max_retries=0)
            else:
                client = Anthropic(api_key=api_key)
        self._client = client

    @staticmethod
    def _content_blocks(content: Any) -> Any:
        if not isinstance(content, list):
            return str(content or "")
        blocks: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                blocks.append({"type": "text", "text": str(item.get("text") or "")})
                continue
            if item.get("type") != "image_url":
                continue
            url = str((item.get("image_url") or {}).get("url") or "")
            match = re.match(r"^data:([^;]+);base64,(.+)$", url, re.DOTALL)
            if match:
                blocks.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": match.group(1), "data": match.group(2)},
                    }
                )
        return blocks

    @staticmethod
    def _payload(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        normalized: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            if role == "system":
                system_parts.append(str(message.get("content") or ""))
                continue
            if role == "assistant" and message.get("anthropic_content"):
                normalized.append({"role": "assistant", "content": list(message["anthropic_content"])})
            elif role == "assistant" and message.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": str(message["content"])})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(call.get("name") or ""),
                        "input": dict(call.get("arguments") or {}),
                    }
                    for call in message.get("tool_calls") or []
                )
                normalized.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                result = {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_call_id") or ""),
                    "content": str(message.get("content") or ""),
                }
                previous = normalized[-1] if normalized else {}
                blocks = previous.get("content")
                if previous.get("role") == "user" and isinstance(blocks, list) and blocks and blocks[-1]["type"] == "tool_result":
                    blocks.append(result)
                else:
                    normalized.append({"role": "user", "content": [result]})
            else:
                normalized.append(
                    {
                        "role": "assistant" if role == "assistant" else "user",
                        "content": AnthropicModelAdapter._content_blocks(message.get("content")),
                    }
                )
        return "\n\n".join(part for part in system_parts if part), normalized

    @staticmethod
    def _tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []
        for raw in tools:
            function = dict(raw.get("function") or {})
            normalized.append(
                {
                    "name": str(function.get("name") or ""),
                    "description": str(function.get("description") or ""),
                    "input_schema": dict(function.get("parameters") or {"type": "object"}),
                }
            )
        return normalized

    def _complete_sync(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], None] | None,
    ) -> ModelResponse:
        system, normalized = self._payload(messages)
        model = self.model
        if self.openrouter:
            model = "anthropic/claude-fable-5.2"
            if match := re.search(r"-\d{8}$", self.model):
                model += match.group()
        elif self.fable_52:
            model = model.removeprefix("anthropic/").replace("5.2", "5-2")
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 8192,
            "messages": normalized,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._tools(tools)
        if self.fable_52:
            extra_body: dict[str, Any] = {"output_config": {"effort": self.effort}}
            if self.openrouter:
                extra_body["provider"] = {"only": ["Anthropic"], "allow_fallbacks": False, "require_parameters": True}
            kwargs["extra_body"] = extra_body
            if tools:
                kwargs["system"] = "\n\n".join(filter(None, (system, FABLE_52_PROGRESS_INSTRUCTION)))
        with self._client.messages.stream(**kwargs) as stream:
            text_parts: list[str] = []
            for delta in stream.text_stream:
                text_parts.append(str(delta))
                if on_delta:
                    on_delta(str(delta))
            final = stream.get_final_message()
        calls = tuple(
            ModelToolCall(
                call_id=str(getattr(block, "id", "") or ""),
                name=str(getattr(block, "name", "") or ""),
                arguments=dict(getattr(block, "input", {}) or {}),
            )
            for block in (getattr(final, "content", None) or [])
            if str(getattr(block, "type", "")) == "tool_use"
        )
        usage_raw = getattr(final, "usage", None)
        usage = usage_raw.model_dump() if hasattr(usage_raw, "model_dump") else {}
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=calls,
            finish_reason=str(getattr(final, "stop_reason", None) or "stream_incomplete"),
            usage=dict(usage or {}),
            anthropic_content=tuple(
                block.model_dump(mode="json", exclude_unset=True)
                for block in final.content
            ) if self.fable_52 and calls else (),
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        return await asyncio.to_thread(self._complete_sync, messages, tools, on_delta)
