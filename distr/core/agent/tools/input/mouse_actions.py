"""
Mouse Action Tools for LangChain.

Handles mouse clicks (left, right, double) and scrolling.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import pyautogui
from distr.core.agent.services.computer_use_context import record_action
# Disable pyautogui FAILSAFE to prevent mouse operations from being blocked
pyautogui.FAILSAFE = False

logger = logging.getLogger(__name__)


class MouseActionsTool(BaseTool):
    """Tool for mouse clicks and scrolling."""
    
    name: str = "mouse_actions"
    description: str = """EXECUTE mouse clicks and scrolling: click, double click, right click, scroll up/down.
    
    CRITICAL: When user asks to click or scroll - CALL THIS TOOL IMMEDIATELY.
    DO NOT explain. DO NOT describe. JUST CALL IT.
    
    Actions: click, double_click, right_click, scroll_up, scroll_down.
    
    CALL THE TOOL - never explain it."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
        
    def get_triggers(self) -> list[str]:
        """Get triggers for mouse actions."""
        return [
            "double click", "double-click",
            "right click", "right-click",
            "click",
            "scroll up", "scroll down"
        ]
    
    def _run(self, action: str = "", text: str = "", **kwargs) -> str:
        """Execute mouse click or scroll action directly using pyautogui."""
        try:
            logger.info(f"MouseActionsTool._run called with action='{action}', text='{text}'")
            
            # Extract action from text if not provided
            if not action and text:
                text_lower = text.lower().strip()
                logger.info(f"Extracting action from text: '{text_lower}'")
                
                # Check for click actions
                if "double click" in text_lower or "double-click" in text_lower:
                    action = "double_click"
                    logger.info("Detected: double_click")
                elif "right click" in text_lower or "right-click" in text_lower:
                    action = "right_click"
                    logger.info("Detected: right_click")
                elif "click" in text_lower:
                    action = "click"
                    logger.info("Detected: click")
                # Check for scroll actions
                elif "scroll up" in text_lower:
                    action = "scroll_up"
                    logger.info("Detected: scroll_up")
                elif "scroll down" in text_lower:
                    action = "scroll_down"
                    logger.info("Detected: scroll_down")
            
            if not action:
                logger.warning(f"Could not extract action from: action='{action}', text='{text}'")
                return "Error: No action specified. Available: click, double_click, right_click, scroll_up, scroll_down"
            
            logger.info(f"Executing mouse action: {action}")
            
            # Handle click actions
            if action in ["click", "double_click", "right_click"]:
                try:
                    if action == "click":
                        pyautogui.click(button='left')
                        record_action("click", "success", {"source": "mouse_actions"})
                        logger.info("Performed left click")
                        return "Performed left click"
                    elif action == "double_click":
                        pyautogui.doubleClick(button='left')
                        record_action("double_click", "success", {"source": "mouse_actions"})
                        logger.info("Performed double click")
                        return "Performed double click"
                    elif action == "right_click":
                        pyautogui.click(button='right')
                        record_action("right_click", "success", {"source": "mouse_actions"})
                        logger.info("Performed right click")
                        return "Performed right click"
                except Exception as e:
                    logger.error(f"Error executing mouse click: {e}", exc_info=True)
                    return f"Error performing click: {str(e)}"
            
            # Handle scroll actions
            elif action in ["scroll_up", "scroll_down"]:
                try:
                    amount = 100 if action == "scroll_up" else -100
                    pyautogui.scroll(amount)
                    direction = action.split('_')[1]
                    record_action(action, "success", {"source": "mouse_actions", "amount": amount})
                    logger.info(f"Scrolled {direction} by {amount}")
                    return f"Scrolled {direction}"
                except Exception as e:
                    logger.error(f"Error executing mouse scroll: {e}", exc_info=True)
                    return f"Error scrolling: {str(e)}"
            
            else:
                return f"Error: Unknown action '{action}'"
            
        except Exception as e:
            logger.error(f"Error in MouseActionsTool: {e}", exc_info=True)
            return f"Error controlling mouse: {str(e)}"
    
    async def _arun(self, action: str = "", text: str = "", **kwargs) -> str:
        return self._run(action=action, text=text)

