"""Authenticated speech injection for external bots and local integrations."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from distr.gui.web.security import rate_limiter, require_internal_token_request

logger = logging.getLogger(__name__)

MAX_INJECTED_TEXT_LENGTH = 4_000
INJECT_RATE_LIMIT = 30
INJECT_RATE_WINDOW_SECONDS = 60


class SpeechInjectionRequest(BaseModel):
    """Text to speak through the currently active Decisions voice."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=MAX_INJECTED_TEXT_LENGTH)


router = APIRouter(prefix="/api/speech", tags=["speech"])


@router.post("/inject", status_code=status.HTTP_202_ACCEPTED)
async def inject_speech(request: Request, payload: SpeechInjectionRequest):
    """Queue text for playback through the active chat's TTS voice.

    This deliberately uses the same direct-speech command as in-app tools. It
    does not create a chat message or invoke the LLM. The active agent session
    therefore remains the source of truth for the configured voice and output
    device.
    """
    require_internal_token_request(request)

    client_host = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(
        f"speech_inject:{client_host}",
        limit=INJECT_RATE_LIMIT,
        window_seconds=INJECT_RATE_WINDOW_SECONDS,
    ):
        raise HTTPException(status_code=429, detail="Speech injection rate limit exceeded")

    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text must not be blank")

    from distr.core.signals import signal_manager

    try:
        signal_manager.speak_text_directly.emit(text)
    except Exception as exc:
        logger.error("Speech injection could not reach the agent: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="Speech service unavailable") from exc

    logger.info("Speech injection accepted: text_len=%d client=%s", len(text), client_host)
    return {"accepted": True, "text_length": len(text)}
