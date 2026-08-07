"""OpenAI Realtime speech-to-speech (S2S) helpers.

Conversation Realtime models are the S2S brain. They must never be passed to
Chat Completions. Dictation keeps ``transcription_model`` (UI may lock the
control while S2S is active; value is preserved).
"""

from __future__ import annotations

from typing import Any

# GA conversation Realtime models (not translate, not transcription-only).
OPENAI_S2S_MODEL_IDS = frozenset(
    {
        "gpt-realtime-2.1",
        "gpt-realtime-2.1-mini",
        "gpt-realtime-2",
        "gpt-realtime-1.5",
        "gpt-realtime",
    }
)

OPENAI_REALTIME_VOICES = frozenset(
    {
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "sage",
        "shimmer",
        "verse",
        "marin",
        "cedar",
    }
)

_DEFAULT_REALTIME_VOICE = "marin"


def is_openai_s2s_model(model_id: str | None) -> bool:
    """True for OpenAI Realtime conversation (speech-to-speech) models."""
    if not model_id:
        return False
    mid = str(model_id).strip().lower()
    if not mid:
        return False
    # Transcription / translation Realtime products are not conversational S2S
    if "translate" in mid or "transcribe" in mid or "whisper" in mid:
        return False
    if mid in OPENAI_S2S_MODEL_IDS:
        return True
    # Snapshots / aliases that start with an allowlisted id
    for base in sorted(OPENAI_S2S_MODEL_IDS, key=len, reverse=True):
        if mid.startswith(base + "-") or mid.startswith(base + "@"):
            return True
    return False


def s2s_ui_locks(model_id: str | None) -> dict[str, Any]:
    """UI lock matrix when conversational/chat model is S2S.

    Conversational *model* stays unlocked (escape hatch). Provider, STT, and
    chained TTS provider/model locks engage; voice set becomes Realtime-only.
    """
    active = is_openai_s2s_model(model_id)
    return {
        "s2s_active": active,
        "lock_stt": active,
        "lock_conversational_provider": active,
        "lock_tts_provider": active,
        "lock_openai_tts_model": active,
        "lock_conversational_model": False,
        "voice_set": sorted(OPENAI_REALTIME_VOICES) if active else None,
        "default_voice": _DEFAULT_REALTIME_VOICE if active else None,
    }


def completions_model_for_chat(
    chat_model: str | None,
    global_conversational_model: str | None,
    *,
    fallback: str = "gpt-4o",
) -> str:
    """Model id safe for Chat Completions when chat may be S2S."""
    global_m = (global_conversational_model or "").strip()
    chat_m = (chat_model or "").strip()
    if is_openai_s2s_model(chat_m):
        if global_m and not is_openai_s2s_model(global_m):
            return global_m
        return fallback if not is_openai_s2s_model(fallback) else "gpt-4o"
    return chat_m or global_m or fallback


def coerce_realtime_voice(voice: str | None) -> str:
    v = (voice or "").strip().lower()
    if v in OPENAI_REALTIME_VOICES:
        return v
    return _DEFAULT_REALTIME_VOICE


def apply_s2s_voice_defaults(
    *,
    model_name: str | None,
    voice_provider: str | None,
    voice_model: str | None,
) -> tuple[str | None, str | None, str | None]:
    """If model is S2S, force OpenAI + Realtime voice. Returns (provider, voice_provider, voice_model)."""
    if not is_openai_s2s_model(model_name):
        return None, voice_provider, voice_model
    return "openai", "openai", coerce_realtime_voice(voice_model)


def strip_realtime_from_settings_models(settings: dict) -> bool:
    """Remove Realtime ids from global conversational/agent/llm fields. Returns True if changed."""
    changed = False
    for key in ("conversational_llm_model", "agent_model", "llm_model"):
        val = (settings.get(key) or "").strip()
        if is_openai_s2s_model(val):
            settings[key] = ""
            changed = True
    return changed


def assert_not_s2s_for_completions(model_id: str | None) -> str:
    """Raise ValueError if *model_id* is a Realtime S2S model (must not hit Completions)."""
    if is_openai_s2s_model(model_id):
        raise ValueError(
            f"OpenAI Realtime model {model_id!r} cannot be used with Chat Completions"
        )
    return (model_id or "").strip()
