"""
Speak on Desktop tool — speaks text aloud on the desktop via TTS.

Used when the user (especially from Telegram) wants the agent to
announce something, say something, or speak text on the desktop.
"""
import logging
from typing import Any, Type

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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
            if self.event_queue:
                self.event_queue.put(("speak_on_desktop", {"text": text}), block=False)
            else:
                # Fallback: try direct signal (may fail in non-Qt threads)
                from distr.core.signals import signal_manager
                signal_manager.speak_text_directly.emit(text)
            return "Done, I've said it on the desktop."
        except Exception as e:
            logger.error("speak_on_desktop failed: %s", e, exc_info=True)
            return f"Error: {str(e)}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)
