"""
Special Keys Tools for LangChain.

These tools handle special key presses like Space, Enter, Tab, Escape, Alt, Control, Command.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging

logger = logging.getLogger(__name__)


class SpecialKeyTool(BaseTool):
    """Tool for pressing special keys."""
    
    name: str = "special_key"
    description: str = """EXECUTE special key presses: Enter, Space, Tab, Escape, Alt, Control, Command.
    
    CRITICAL: When user asks to press Enter, Space, Tab, etc. - CALL THIS TOOL IMMEDIATELY.
    DO NOT explain. DO NOT describe. JUST CALL IT.
    
    Keys: space, enter, tab, escape, alt, control, command.
    
    CALL THE TOOL - never explain it."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
        
    def get_triggers(self) -> list[str]:
        """Get triggers for special keys."""
        # NOTE: "tab" alone is removed - it conflicts with "new tab" browser command
        # Use "press tab" instead
        return [
            "press enter", "hit enter", "enter",
            "press space", "spacebar", "space",
            "press tab",  # "tab" alone removed to avoid conflict with "new tab"
            "press escape", "press esc", "escape", "esc", "cancel",
            "press alt", "alt",
            "press control", "press ctrl", "control", "ctrl",
            "press command", "press cmd", "command", "cmd"
        ]
    
    def _run(self, key: str = "", text: str = "", **kwargs) -> str:
        """Execute special key press."""
        try:
            from distr.core.actions.keyboard import press_keys
            
            # Map of key names to their key codes
            key_map = {
                "space": "space",
                "spacebar": "space",
                "enter": "enter",
                "tab": "tab",
                "escape": "escape",
                "esc": "escape",
                "alt": "alt",
                "control": "ctrl",
                "ctrl": "ctrl",
                "command": "command",
                "cmd": "command"
            }
            
            # Extract key from text if not provided
            if not key and text:
                text_lower = text.lower().strip()
                logger.info(f"Extracting key from text: '{text_lower}'")
                if "press enter" in text_lower or "hit enter" in text_lower or ("press" in text_lower and "enter" in text_lower):
                    key = "enter"
                elif "press space" in text_lower or "spacebar" in text_lower or ("press" in text_lower and "space" in text_lower):
                    key = "space"
                elif "press tab" in text_lower or (text_lower.strip() == "tab"):
                    key = "tab"
                elif "press escape" in text_lower or "press esc" in text_lower or "escape" in text_lower or "esc" in text_lower or "cancel" in text_lower:
                    key = "escape"
                elif "press alt" in text_lower or ("press" in text_lower and "alt" in text_lower):
                    key = "alt"
                elif "press control" in text_lower or "press ctrl" in text_lower or ("press" in text_lower and ("control" in text_lower or "ctrl" in text_lower)):
                    key = "control"
                elif "press command" in text_lower or "press cmd" in text_lower or ("press" in text_lower and ("command" in text_lower or "cmd" in text_lower)):
                    key = "command"
                elif "enter" in text_lower:
                    key = "enter"
                elif "space" in text_lower:
                    key = "space"
                elif text_lower.strip() == "tab":
                    key = "tab"
            
            if not key:
                logger.warning(f"Could not extract key from: key='{key}', text='{text}'")
                return "Error: No key specified. Available: space, enter, tab, escape, alt, control, command"
            
            logger.info(f"Executing special key press: {key}")
            
            # Get key code
            key_code = key_map.get(key.lower())
            if not key_code:
                key_code = key.lower()
                if key_code not in ["space", "enter", "tab", "escape", "alt", "ctrl", "command"]:
                    return f"Error: Unknown key '{key}'. Available keys: {', '.join(key_map.keys())}"
            
            press_keys([key_code])
            
            return f"Pressed {key_code} key"
            
        except Exception as e:
            logger.error(f"Error in SpecialKeyTool: {e}", exc_info=True)
            return f"Error pressing special key: {str(e)}"
    
    async def _arun(self, key: str = "", text: str = "", **kwargs) -> str:
        # Filter out any unexpected arguments (like 'transcription' from Ollama)
        return self._run(key=key, text=text)

