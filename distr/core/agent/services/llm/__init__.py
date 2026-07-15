"""Lazy LLM service exports.

Provider modules are intentionally loaded only when selected. Importing the
LLM package is part of voice-worker startup and must not initialize every
available provider.
"""

from __future__ import annotations

import importlib


_EXPORTS = {
    "BaseLLMService": ".base_service",
    "OpenAICompatibleLLMService": ".openai_compat",
    "OllamaLLMService": ".providers.ollama",
    "OpenAILLMService": ".providers.openai",
    "AnthropicLLMService": ".providers.anthropic",
    "GroqLLMService": ".providers.groq",
    "OpenRouterLLMService": ".providers.openrouter",
    "KiloCodeLLMService": ".providers.kilocode",
    "GeminiLLMService": ".providers.gemini",
    "NvidiaLLMService": ".providers.nvidia",
    "LLMSharedMixin": ".core_mixin",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
