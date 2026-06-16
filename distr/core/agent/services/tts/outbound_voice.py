"""Resolve which TTS provider/voice to use for outbound voice (Telegram, remote, tools)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_LEGACY_VOICE_DELIVERY_ALIASES = frozenset({"kokoro", "tool", "tts", "voice"})


def is_voice_delivery_provider(provider: str | None) -> bool:
    """Return whether an event-queue provider value supports voice delivery."""
    raw = (provider or "").strip().lower()
    if not raw:
        return False
    if raw in _LEGACY_VOICE_DELIVERY_ALIASES:
        return True
    try:
        from distr.core.agent.constants import normalize_voice_provider
        from distr.core.agent.services.tts.registry import tts_registry

        tts_registry.get(normalize_voice_provider(raw))
        return True
    except Exception:
        return False


def resolve_outbound_voice_settings(settings: dict[str, Any] | None = None) -> tuple[str, str, str]:
    """Return (provider_id, voice_id, provider_label) for the active chat or global settings."""
    from distr.core.agent.constants import normalize_voice_provider
    from distr.core.agent.services.tts.registry import tts_registry
    from distr.core.settings import load_settings_from_db

    settings = settings or load_settings_from_db()
    chat_voice_provider = ""
    chat_voice_model = ""

    try:
        from distr.core.db import Chat, get_session

        chat_id = settings.get("agent_current_chat_id")
        if chat_id:
            with get_session() as session:
                chat = session.query(Chat).filter(Chat.id == int(chat_id)).first()
                if chat:
                    root = chat
                    while root.parent_id:
                        parent = session.query(Chat).filter(Chat.id == root.parent_id).first()
                        if not parent:
                            break
                        root = parent
                    chat_voice_provider = (root.voice_provider or "").strip()
                    chat_voice_model = (root.voice_model or "").strip()
    except Exception as exc:
        logger.debug("Could not load chat voice settings: %s", exc)

    provider_label = chat_voice_provider or settings.get("tts_provider", "Kokoro (Offline)")
    provider_id = normalize_voice_provider(provider_label)

    if chat_voice_model:
        voice_id = chat_voice_model
    else:
        try:
            descriptor = tts_registry.get(provider_id)
            voice_id = descriptor.get_telegram_voice_id(settings)
        except KeyError:
            voice_id = ""

    return provider_id, voice_id or "", provider_label


def voice_delivery_provider_for_event(settings: dict[str, Any] | None = None) -> str:
    """Canonical provider id to attach to outbound voice events."""
    provider_id, _, _ = resolve_outbound_voice_settings(settings)
    return provider_id
