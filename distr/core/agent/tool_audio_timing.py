"""Coordinate spoken preambles with tool side effects (screenshot shutter, search chime)."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_POST_SPEECH_GAP_SEC = 0.4
MIN_PRE_TOOL_WAIT_SEC = 0.35
MAX_PRE_TOOL_WAIT_SEC = 12.0


def estimate_speech_duration(text: str, playback_speed: float = 1.0) -> float:
    """Rough seconds for TTS playback of *text* at the given speed."""
    words = len(re.findall(r"\w+", text or ""))
    if words <= 0:
        return 0.0
    speed = max(0.5, min(2.0, float(playback_speed or 1.0)))
    return (words / 2.4) / speed


async def wait_before_tool_side_effects(
    service: Any,
    spoken_text: str = "",
    *,
    end_current_utterance: bool = True,
    post_gap_sec: float = DEFAULT_POST_SPEECH_GAP_SEC,
) -> None:
    """Let any in-flight preamble TTS finish before tools play sounds or capture."""
    if getattr(service, "_is_telegram_request", False):
        return
    if not getattr(service, "_speaker_enabled", True):
        return

    text = (spoken_text or "").strip()
    if not text and not end_current_utterance:
        return

    if end_current_utterance and text:
        try:
            from distr.core.agent.libs import LLMFullResponseEndFrame

            await service.push_frame(
                LLMFullResponseEndFrame(),
                getattr(service, "_pipeline_direction", None),
            )
        except Exception as exc:
            logger.debug("wait_before_tool_side_effects: could not push EndFrame: %s", exc)

    transport = getattr(service, "_audio_transport_output", None)
    playback_speed = float(getattr(transport, "_speed", 1.0) or 1.0) if transport else 1.0
    estimated = estimate_speech_duration(text, playback_speed)

    if transport is not None and hasattr(transport, "wait_for_playback_idle"):
        try:
            timeout = min(
                max(estimated + post_gap_sec + 1.5, MIN_PRE_TOOL_WAIT_SEC + post_gap_sec),
                MAX_PRE_TOOL_WAIT_SEC,
            )
            await transport.wait_for_playback_idle(timeout=timeout)
            if post_gap_sec > 0:
                await asyncio.sleep(post_gap_sec)
            return
        except Exception as exc:
            logger.debug("wait_before_tool_side_effects: transport wait failed: %s", exc)

    wait_sec = estimated + post_gap_sec
    if text:
        wait_sec = max(wait_sec, MIN_PRE_TOOL_WAIT_SEC + post_gap_sec)
    wait_sec = min(wait_sec, MAX_PRE_TOOL_WAIT_SEC)
    if wait_sec > 0:
        await asyncio.sleep(wait_sec)


async def wait_after_tool_sound_before_tts(
    service: Any,
    *,
    gap_sec: float = DEFAULT_POST_SPEECH_GAP_SEC,
) -> None:
    """Short pause after a tool sound before speaking the tool result."""
    if getattr(service, "_is_telegram_request", False):
        return
    if not getattr(service, "_speaker_enabled", True):
        return
    if gap_sec <= 0:
        return
    await asyncio.sleep(gap_sec)
