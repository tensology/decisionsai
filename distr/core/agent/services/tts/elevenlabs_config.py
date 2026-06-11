"""Shared ElevenLabs TTS configuration."""

import os

ELEVENLABS_TTS_MODEL_ID = os.getenv(
    "DECISIONS_ELEVENLABS_TTS_MODEL_ID",
    "eleven_flash_v2_5",
)
