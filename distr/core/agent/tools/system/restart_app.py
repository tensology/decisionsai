"""
Restart Application Tool.

Restarts the DecisionsAI application gracefully.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging

logger = logging.getLogger(__name__)


class RestartAppTool(BaseTool):
    """Tool for restarting the application."""

    name: str = "restart_app"
    description: str = """EXECUTE restarting the application gracefully.

    CRITICAL: ONLY call this tool when the user EXPLICITLY requests to restart the application.
    DO NOT explain what the tool does. DO NOT describe the tool. DO NOT ask questions. JUST CALL IT.

    The tool automatically:
    - Saves current state
    - Spawns a new instance
    - Exits the current instance

    REQUIRED CALLS:
    - "restart the app" -> CALL immediately
    - "restart application" -> CALL immediately
    - "restart decisions" -> CALL immediately
    - "reboot the app" -> CALL immediately

    CALL THE TOOL - never describe it."""

    llm_service: Optional[Any] = Field(default=None, exclude=True)
    event_queue: Optional[Any] = Field(default=None, exclude=True)

    def __init__(self, llm_service=None, event_queue=None, **kwargs):
        super().__init__(**kwargs)
        self._llm_service = llm_service
        self._event_queue = event_queue

    def _run(self, text: str = "", **kwargs) -> str:
        try:
            logger.info("Restart app: requesting restart")
            eq = self._event_queue
            if not eq and self._llm_service and hasattr(self._llm_service, 'event_queue'):
                eq = self._llm_service.event_queue
            if eq:
                # Notify Telegram/chat that we're restarting
                eq.put(('send_to_telegram', {
                    'text': 'Restarting now.',
                    'is_done': False,
                    'analyzed_image_path': None,
                    'skip_screenshot': True,
                }), block=False)
                import time
                time.sleep(0.5)  # Give the message time to send
                eq.put(('restart_app', {}), block=False)
            else:
                from distr.core.signals import signal_manager
                signal_manager.restart_app.emit()
            return "Restarting now."
        except Exception as e:
            logger.error("Error in RestartAppTool: %s", e, exc_info=True)
            return f"Failed to restart: {e}"

    async def _arun(self, text: str = "", **kwargs) -> str:
        return self._run(text=text)
