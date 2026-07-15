"""Lazy service exports for the voice-agent pipeline.

Importing this package used to import every STT, TTS, and LLM provider. On a
macOS spawn worker that delayed microphone readiness even when only Whisper
and one LLM provider were selected.
"""

from __future__ import annotations

import importlib
from typing import Any


_SERVICE_EXPORTS = {
    "WhisperSTTService": (".stt.whisper", True),
    "VoskSTTService": (".stt.vosk", False),
    "OpenAIWhisperSTTService": (".stt.openai", False),
    "AssemblyAISTTService": (".stt.assemblyai", False),
    "KokoroTTSService": (".tts.kokoro", True),
    "ElevenLabsTTSService": (".tts.elevenlabs", True),
    "OpenAITTSService": (".tts.openai", False),
    "CoquiTTSService": (".tts.coqui", False),
    "SupertonicTTSService": (".tts.supertonic", False),
    "OllamaLLMService": (".llm.providers.ollama", True),
    "OpenAILLMService": (".llm.providers.openai", False),
    "OpenRouterLLMService": (".llm.providers.openrouter", False),
    "AnthropicLLMService": (".llm.providers.anthropic", False),
    "GroqLLMService": (".llm.providers.groq", False),
    "KiloCodeLLMService": (".llm.providers.kilocode", False),
    "GeminiLLMService": (".llm.providers.gemini", False),
    "NvidiaLLMService": (".llm.providers.nvidia", False),
}

__all__ = list(_SERVICE_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, required = _SERVICE_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc

    try:
        module = importlib.import_module(module_name, __name__)
        value = getattr(module, name)
    except ImportError:
        if required:
            raise
        value = None

    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
