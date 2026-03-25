"""
Oracle Globe Control Tool for LangChain.

This tool allows changing the oracle/globe image forward and backward.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging

logger = logging.getLogger(__name__)


class OracleGlobeTool(BaseTool):
    """Tool for changing the oracle/globe image forward or backward."""
    
    name: str = "oracle_globe"
    description: str = """Change the oracle/globe image. Use this when the user wants to change the globe/oracle visual.
    
    Actions:
    - 'change_globe' or 'change_oracle': Change to the next globe/oracle image (forward)
    - 'change_previous_globe' or 'change_previous_oracle': Change to the previous globe/oracle image (backward)
    
    Examples: "change globe", "change oracle", "next globe", "change previous globe", "change previous oracle", "previous globe", "go back globe"
    
    CALL THE TOOL - never explain it."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    event_queue: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, event_queue=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
        self._event_queue = event_queue
        if event_queue is None:
            logger.warning("OracleGlobeTool initialized without event_queue - UI updates may not work across processes")
    
    def _run(self, text: str = "", transcription: list = None, **kwargs) -> str:
        """Execute globe change action."""
        try:
            # Handle different argument formats from LLM
            # LLM might call: oracleglobe("changeglobe") or oracleglobe({text: "change globe"})
            # If text is empty, check kwargs for string values
            if not text:
                # Check kwargs for any string value that might be the text
                for key, value in kwargs.items():
                    if isinstance(value, str) and value.strip():
                        text = value
                        logger.info(f"OracleGlobeTool: Found text in kwargs['{key}']: '{text}'")
                        break
                # If still no text and we have a single kwarg with a string value
                if not text and len(kwargs) == 1:
                    first_value = list(kwargs.values())[0]
                    if isinstance(first_value, str) and first_value.strip():
                        text = first_value
                        logger.info(f"OracleGlobeTool: Using single kwarg value as text: '{text}'")
            
            # Extract action from text
            text_lower = text.lower().strip() if text else ""
            logger.info(f"OracleGlobeTool called with text: '{text_lower}', kwargs: {kwargs}")
            
            # Normalize text: remove punctuation and extra spaces for better matching
            import re
            # Remove punctuation but keep spaces
            text_normalized = re.sub(r'[^\w\s]', ' ', text_lower)
            # Collapse multiple spaces to single space
            text_normalized = ' '.join(text_normalized.split())
            logger.info(f"OracleGlobeTool normalized text: '{text_normalized}'")
            
            # Handle cases like "changeglobe" or "changeoracle" (no space) - add space for matching
            if text_normalized and ' ' not in text_normalized:
                # Try to split camelCase or detect patterns
                if 'change' in text_normalized and ('globe' in text_normalized or 'oracle' in text_normalized):
                    # "changeglobe" -> "change globe", "changeoracle" -> "change oracle"
                    text_normalized = text_normalized.replace('changeglobe', 'change globe')
                    text_normalized = text_normalized.replace('changeoracle', 'change oracle')
                    text_normalized = text_normalized.replace('previousglobe', 'previous globe')
                    text_normalized = text_normalized.replace('previousoracle', 'previous oracle')
            
            # If still no text, default to forward
            if not text_normalized:
                logger.warning("OracleGlobeTool called with no text, defaulting to change_globe")
                text_normalized = "change globe"
            
            # Check for backward/previous actions FIRST (more specific)
            # Check for phrases that indicate previous/backward movement
            previous_patterns = [
                'change previous globe', 'change previous oracle', 'previous globe', 'previous oracle',
                'go back globe', 'go back oracle', 'back globe', 'back oracle', 'go back', 
                'previous', 'change back', 'go previous'
            ]
            if any(phrase in text_normalized for phrase in previous_patterns):
                action = "change_previous_globe"
                logger.info(f"Detected previous/backward action from text: '{text_normalized}'")
            # Check for forward/next actions (handle both "globe" and "oracle")
            elif any(phrase in text_normalized for phrase in [
                'change globe', 'change oracle', 'next globe', 'next oracle', 
                'change to next globe', 'change to next oracle', 'next'
            ]):
                action = "change_globe"
                logger.info(f"Detected forward/next action from text: '{text_normalized}'")
            # Default to forward if just "globe" or "oracle" is mentioned
            elif 'globe' in text_normalized or 'oracle' in text_normalized:
                action = "change_globe"
                logger.info(f"Detected 'globe' or 'oracle' keyword, defaulting to forward action")
            else:
                # If no clear action, default to forward
                action = "change_globe"
                logger.info(f"No clear action found in text '{text_normalized}', defaulting to change_globe")
            
            if not action:
                return "Error: No action specified. Use 'change_globe' or 'change_previous_globe'"
            
            from distr.core.actions.oracle_control import change_oracle, change_previous_oracle
            
            action_funcs = {
                "change_globe": change_oracle,
                "next_globe": change_oracle,
                "change_previous_globe": change_previous_oracle,
                "previous_globe": change_previous_oracle,
            }
            
            func = action_funcs.get(action.lower())
            if not func:
                return f"Error: Invalid action '{action}'. Use 'change_globe' or 'change_previous_globe'"
            
            logger.info(f"OracleGlobeTool: Calling {func.__name__} with event_queue={self._event_queue is not None}")
            func(event_queue=self._event_queue)
            
            direction = "next" if "previous" not in action.lower() else "previous"
            return f"Changed globe to {direction} image successfully"
            
        except Exception as e:
            logger.error(f"Error in OracleGlobeTool: {e}", exc_info=True)
            return f"Error changing globe: {str(e)}"
    
    async def _arun(self, text: str = "", transcription: list = None, **kwargs) -> str:
        """Async execution."""
        return self._run(text=text, transcription=transcription)


