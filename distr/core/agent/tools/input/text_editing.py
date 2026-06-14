"""
Text Editing and Navigation Tools for LangChain.

These tools handle text editing commands like copy, paste, cut, undo, redo, etc.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
from distr.core.agent.tools.base import get_platform_modifier_key

logger = logging.getLogger(__name__)


class TextEditingTool(BaseTool):
    """Tool for text editing operations like copy, paste, cut, undo, redo, delete."""
    
    name: str = "text_editing"
    description: str = """MANDATORY TOOL CALL for text editing: copy, paste, cut, select all, undo, redo, delete.
    
    ⚠️ CRITICAL RULES - YOU MUST FOLLOW THESE EXACTLY:
    1. When user says "copy this" → IMMEDIATELY call this tool with operation="copy" - DO NOT generate ANY text response
    2. When user says "cut this" → IMMEDIATELY call this tool with operation="cut" - DO NOT generate ANY text response
    3. When user says "paste" or "paste this" → IMMEDIATELY call this tool with operation="paste" - DO NOT generate ANY text response
    
    FORBIDDEN RESPONSES (DO NOT SAY THESE - EVER):
    - "I've copied the text for you"
    - "I'll copy that"
    - "Here is the copied text"
    - "Pasted!"
    - "Would you like me to paste it?"
    - "Calling textediting" or "Calling text_editing"
    - "I should use textediting" or "I should use text_editing"
    - "The corrected response would be: Calling textediting..."
    - ANY text response for copy/cut/paste commands - NO EXCEPTIONS
    
    REQUIRED BEHAVIOR (NO DEVIATIONS):
    - User: "copy this" → IMMEDIATELY call tool(operation="copy") → Say "Done" (1 word only, NO OTHER TEXT)
    - User: "cut this" → IMMEDIATELY call tool(operation="cut") → Say "Done" (1 word only, NO OTHER TEXT)
    - User: "paste" → IMMEDIATELY call tool(operation="paste") → Say "Done" (1 word only, NO OTHER TEXT)
    
    Operations: copy, paste, cut, select_all, undo, redo, backspace, delete, clear_line, delete_line, force_delete.
    
    YOU MUST CALL THIS TOOL IMMEDIATELY - DO NOT EXPLAIN, DO NOT DESCRIBE, DO NOT GENERATE ANY TEXT - JUST EXECUTE THE TOOL."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
        
    def get_triggers(self) -> list[str]:
        """Get triggers for text editing."""
        return [
            "copy this", "copy", "cut this", "cut", "paste this", "paste",
            "select all", "select everything", "undo", "redo",
            "backspace", "back space", "delete line", "clear line",
            "force delete", "delete"
        ]
    
    def _run(self, operation: str = "", text: str = "", **kwargs) -> str:
        """Execute text editing operation."""
        try:
            from distr.core.actions.keyboard import press_keys
            
            cmd = get_platform_modifier_key()
            is_mac = cmd == 'command'
            
            # Platform specific key mappings
            redo_keys = [cmd, "shift", "z"] if is_mac else [cmd, "y"]
            
            # Map of operation names to their key combinations
            operation_map = {
                "copy": [cmd, "c"],
                "paste": [cmd, "v"],
                "cut": [cmd, "x"],
                "select_all": [cmd, "a"],
                "undo": [cmd, "z"],
                "redo": redo_keys,
                "backspace": ["backspace"],
                "delete": ["delete"],
                "clear_line": [{"trigger": "select all"}, {"trigger": "back space"}],
                "delete_line": [cmd, "shift", "k"],
                "force_delete": [cmd, "backspace"]
            }
            
            # Extract operation from text if not provided
            if not operation and text:
                text_lower = text.lower().strip()
                logger.info(f"Extracting operation from text: '{text_lower}'")
                # Try to match common phrases - check for "this" variants first
                if "copy this" in text_lower or ("copy" in text_lower and "this" in text_lower):
                    operation = "copy"
                    logger.info("Detected: copy (from 'copy this')")
                elif "save this to the clipboard" in text_lower or "save this to clipboard" in text_lower or ("save" in text_lower and "clipboard" in text_lower and "this" in text_lower):
                    operation = "copy"
                    logger.info("Detected: copy (from 'save this to clipboard')")
                elif "cut this" in text_lower or ("cut" in text_lower and "this" in text_lower):
                    operation = "cut"
                    logger.info("Detected: cut (from 'cut this')")
                elif "paste this" in text_lower or ("paste" in text_lower and "this" in text_lower):
                    operation = "paste"
                    logger.info("Detected: paste (from 'paste this')")
                elif "copy" in text_lower:
                    operation = "copy"
                    logger.info("Detected: copy")
                elif "paste" in text_lower:
                    operation = "paste"
                    logger.info("Detected: paste")
                elif "cut" in text_lower:
                    operation = "cut"
                    logger.info("Detected: cut")
                elif "select all" in text_lower or "select everything" in text_lower:
                    operation = "select_all"
                    logger.info("Detected: select_all")
                elif "undo" in text_lower:
                    operation = "undo"
                    logger.info("Detected: undo")
                elif "redo" in text_lower:
                    operation = "redo"
                    logger.info("Detected: redo")
                elif "backspace" in text_lower or "back space" in text_lower:
                    operation = "backspace"
                    logger.info("Detected: backspace")
                elif "delete line" in text_lower:
                    operation = "delete_line"
                    logger.info("Detected: delete_line")
                elif "clear line" in text_lower:
                    operation = "clear_line"
                    logger.info("Detected: clear_line")
                elif "force delete" in text_lower:
                    operation = "force_delete"
                    logger.info("Detected: force_delete")
                elif "delete" in text_lower:
                    operation = "delete"
                    logger.info("Detected: delete")
            
            if not operation:
                logger.warning(f"Could not extract operation from: operation='{operation}', text='{text}'")
                return "Error: No operation specified. Available: copy, paste, cut, select_all, undo, redo, backspace, delete, clear_line, delete_line, force_delete"
            
            logger.info(f"Executing text editing operation: {operation}")
            
            # Get key combination
            keys = operation_map.get(operation.lower())
            if not keys:
                return f"Error: Unknown operation '{operation}'. Available operations: {', '.join(operation_map.keys())}"
            
            press_keys(keys)
            
            return f"Executed text editing operation: {operation}"
            
        except Exception as e:
            logger.error(f"Error in TextEditingTool: {e}", exc_info=True)
            return f"Error executing text editing operation: {str(e)}"
    
    async def _arun(self, operation: str = "", text: str = "", **kwargs) -> str:
        # Filter out any unexpected arguments (like 'transcription' from Ollama)
        return self._run(operation=operation, text=text)

