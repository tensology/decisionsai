"""Streaming Anthropic adapter for native Development turns."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from distr.core.turn_runtime.contracts import ModelResponse, ModelToolCall


class AnthropicModelAdapter:
    adapter_id = "anthropic"

    def __init__(self, *, model: str, settings: dict[str, Any], client: Any | None = None) -> None:
        self.model = str(model or "").strip()
        if not self.model or self.model == "auto":
            raise ValueError("Native Anthropic execution requires a concrete model.")
        api_key = str(settings.get("anthropic_key") or "").strip()
        if not api_key:
            raise ValueError("No API key is configured for anthropic.")
        if client is None:
            from anthropic import Anthropic

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
            if role == "assistant" and message.get("tool_calls"):
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
                normalized.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": str(message.get("tool_call_id") or ""),
                                "content": str(message.get("content") or ""),
                            }
                        ],
                    }
                )
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
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": normalized,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = self._tools(tools)
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
            finish_reason=str(getattr(final, "stop_reason", "stop") or "stop"),
            usage=dict(usage or {}),
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> ModelResponse:
        return await asyncio.to_thread(self._complete_sync, messages, tools, on_delta)
