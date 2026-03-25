"""
Create Action Tool for LangChain.

This tool creates a new action in the action manager.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import logging
import re
import json
from distr.core.db import get_session, Action

logger = logging.getLogger(__name__)


class CreateActionInput(BaseModel):
    """Input schema for create_action tool."""
    text: Optional[str] = Field(default="", description="The full user request text (used to extract action name if provided)")

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

class CreateActionTool(BaseTool):
    """Tool for creating a new action."""
    
    name: str = "create_action"
    description: str = """Create a new action in the action manager.
    
    Usage:
    - "create a new action" -> Automatically names it based on sequence (e.g. "five" -> "six")
    - "create action 'My Action'" -> Creates action named "My Action"
    """
    args_schema: type[BaseModel] = CreateActionInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue
            
    def get_triggers(self) -> list[str]:
        """Get triggers for create action."""
        return [
            "create action", "create a action", "new action", 
            "make a action", "create the action"
        ]
    
    def _run(self, text: str = "", **kwargs) -> str:
        """Execute create action."""
        try:
            # Step 1: Determine Title
            title = None
            
            # Check for explicit title in text
            # Regex to catch "action 'Title'" or 'action "Title"'
            title_match = re.search(r"action\s+['\"](.+?)['\"]", text, re.IGNORECASE)
            if title_match:
                title = title_match.group(1)
            else:
                # Step 2: Check previous actions for auto-naming
                # Search backwards for the first action that looks like a sequence (e.g., "one", "Action 5")
                session = get_session()
                try:
                    # Get recent actions (limit 50 to be safe)
                    recent_actions = session.query(Action).order_by(Action.id.desc()).limit(50).all()
                    
                    found_iterable = False
                    for action in recent_actions:
                        next_title = get_next_title(action.title)
                        if next_title:
                            title = next_title
                            found_iterable = True
                            break
                    
                    # If no iterable action found in history (or history empty), default to "one"
                    if not found_iterable:
                        title = "one"
                finally:
                    session.close()
            
            # Step 3: Save to Database
            session = get_session()
            new_action_id = None
            try:
                trigger_words_json = generate_trigger_words(title)
                new_action = Action(
                    title=title,
                    description=f"Created via voice command",
                    additional_trigger_words=trigger_words_json,
                    is_instruction=False,
                    instruction_text=None,
                    action="{}",  # Default empty JSON object
                    recording_filename=None
                )
                session.add(new_action)
                session.commit()
                new_action_id = new_action.id
                
                # Emit event if queue is available
                if self.event_queue:
                    try:
                        self.event_queue.put(('action_created', {'id': new_action_id, 'title': title}))
                        logger.info(f"Emitted action_created event for ID: {new_action_id}")
                    except Exception as e:
                        logger.error(f"Failed to emit action_created event: {e}")
                        
                return f"Successfully created action: '{title}'. You can now record it by saying 'Start recording' or use it by saying 'Action {title}'."
            except Exception as e:
                session.rollback()
                raise e
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error creating action: {e}", exc_info=True)
            return f"Error creating action: {str(e)}"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        return self._run(text=text)







