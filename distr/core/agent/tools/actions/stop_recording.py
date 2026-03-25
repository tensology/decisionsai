"""
Stop Recording Tool for LangChain.

This tool stops recording an action.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import logging
from distr.core.signals import signal_manager

logger = logging.getLogger(__name__)


class StopRecordingInput(BaseModel):
    """Input schema for stop_recording tool."""
    text: Optional[str] = Field(default="", description="The full user request text (optional, used for context)")

class StopRecordingTool(BaseTool):
    """
    Tool for stopping action recording.
    
    When this tool is executed:
    1. Stops the current recording process
    2. Saves the recording file
    3. Emits action_recording_stopped signal (which restores tray icon based on listening state)
    4. Tray icon changes from tray-recording.png to:
       - tray.png if listening is enabled
       - tray-disabled.png if listening is disabled
    """
    
    name: str = "stop_recording"
    description: str = """Stop recording an action.
    
    Usage:
    - "stop recording" -> Stops the current recording and saves it
    - "stop recording action" -> Same as above
    
    When recording stops, the tray icon is restored based on listening state.
    """
    args_schema: type[BaseModel] = StopRecordingInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue
            
    def get_triggers(self) -> list[str]:
        """Get triggers for stop recording."""
        return [
            "stop recording", "end recording", "finish recording",
            "stop recording action", "end recording action"
        ]
    
    def _run(self, text: str = "", **kwargs) -> str:
        """Execute stop recording."""
        try:
            if not self.event_queue:
                logger.warning("No event_queue available - cannot stop recording from agent process")
                return "Error: Cannot stop recording (no event queue)"

            # Ask the main process to check recording state and stop if active.
            # The recorder_host will log a warning and no-op if nothing is recording,
            # so we query it first via a synchronous check in the main process.
            try:
                from PyQt6.QtWidgets import QApplication
                app = QApplication.instance()
                if app and getattr(app, 'recorder_host', None):
                    rp = getattr(app.recorder_host, 'recorder_process', None)
                    if not (rp and rp.is_alive()):
                        logger.info("StopRecordingTool: no active recording to stop")
                        return "No recording is currently active."
            except Exception as e:
                # Running in agent subprocess - can't access main process QApplication directly.
                # Fall through and let recorder_host handle the no-op gracefully.
                logger.debug(f"StopRecordingTool: could not check recording state cross-process: {e}")

            self.event_queue.put(('stop_action_recording', {}))
            logger.info("Emitted stop_action_recording event via queue")
            return "Stopped recording."
        except Exception as e:
            logger.error(f"Error stopping recording: {e}", exc_info=True)
            return f"Error stopping recording: {str(e)}"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        return self._run(text=text)

