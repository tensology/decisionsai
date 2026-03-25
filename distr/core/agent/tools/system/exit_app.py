"""
Exit Application Tool for LangChain.

This tool says goodbye to the user and then exits the application.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import asyncio
import time

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
    - "quit application" -> CALL immediately
    - "exit application" -> CALL immediately
    
    DO NOT CALL FOR:
    - "goodbye" (just conversation)
    - "bye" (just conversation)
    - "see you later" (just conversation)
    - "farewell" (just conversation)
    - Any casual farewell without explicit exit intent
    
    CALL THE TOOL - never describe it."""
    
    llm_service: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, llm_service=None, **kwargs):
        super().__init__(**kwargs)
        self._llm_service = llm_service
    
    def _run(self, text: str = "", **kwargs) -> str:
        """Execute exit application action."""
        try:
            logger.info("Exit app: Saying goodbye and exiting application")
            
            # Return goodbye message - the LLM service will handle TTS and force quit
            goodbye = "Goodbye! It was great helping you today."
            
            # Also emit the exit signal as a backup
            try:
                from distr.core.signals import signal_manager
                logger.info("Exit app: Emitting exit_app signal")
                signal_manager.exit_app.emit()
            except Exception as e:
                logger.warning(f"Error emitting exit signal: {e}")
            
            return goodbye
            
        except Exception as e:
            logger.error(f"Error in ExitAppTool: {e}", exc_info=True)
            return "Goodbye!"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        # Filter out any unexpected arguments
        return self._run(text=text)

