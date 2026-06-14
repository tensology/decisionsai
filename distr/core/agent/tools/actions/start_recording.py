"""
Start Recording Tool for LangChain.

This tool starts recording an action (mouse and keyboard events).
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import logging
import re
import json

logger = logging.getLogger(__name__)


class StartRecordingInput(BaseModel):
    """Input schema for start_recording tool."""
    text: Optional[str] = Field(default="", description="The full user request text (optional, used for context)")

def number_to_word(n):
    """Convert number to word for 1-20."""
    mapping = {
        1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five',
        6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten',
        11: 'eleven', 12: 'twelve', 13: 'thirteen', 14: 'fourteen',
        15: 'fifteen', 16: 'sixteen', 17: 'seventeen', 18: 'eighteen',
        19: 'nineteen', 20: 'twenty'
    }
    return mapping.get(n, str(n))

def word_to_number(w):
    """Convert word to number for 1-20."""
    mapping = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
        'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
        'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
        'nineteen': 19, 'twenty': 20
    }
    return mapping.get(w.lower())

def get_next_title(last_title):
    """Generate next title based on previous title. Returns None if cannot iterate."""
    if not last_title:
        return None
        
    # Check for word numbers (e.g., "five")
    lower_title = last_title.lower()
    num = word_to_number(lower_title)
    if num:
        # If next number is within range 1-20, return word
        # Otherwise return string representation of number
        if num < 20:
            return number_to_word(num + 1)
        return str(num + 1)
        
    # Check for numeric endings (e.g., "Action 5")
    match = re.search(r'(\d+)$', last_title)
    if match:
        number = int(match.group(1))
        prefix = last_title[:match.start()]
        return f"{prefix}{number + 1}"
        
    return None

def generate_trigger_words(title):
    """Generate relevant trigger words based on the title."""
    if not title:
        return "[]"
        
    triggers = []
    lower_title = title.lower()
    
    # Case 1: Pure number word (e.g. "one" -> "1")
    num = word_to_number(lower_title)
    if num:
        triggers.append(str(num))
        
    # Case 2: Pure digit string (e.g. "1" -> "one")
    if lower_title.isdigit():
        try:
            val = int(lower_title)
            word = number_to_word(val)
            if word != lower_title:
                triggers.append(word)
        except (ValueError, KeyError):
            pass

    # Case 3: "Action X" variations
    # Check for "Action <digit>" -> add "Action <word>" and "<digit>"
    match_digit = re.search(r'(\d+)$', title)
    if match_digit:
        val = int(match_digit.group(1))
        word = number_to_word(val)
        prefix = title[:match_digit.start()]
        # Add "Action <word>" (e.g. "Action six")
        triggers.append(f"{prefix}{word}")
        # Add just the digit (e.g. "6")
        triggers.append(str(val))
        
    # Check for "Action <word>" -> add "Action <digit>" and "<digit>"
    # We need to check if the title ends with a known number word
    for n in range(1, 21):
        word = number_to_word(n)
        if lower_title.endswith(f" {word}"):
            prefix = title[:-len(word)] # keep original case prefix including space
            # Add "Action <digit>" (e.g. "Action 6")
            triggers.append(f"{prefix}{n}")
            # Add just the digit (e.g. "6")
            triggers.append(str(n))
            break
            
    # Deduplicate and JSON encode
    unique_triggers = list(set(triggers))
    return json.dumps(unique_triggers)

class StartRecordingTool(BaseTool):
    """
    Tool for starting action recording.
    
    When this tool is executed:
    1. Creates a new action with auto-incremented name
    2. Starts recording mouse and keyboard events
    3. Emits action_recording_started signal (which updates tray icon to tray-recording.png)
    4. User can stop recording by:
       - Saying "stop recording" (triggers StopRecordingTool)
       - Clicking tray icon (stops recording, no menu shown)
       - Using context menu "Stop Recording" option
    """
    
    name: str = "start_recording"
    description: str = """Start recording an action (mouse and keyboard events).
    
    Usage:
    - "start recording" -> Creates a new action with auto-incremented name and starts recording
    - "start recording action" -> Same as above
    
    When recording starts, the tray icon changes to tray-recording.png.
    Click the tray icon to stop recording, or say "stop recording".
    """
    args_schema: type[BaseModel] = StartRecordingInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue
            
    def get_triggers(self) -> list[str]:
        """Get triggers for start recording."""
        return [
            "start recording", "begin recording", "record action",
            "start recording action", "begin recording action"
        ]
    
    def _run(self, text: str = "", **kwargs) -> str:
        """Execute start recording."""
        try:
            if not self.event_queue:
                logger.warning("[START_RECORDING] No event_queue available - cannot start recording from agent process")
                return "Error: Cannot start recording (no event queue)"

            # Delegate entirely to the recorder_host in the main process.
            # Do NOT create the Action here - recorder_host._start_recording_silently() handles
            # that to avoid duplicate DB entries when the signal is received.
            logger.info("[START_RECORDING] Sending start_action_recording event to main process")
            try:
                self.event_queue.put(('start_action_recording', {}))
                logger.info("[START_RECORDING] Emitted start_action_recording event")
            except Exception as e:
                logger.error(f"[START_RECORDING] Failed to emit event: {e}", exc_info=True)
                return f"Error starting recording: {str(e)}"

            return "Started recording. Perform your actions now. Say 'stop recording' when done."
                
        except Exception as e:
            logger.error(f"Error starting recording: {e}", exc_info=True)
            return f"Error starting recording: {str(e)}"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        return self._run(text=text)

