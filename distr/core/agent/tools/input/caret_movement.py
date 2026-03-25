"""
Caret (Cursor) Movement Tools for LangChain.

These tools handle cursor/caret movement commands like up, down, left, right, page up, etc.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import platform

logger = logging.getLogger(__name__)


class CaretMovementTool(BaseTool):
    """Tool for moving the text cursor/caret."""
    
    name: str = "caret_movement"
    description: str = """EXECUTE cursor movements: arrow keys, page up/down, home, end.
    
    CRITICAL: When user asks to move cursor or press arrow keys - CALL THIS TOOL IMMEDIATELY.
    DO NOT explain. DO NOT describe. JUST CALL IT.
    
    Movements: up, down, left, right, page_up, page_down, home, end, delete_forward.
    
    CALL THE TOOL - never explain it."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
    
    def _run(self, direction: str = "", text: str = "", **kwargs) -> str:
        """Execute caret movement."""
        try:
            from distr.core.actions.keyboard import press_keys
            
            is_mac = platform.system() == 'Darwin'
            
            # Map of direction names to their key combinations
            if is_mac:
                # Mac keyboards often use Fn + Arrows for these keys
                direction_map = {
                    "up": ["up"],
                    "down": ["down"],
                    "left": ["left"],
                    "right": ["right"],
                    "page_up": ["fn", "up"],
                    "page_down": ["fn", "down"],
                    "home": ["fn", "left"],
                    "end": ["fn", "right"],
                    "delete_forward": ["fn", "delete"]
                }
            else:
                # Windows/Linux use dedicated keys
                direction_map = {
                    "up": ["up"],
                    "down": ["down"],
                    "left": ["left"],
                    "right": ["right"],
                    "page_up": ["pageup"],
                    "page_down": ["pagedown"],
                    "home": ["home"],
                    "end": ["end"],
                    "delete_forward": ["delete"]
                }
            
            # Extract direction from text if not provided
            if not direction and text:
                text_lower = text.lower().strip()
                logger.info(f"Extracting direction from text: '{text_lower}'")
                # Try to match common phrases - check for arrow key mentions first
                if "down arrow" in text_lower or ("press" in text_lower and "down" in text_lower):
                    direction = "down"
                    logger.info("Detected: down arrow")
                elif "up arrow" in text_lower or ("press" in text_lower and "up" in text_lower and "page" not in text_lower):
                    direction = "up"
                    logger.info("Detected: up arrow")
                elif "left arrow" in text_lower or ("press" in text_lower and "left" in text_lower):
                    direction = "left"
                    logger.info("Detected: left arrow")
                elif "right arrow" in text_lower or ("press" in text_lower and "right" in text_lower):
                    direction = "right"
                    logger.info("Detected: right arrow")
                elif "page up" in text_lower:
                    direction = "page_up"
                    logger.info("Detected: page_up")
                elif "page down" in text_lower:
                    direction = "page_down"
                    logger.info("Detected: page_down")
                elif "home" in text_lower or "beginning" in text_lower or "start" in text_lower:
                    direction = "home"
                    logger.info("Detected: home")
                elif "end" in text_lower:
                    direction = "end"
                    logger.info("Detected: end")
                elif "delete forward" in text_lower:
                    direction = "delete_forward"
                    logger.info("Detected: delete_forward")
                elif "up" in text_lower and "page" not in text_lower:
                    direction = "up"
                    logger.info("Detected: up")
                elif "down" in text_lower and "page" not in text_lower:
                    direction = "down"
                    logger.info("Detected: down")
                elif "left" in text_lower:
                    direction = "left"
                    logger.info("Detected: left")
                elif "right" in text_lower:
                    direction = "right"
                    logger.info("Detected: right")
            
            if not direction:
                logger.warning(f"Could not extract direction from: direction='{direction}', text='{text}'")
                return "Error: No direction specified. Available: up, down, left, right, page_up, page_down, home, end, delete_forward"
            
            logger.info(f"Executing caret movement: {direction}")
            
            # Get key combination
            keys = direction_map.get(direction.lower())
            if not keys:
                return f"Error: Unknown direction '{direction}'. Available directions: {', '.join(direction_map.keys())}"
            
            press_keys(keys)
            
            logger.info(f"Successfully pressed {direction} arrow key")
            return f"Pressed {direction} arrow key"
            
        except Exception as e:
            logger.error(f"Error in CaretMovementTool: {e}", exc_info=True)
            return f"Error moving cursor: {str(e)}"
    
    async def _arun(self, direction: str = "", text: str = "", **kwargs) -> str:
        # Filter out any unexpected arguments (like 'transcription' from Ollama)
        return self._run(direction=direction, text=text)

