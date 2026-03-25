"""
Create Snippet Tool for LangChain.

This tool creates a new snippet from clipboard content.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import subprocess
import platform
import re
import json
from distr.core.db import get_session, Snippet

logger = logging.getLogger(__name__)

def get_clipboard_content():
    """Get content from clipboard using platform-specific methods."""
    try:
        system = platform.system()
        
        if system == "Darwin":  # macOS
            result = subprocess.run(
                ['pbpaste'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.stdout if result.returncode == 0 else None
        elif system == "Windows":
            result = subprocess.run(
                ['powershell', '-command', 'Get-Clipboard'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.stdout.strip() if result.returncode == 0 else None
        else:  # Linux
            try:
                # Try xclip first
                result = subprocess.run(
                    ['xclip', '-selection', 'clipboard', '-o'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                pass
            try:
                # Fallback to xsel
                result = subprocess.run(
                    ['xsel', '--clipboard', '--output'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                return result.stdout if result.returncode == 0 else None
            except Exception:
                pass
            return None
    except Exception as e:
        logger.error(f"Error getting clipboard content: {e}", exc_info=True)
        return None

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
        
    # Check for numeric endings (e.g., "Snippet 5")
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
            
    # Case 3: "Snippet X" variations
    # Check for "Snippet <digit>" -> add "Snippet <word>" and "<digit>"
    match_digit = re.search(r'(\d+)$', title)
    if match_digit:
        val = int(match_digit.group(1))
        word = number_to_word(val)
        prefix = title[:match_digit.start()]
        # Add "Snippet <word>" (e.g. "Snippet six")
        triggers.append(f"{prefix}{word}")
        # Add just the digit (e.g. "6")
        triggers.append(str(val))
        
    # Check for "Snippet <word>" -> add "Snippet <digit>" and "<digit>"
    # We need to check if the title ends with a known number word
    for n in range(1, 21):
        word = number_to_word(n)
        if lower_title.endswith(f" {word}"):
            prefix = title[:-len(word)] # keep original case prefix including space
            # Add "Snippet <digit>" (e.g. "Snippet 6")
            triggers.append(f"{prefix}{n}")
            # Add just the digit (e.g. "6")
            triggers.append(str(n))
            break
            
    # Deduplicate and JSON encode
    unique_triggers = list(set(triggers))
    return json.dumps(unique_triggers)

class CreateSnippetTool(BaseTool):
    """Tool for creating a new snippet from clipboard content."""
    
    name: str = "create_snippet"
    description: str = """Create a new snippet from clipboard content.
    
    Usage:
    - "create a new snippet from clipboard" -> Automatically names it based on sequence (e.g. "five" -> "six")
    - "create snippet 'My Code' from clipboard" -> Creates snippet named "My Code"
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue
            
    def get_triggers(self) -> list[str]:
        """Get triggers for create snippet."""
        return [
            "create snippet", "create a snippet", "new snippet", 
            "make a snippet", "save as snippet", "create the snippet"
        ]
    
    def _run(self, text: str = "", **kwargs) -> str:
        """Execute create snippet action."""
        try:
            # Step 1: Get clipboard content
            clipboard_text = get_clipboard_content()
            if not clipboard_text or not clipboard_text.strip():
                return "Error: Clipboard is empty."
                
            # Step 2: Determine Title
            title = None
            
            # Check for explicit title in text
            # Regex to catch "snippet 'Title'" or 'snippet "Title"'
            title_match = re.search(r"snippet\s+['\"](.+?)['\"]", text, re.IGNORECASE)
            if title_match:
                title = title_match.group(1)
            else:
                # Step 3: Check previous snippets for auto-naming
                # Search backwards for the first snippet that looks like a sequence (e.g., "one", "Snippet 5")
                session = get_session()
                try:
                    # Get recent snippets (limit 50 to be safe)
                    recent_snippets = session.query(Snippet).order_by(Snippet.id.desc()).limit(50).all()
                    
                    found_iterable = False
                    for snippet in recent_snippets:
                        next_title = get_next_title(snippet.title)
                        if next_title:
                            title = next_title
                            found_iterable = True
                            break
                    
                    # If no iterable snippet found in history (or history empty), default to "one"
                    if not found_iterable:
                        title = "one"
                finally:
                    session.close()
            
            # Step 4: Save to Database
            session = get_session()
            new_snippet_id = None
            try:
                trigger_words_json = generate_trigger_words(title)
                new_snippet = Snippet(
                    title=title,
                    description=f"Created from clipboard via voice command",
                    snippet=clipboard_text,  # Storing directly as text since db.py defines it as Text
                    additional_trigger_words=trigger_words_json
                )
                session.add(new_snippet)
                session.commit()
                new_snippet_id = new_snippet.id
                        
                return f"Successfully created snippet: '{title}'. You can now use it by saying 'Paste snippet {title}'."
            except Exception as e:
                session.rollback()
                raise e
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error creating snippet: {e}", exc_info=True)
            return f"Error creating snippet: {str(e)}"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        return self._run(text=text)

