"""Fish Audio TTS models and voice defaults."""

from __future__ import annotations

DEFAULT_FISHAUDIO_TTS_MODEL = "s2.1-pro-free"
DEFAULT_FISHAUDIO_VOICE = "9a9cf47702da476aa4629e2506d4a857"
DEFAULT_FISHAUDIO_AGENT = "Fish Audio"

VALID_FISHAUDIO_TTS_MODELS = (
    "s2.1-pro-free",
    "s2.1-pro",
    "s2-pro",
    "s1",
)

FISHAUDIO_TTS_MODEL_OPTIONS = [
    {"id": "s2.1-pro-free", "name": "s2.1-pro-free (default, fair-use)"},
    {"id": "s2.1-pro", "name": "s2.1-pro"},
    {"id": "s2-pro", "name": "s2-pro"},
    {"id": "s1", "name": "s1"},
]


def resolve_fishaudio_tts_model(raw: str | None = None) -> str:
    value = (raw or "").strip()
    if value in VALID_FISHAUDIO_TTS_MODELS:
        return value
    return DEFAULT_FISHAUDIO_TTS_MODEL
