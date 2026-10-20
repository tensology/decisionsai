from __future__ import annotations

import asyncio
from types import SimpleNamespace

from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService
from distr.core.agent.services.llm.providers.openai import OpenAILLMService


class _CompletionRecorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return object()


def _service(model_name: str):
    completions = _CompletionRecorder()
    service = object.__new__(OpenAILLMService)
    service._model_name = model_name
    service.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    return service, completions


def test_luna_chat_completions_disables_reasoning_when_tools_are_present():
    service, completions = _service("gpt-5.6-luna")

    asyncio.run(
        OpenAICompatibleLLMService._call_stream(
            service,
            [{"role": "user", "content": "How are you doing?"}],
            [{"type": "function", "function": {"name": "system_info"}}],
        )
    )

    assert completions.calls[0]["reasoning_effort"] == "none"


def test_luna_chat_completions_keeps_default_reasoning_without_tools():
    service, completions = _service("gpt-5.6-luna")

    asyncio.run(
        OpenAICompatibleLLMService._call_stream(
            service,
            [{"role": "user", "content": "How are you doing?"}],
        )
    )

    assert "reasoning_effort" not in completions.calls[0]


def test_other_openai_models_keep_default_reasoning_with_tools():
    service, completions = _service("gpt-5.6-sol")

    asyncio.run(
        OpenAICompatibleLLMService._call_stream(
            service,
            [{"role": "user", "content": "How are you doing?"}],
            [{"type": "function", "function": {"name": "system_info"}}],
        )
    )

    assert "reasoning_effort" not in completions.calls[0]
