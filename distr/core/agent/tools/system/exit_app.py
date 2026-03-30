"""
Exit Application Tool for LangChain.

This tool says goodbye to the user and then exits the application.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging

logger = logging.getLogger(__name__)


class ExitAppTool(BaseTool):
    """Tool for saying goodbye and exiting the application."""
    
    name: str = "exit_app"
    description: str = """EXECUTE exiting the application gracefully.
    
    CRITICAL: ONLY call this tool when the user EXPLICITLY requests to exit or quit the application.
    DO NOT call this for casual farewells like "goodbye", "bye", "see you later" - those are just conversation.
    DO NOT explain what the tool does. DO NOT describe the tool. DO NOT ask questions. JUST CALL IT.
    
    The tool automatically:
    - Says a friendly goodbye message to the user via TTS
    - Exits the application gracefully
    
    REQUIRED CALLS (EXPLICIT EXIT COMMANDS ONLY):
    - "quit this application" -> CALL immediately
    - "exit this application" -> CALL immediately
    - "close this application" -> CALL immediately
    - "quit the app" -> CALL immediately
    - "exit the app" -> CALL immediately
    - "close the app" -> CALL immediately
    
    DO NOT CALL FOR:
    - "goodbye" (just conversation)
    - "bye" (just conversation)
    - "see you later" (just conversation)
    
    CALL THE TOOL - never describe it."""
    
    llm_service: Optional[Any] = Field(default=None, exclude=True)
    event_queue: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, llm_service=None, event_queue=None, **kwargs):
        super().__init__(**kwargs)
        self._llm_service = llm_service
        self._event_queue = event_queue
    
    def _run(self, text: str = "", **kwargs) -> str:
        try:
            logger.info("Exit app: requesting exit")
            eq = self._event_queue
            if not eq and self._llm_service and hasattr(self._llm_service, 'event_queue'):
                eq = self._llm_service.event_queue
            if eq:
                # Notify Telegram/chat that we're shutting down
                eq.put(('send_to_telegram', {
                    'text': 'Goodbye! Shutting down DecisionsAI now.',
                    'is_done': True,
                    'analyzed_image_path': None,
                }), block=False)
                import time
                time.sleep(0.5)
                eq.put(('exit_app', {}), block=False)
            else:
                from distr.core.signals import signal_manager
                signal_manager.exit_app.emit()
            return "Goodbye! It was great helping you today."
        except Exception as e:
            logger.error("Error in ExitAppTool: %s", e, exc_info=True)
            return "Goodbye!"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        return self._run(text=text)
