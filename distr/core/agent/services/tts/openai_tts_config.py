"""Shared OpenAI TTS model / voice configuration."""

from __future__ import annotations

import os

# Default stays tts-1 so existing installs do not change until the user picks otherwise.
DEFAULT_OPENAI_TTS_MODEL = "tts-1"

VALID_OPENAI_TTS_MODELS = (
    "tts-1",
    "tts-1-hd",
    "gpt-4o-mini-tts",
)

OPENAI_TTS_MODEL_OPTIONS = [
    {"id": "tts-1", "name": "tts-1 (low latency)"},
    {"id": "tts-1-hd", "name": "tts-1-hd (higher quality)"},
    {"id": "gpt-4o-mini-tts", "name": "gpt-4o-mini-tts (steerable)"},
]

# Smaller set for legacy tts-1 / tts-1-hd.
OPENAI_TTS_VOICES_LEGACY = frozenset(
    {"alloy", "ash", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"}
)

# gpt-4o-mini-tts adds ballad, verse, marin, cedar (and keeps the legacy set).
OPENAI_TTS_VOICES_GPT4O_MINI = OPENAI_TTS_VOICES_LEGACY | {
    "ballad",
    "verse",
    "marin",
    "cedar",
}

# Models that accept the Speech API `instructions` parameter.
OPENAI_TTS_MODELS_WITH_INSTRUCTIONS = frozenset({"gpt-4o-mini-tts"})


def resolve_openai_tts_model(raw: str | None = None) -> str:
    """Resolve a TTS model id from settings / env / default."""
    env = (os.getenv("DECISIONS_OPENAI_TTS_MODEL_ID") or "").strip()
    candidate = (raw or "").strip() or env or DEFAULT_OPENAI_TTS_MODEL
    if candidate not in VALID_OPENAI_TTS_MODELS:
        return DEFAULT_OPENAI_TTS_MODEL
    return candidate


def voices_for_openai_tts_model(model: str | None) -> frozenset[str]:
    """Return the built-in voice ids valid for the given Speech API model."""
    mid = resolve_openai_tts_model(model)
    if mid in OPENAI_TTS_MODELS_WITH_INSTRUCTIONS:
        return OPENAI_TTS_VOICES_GPT4O_MINI
    return OPENAI_TTS_VOICES_LEGACY


def openai_tts_supports_instructions(model: str | None) -> bool:
    return resolve_openai_tts_model(model) in OPENAI_TTS_MODELS_WITH_INSTRUCTIONS


if __name__ == "__main__":
    # ponytail: smallest check that fails if model/voice gating breaks
    assert resolve_openai_tts_model(None) == DEFAULT_OPENAI_TTS_MODEL
    assert resolve_openai_tts_model("gpt-4o-mini-tts") == "gpt-4o-mini-tts"
    assert "marin" in voices_for_openai_tts_model("gpt-4o-mini-tts")
    assert "marin" not in voices_for_openai_tts_model("tts-1")
    assert openai_tts_supports_instructions("gpt-4o-mini-tts")
    assert not openai_tts_supports_instructions("tts-1")
    print("openai_tts_config: ok")
