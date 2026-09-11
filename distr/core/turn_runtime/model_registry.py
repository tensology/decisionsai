"""Provider registry for Decisions-owned native model adapters."""

from __future__ import annotations

import asyncio
from typing import Any

from distr.core.turn_runtime.anthropic_adapter import AnthropicModelAdapter
from distr.core.turn_runtime.contracts import ModelAdapter
from distr.core.turn_runtime.openai_compatible import (
    OpenAICompatibleModelAdapter,
    normalize_openai_provider,
    supports_openai_compatible_provider,
)


def is_retryable_provider_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in {408, 409, 429} or (isinstance(status, int) and status >= 500):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return any(token in name or token in message for token in ("timeout", "connection", "temporarily unavailable"))


class RetryingModelAdapter:
    """Retry transient provider failures only before any response text is emitted."""

    adapter_id = "retrying"

    def __init__(self, adapter: ModelAdapter, *, max_attempts: int = 3) -> None:
        self.adapter = adapter
        self.max_attempts = max(1, int(max_attempts))

    async def complete(self, messages, tools, *, on_delta=None):
        for attempt in range(1, self.max_attempts + 1):
            emitted = False

            def relay(delta: str) -> None:
                nonlocal emitted
                emitted = emitted or bool(delta)
                if on_delta:
                    on_delta(delta)

            try:
                return await self.adapter.complete(messages, tools, on_delta=relay)
            except Exception as exc:
                if emitted or attempt >= self.max_attempts or not is_retryable_provider_error(exc):
                    raise
                await asyncio.sleep(0.25 * attempt)
        raise RuntimeError("Provider retry loop exhausted.")


def normalize_native_provider(provider: str) -> str:
    value = normalize_openai_provider(provider)
    return "anthropic" if value in {"anthropic", "claude", "claude code"} else value


def supports_native_provider(provider: str) -> bool:
    normalized = normalize_native_provider(provider)
    return normalized == "anthropic" or supports_openai_compatible_provider(normalized)


def create_native_model_adapter(
    *,
    provider: str,
    model: str,
    settings: dict[str, Any],
    client: Any | None = None,
) -> ModelAdapter:
    normalized = normalize_native_provider(provider)
    if normalized == "anthropic":
        return RetryingModelAdapter(AnthropicModelAdapter(model=model, settings=settings, client=client))
    if supports_openai_compatible_provider(normalized):
        return RetryingModelAdapter(
            OpenAICompatibleModelAdapter(
                provider=normalized,
                model=model,
                settings=settings,
                client=client,
            )
        )
    raise ValueError(f"Unsupported native model provider: {provider}")
