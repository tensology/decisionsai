"""Shared ElevenLabs TTS configuration."""

from __future__ import annotations

import os

DEFAULT_ELEVENLABS_TTS_MODEL = "eleven_flash_v2_5"

ELEVENLABS_DIALOGUE_MODELS = frozenset({"eleven_v3", "eleven_v3_conversational"})

VALID_ELEVENLABS_TTS_MODELS = (
    "eleven_flash_v2_5",
    "eleven_multilingual_v2",
    "eleven_v3",
    "eleven_v3_conversational",
)

ELEVENLABS_TTS_MODEL_OPTIONS = [
    {"id": "eleven_flash_v2_5", "name": "eleven_flash_v2_5 (default, low latency)"},
    {"id": "eleven_multilingual_v2", "name": "eleven_multilingual_v2"},
    {"id": "eleven_v3", "name": "eleven_v3"},
    {
        "id": "eleven_v3_conversational",
        "name": "eleven_v3_conversational (expressive streaming)",
    },
]


def is_elevenlabs_v3_compatibility_error(error: Exception) -> bool:
    """Return whether a pre-audio v3 rejection is safe to retry with Flash."""
    status_code = getattr(error, "status_code", None)
    if status_code == 404:
        return True
    if status_code not in (400, 422):
        return False

    body = getattr(error, "body", None)
    detail = body.get("detail") if isinstance(body, dict) else None
    provider_status = detail.get("status") if isinstance(detail, dict) else ""
    provider_message = detail.get("message") if isinstance(detail, dict) else ""
    error_text = " ".join(
        str(value or "") for value in (provider_status, provider_message, error)
    ).lower()
    return any(
        marker in error_text
        for marker in (
            "model",
            "voice",
            "setting",
            "stability",
            "unsupported",
            "not available",
        )
    )


def resolve_elevenlabs_tts_model(raw: str | None = None) -> str:
    """Resolve ElevenLabs model: settings value, then env override, then default."""
    env = (os.getenv("DECISIONS_ELEVENLABS_TTS_MODEL_ID") or "").strip()
    candidate = (raw or "").strip() or env or DEFAULT_ELEVENLABS_TTS_MODEL
    if candidate in {"eleven_turbo_v2_5", "eleven_turbo_v2"}:
        return DEFAULT_ELEVENLABS_TTS_MODEL
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
