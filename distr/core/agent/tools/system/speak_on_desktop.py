"""
Speak on Desktop tool — speaks text aloud on the desktop via TTS.

Used when the user (especially from Telegram) wants the agent to
announce something, say something, or speak text on the desktop.
"""
import logging
import os
from typing import Any, Type

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _telegram_desktop_tts_allowed() -> bool:
    """Allow explicit Telegram intercom requests unless explicitly disabled."""
    raw = os.environ.get("DECISIONSAI_ALLOW_TELEGRAM_DESKTOP_TTS")
    if raw is None:
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off", "disabled")


class SpeakOnDesktopInput(BaseModel):
    text: str = Field(description="The text to speak aloud on the desktop")


class SpeakOnDesktopTool(BaseTool):
    name: str = "speak_on_desktop"
    description: str = (
        "Speak text aloud on the desktop via TTS. The text will be spoken "
        "through the computer's speakers. Use when user says "
        "'tell my desktop X', 'say X on my desktop', 'announce X', "
        "'speak X out loud', 'tell whoever is there X', "
        "'say something on the desktop'. "
        "This is especially useful from Telegram as a remote intercom."
    )
    args_schema: Type[BaseModel] = SpeakOnDesktopInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def _run(self, text: str = "", **kwargs) -> str:
        text = (text or "").strip()
        if not text:
            return "No text provided to speak."
        try:
            # Set thread flag so _emit_telegram_response knows to skip screenshot
            import threading
            is_telegram_request = bool(getattr(threading.current_thread(), "telegram_request", False))
            if is_telegram_request and not _telegram_desktop_tts_allowed():
                logger.info(
                    "speak_on_desktop blocked for Telegram request "
                    "(DECISIONSAI_ALLOW_TELEGRAM_DESKTOP_TTS explicitly disabled)"
                )
                return (
                    "Desktop speech is disabled for Telegram requests in local settings."
                )
            threading.current_thread().skip_telegram_screenshot = True

            if self.event_queue:
                self.event_queue.put(("speak_on_desktop", {"text": text}), block=False)
            else:
                from distr.core.signals import signal_manager
                signal_manager.speak_text_directly.emit(text)
            return "Done"
        except Exception as e:
            logger.error("speak_on_desktop failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)
