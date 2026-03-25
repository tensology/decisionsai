"""
Function Keys Tools for LangChain.

These tools handle function key presses (F1-F12).
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging

logger = logging.getLogger(__name__)


class FunctionKeyTool(BaseTool):
    """Tool for pressing function keys (F1-F12)."""
    
    name: str = "function_key"
    description: str = """EXECUTE function key presses: F1 through F12.
    
    CRITICAL: When user asks to press F1, F2, etc. - CALL THIS TOOL IMMEDIATELY.
    DO NOT explain. DO NOT describe. JUST CALL IT.
    
    Keys: F1, F2, F3, F4, F5, F6, F7, F8, F9, F10, F11, F12.
    
    CALL THE TOOL - never explain it."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
    
    def _run(self, key: str = "", text: str = "", **kwargs) -> str:
        """Execute function key press."""
        try:
            from distr.core.actions.keyboard import press_keys
            
            # Extract function key number from text if not provided
            if not key and text:
                text_lower = text.lower().strip()
                # Try to extract F number
                import re
                match = re.search(r'f\s*(\d+)', text_lower)
                if match:
                    key = f"F{match.group(1)}"
                else:
                    # Try to find number
                    numbers = re.findall(r'\d+', text_lower)
                    if numbers:
                        key = f"F{numbers[0]}"
            
            # Validate and format key
            if not key:
                return "Error: No function key specified. Use F1 through F12"
            
            # Normalize key format
            key = key.upper().strip()
            if not key.startswith('F'):
                if key.isdigit():
                    key = f"F{key}"
                else:
                    return f"Error: Invalid function key '{key}'. Use F1 through F12"
            
            # Validate key number
            try:
                key_num = int(key[1:])
                if key_num < 1 or key_num > 12:
                    return f"Error: Function key number must be between 1 and 12, got {key_num}"
            except ValueError:
                return f"Error: Invalid function key number in '{key}'"
            
            press_keys([key])
            
            return f"Pressed {key}"
            
        except Exception as e:
            logger.error(f"Error in FunctionKeyTool: {e}", exc_info=True)
            return f"Error pressing function key: {str(e)}"
    
    async def _arun(self, key: str = "", text: str = "", **kwargs) -> str:
        # Filter out any unexpected arguments (like 'transcription' from Ollama)
        return self._run(key=key, text=text)

