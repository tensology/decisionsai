"""Shared ElevenLabs TTS configuration."""

from __future__ import annotations

import os

DEFAULT_ELEVENLABS_TTS_MODEL = "eleven_flash_v2_5"

VALID_ELEVENLABS_TTS_MODELS = (
    "eleven_flash_v2_5",
    "eleven_turbo_v2_5",
    "eleven_multilingual_v2",
    "eleven_v3",
)

ELEVENLABS_TTS_MODEL_OPTIONS = [
    {"id": "eleven_flash_v2_5", "name": "eleven_flash_v2_5 (default, low latency)"},
    {"id": "eleven_turbo_v2_5", "name": "eleven_turbo_v2_5"},
    {"id": "eleven_multilingual_v2", "name": "eleven_multilingual_v2"},
    {"id": "eleven_v3", "name": "eleven_v3"},
]


def resolve_elevenlabs_tts_model(raw: str | None = None) -> str:
    """Resolve ElevenLabs model: settings value, then env override, then default."""
    env = (os.getenv("DECISIONS_ELEVENLABS_TTS_MODEL_ID") or "").strip()
    candidate = (raw or "").strip() or env or DEFAULT_ELEVENLABS_TTS_MODEL
    if candidate not in VALID_ELEVENLABS_TTS_MODELS:
        # Allow unknown IDs from env for forward-compat, but prefer known settings values.
        if (raw or "").strip():
            return (raw or "").strip()
        if env:
            return env
        return DEFAULT_ELEVENLABS_TTS_MODEL
    return candidate


# Backward-compatible module constant (env or default). Prefer resolve_elevenlabs_tts_model(settings).
ELEVENLABS_TTS_MODEL_ID = resolve_elevenlabs_tts_model(None)
