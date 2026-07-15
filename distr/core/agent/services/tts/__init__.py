"""Lazy TTS service exports."""

from __future__ import annotations

import importlib


_EXPORTS = {
    "OpenAITTSService": ".openai",
    "KokoroTTSService": ".kokoro",
    "ElevenLabsTTSService": ".elevenlabs",
    "SupertonicTTSService": ".supertonic",
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
