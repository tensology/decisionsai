"""
Stop Action Tool for LangChain.

This tool stops a currently playing action.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)


class StopActionInput(BaseModel):
    """Input schema for stop_action tool."""
    text: Optional[str] = Field(default="", description="The full user request text (optional, used for context)")

class StopActionTool(BaseTool):
    """Tool for stopping action playback."""
    
    name: str = "stop_action"
    description: str = """Stop a currently playing action.
    
    Usage:
    - "stop action" -> Stops the currently playing action
    """
    args_schema: type[BaseModel] = StopActionInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue
            
    def get_triggers(self) -> list[str]:
        """Get triggers for stop action."""
        return [
            "stop action", "stop the action", "cancel action", "abort action"
        ]
    
    def _run(self, text: str = "", **kwargs) -> str:
        """Execute stop action."""
        try:
            # Send event via queue (signals don't work across processes)
            if self.event_queue:
                try:
                    self.event_queue.put(('stop_action', {}))
                    logger.info("Emitted stop_action event via queue")
                    return "Stopped action."
                except Exception as e:
                    logger.error(f"Failed to emit stop_action event: {e}")
                    return f"Error stopping action: {str(e)}"
            else:
                logger.warning("No event_queue available - cannot stop action from agent process")
                return "Error: Cannot stop action (no event queue)"
        except Exception as e:
            logger.error(f"Error stopping action: {e}", exc_info=True)
            return f"Error stopping action: {str(e)}"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        return self._run(text=text)










