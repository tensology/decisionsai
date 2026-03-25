"""
Use Snippet Tool for LangChain.

This tool allows copying a snippet to the clipboard or pasting it directly, 
identified by its title or a keyword.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import pyautogui
# Disable pyautogui FAILSAFE to prevent mouse operations from being blocked
pyautogui.FAILSAFE = False
import time
import json
from distr.core.db import get_session, Snippet
from distr.core.agent.tools.base import get_platform_modifier_key
import re

logger = logging.getLogger(__name__)

def number_to_word(n):
    """Convert number to word for 1-20 and tens."""
    mapping = {
        1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five',
        6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten',
        11: 'eleven', 12: 'twelve', 13: 'thirteen', 14: 'fourteen',
        15: 'fifteen', 16: 'sixteen', 17: 'seventeen', 18: 'eighteen',
        19: 'nineteen', 20: 'twenty',
        30: 'thirty', 40: 'forty', 50: 'fifty', 60: 'sixty',
        70: 'seventy', 80: 'eighty', 90: 'ninety', 100: 'hundred'
    }
    return mapping.get(n, str(n))

def word_to_number(w):
    """Convert word to number for 1-20 and tens."""
    mapping = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
        'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
        'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
        'nineteen': 19, 'twenty': 20,
        'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60,
        'seventy': 70, 'eighty': 80, 'ninety': 90, 'hundred': 100
    }
    return mapping.get(w.lower())

def set_clipboard_content(text: str):
    """Set content to clipboard using platform-specific methods."""
    try:
        import platform
        system = platform.system()
        
        if system == "Darwin":  # macOS
            import subprocess
            result = subprocess.run(
                ['pbcopy'],
                input=text,
                text=True,
                timeout=1
            )
            return result.returncode == 0
        elif system == "Windows":
            import subprocess
            result = subprocess.run(
                ['powershell', '-command', f'Set-Clipboard -Value @"{text}"@'],
                timeout=1
            )
            return result.returncode == 0
        else:  # Linux
            try:
                import subprocess
                # Try xclip first
                result = subprocess.run(
                    ['xclip', '-selection', 'clipboard'],
                    input=text,
                    text=True,
                    timeout=1
                )
                if result.returncode == 0:
                    return True
            except Exception:
                pass
            try:
                # Fallback to xsel
                result = subprocess.run(
                    ['xsel', '--clipboard', '--input'],
                    input=text,
                    text=True,
                    timeout=1
                )
                return result.returncode == 0
            except Exception:
                pass
            return False
    except Exception as e:
        logger.error(f"Error setting clipboard content: {e}", exc_info=True)
        return False

class UseSnippetTool(BaseTool):
    """Tool for using (copy/paste) a snippet."""
    
    name: str = "use_snippet"
    description: str = """Use a snippet (copy to clipboard or paste).
    
    Usage:
    - "copy snippet <x> to clipboard" -> Copies snippet content to clipboard.
    - "paste snippet <x>" -> Copies to clipboard AND simulates paste command.
    - "copy <x> snippet" -> Copies snippet content.
    
    <x> can be the snippet title OR a trigger word (keyword).
    """
    
    def get_triggers(self) -> list[str]:
        """Get triggers for use snippet."""
        return ["paste snippet", "copy snippet", "use snippet", "snippet"]
    
    def _run(self, text: str = "", **kwargs) -> str:
        """Execute use snippet action."""
        logger.debug(f"🔍 DEBUG: use_snippet called with text: '{text}'")
        try:
            text_lower = text.lower()
            
            # Pre-processing: Fix common phonetic transcription errors
            # "snip it" -> "snippet"
            # "snipit" -> "snippet"
            text_lower = text_lower.replace("snip it", "snippet").replace("snipit", "snippet")
            
            action = "paste" if "paste" in text_lower else "copy"
            
            # Extract the snippet identifier <x>
            identifier = None
            
            # Try regex patterns
            patterns = [
                r"copy snippet (.+?) to clipboard",
                r"paste snippet (.+)",
                r"copy (.+?) snippet",
                r"snippet (.+)", # fallback
            ]
            
            for pattern in patterns:
                match = re.search(pattern, text_lower, re.IGNORECASE)  # Match against normalized text
                if match:
                    identifier = match.group(1).strip()
                    # cleanup common suffix if present in group 1 capture due to greedy match
                    if identifier.endswith(" to clipboard"):
                        identifier = identifier[:-13].strip()
                    # Strip quotes if present
                    identifier = identifier.strip("'\"")
                    # Also strip common punctuation that might be captured at the end (e.g. "snippet 50.")
                    identifier = identifier.strip(".,;!?")
                    break
            
            if not identifier:
                # Fallback: if no pattern matches, maybe the whole text is the identifier minus the action verb
                words = text.split()
                cleaned_words = [w for w in words if w.lower() not in ['copy', 'paste', 'snippet', 'to', 'clipboard', 'a', 'the']]
                if cleaned_words:
                    identifier = " ".join(cleaned_words)
            
            logger.debug(f"🔍 DEBUG: Extracted identifier: '{identifier}'")
            
            if not identifier:
                return "Error: Could not identify which snippet to use."
                
            # Search in DB
            session = get_session()
            found_snippet = None
            try:
                from difflib import SequenceMatcher
                
                def similarity(a: str, b: str) -> float:
                    """Calculate similarity ratio between two strings."""
                    return SequenceMatcher(None, a.lower(), b.lower()).ratio()
                
                # 1. Exact title match (case insensitive)
                snippets = session.query(Snippet).all()
                
                # Priority 1: Title Match
                for s in snippets:
                    if s.title.lower() == identifier.lower():
                        found_snippet = s
                        break
                
                # Priority 2: Partial title match
                if not found_snippet:
                    for s in snippets:
                        if s.title and identifier.lower() in s.title.lower():
                            found_snippet = s
                            logger.info(f"UseSnippetTool: Found snippet by partial title match: '{s.title}'")
                            break
                
                # Priority 3: Keyword/Trigger Word Match
                if not found_snippet:
                    for s in snippets:
                        try:
                            triggers = json.loads(s.additional_trigger_words)
                            # triggers is a list of strings
                            if any(t.lower() == identifier.lower() for t in triggers):
                                found_snippet = s
                                logger.info(f"UseSnippetTool: Found snippet by trigger word: '{s.title}'")
                                break
                        except (json.JSONDecodeError, ValueError, TypeError):
                            continue
                
                # Priority 4: Fuzzy matching on titles and trigger words
                if not found_snippet:
                    logger.info(f"UseSnippetTool: Trying fuzzy match for '{identifier}'...")
                    best_match = None
                    best_score = 0.0
                    
                    for s in snippets:
                        # Check title similarity
                        if s.title:
                            title_score = similarity(identifier, s.title)
                            if title_score > best_score:
                                best_score = title_score
                                best_match = s
                        
                        # Check trigger words similarity
                        try:
                            triggers = json.loads(s.additional_trigger_words)
                            if isinstance(triggers, list):
                                for trigger in triggers:
                                    if trigger:
                                        trigger_score = similarity(identifier, str(trigger))
                                        if trigger_score > best_score:
                                            best_score = trigger_score
                                            best_match = s
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass
                    
                    # Accept match if score is above threshold
                    if best_match and best_score >= 0.7:
                        found_snippet = best_match
                        logger.info(f"UseSnippetTool: Fuzzy matched '{identifier}' to snippet '{found_snippet.title}' (score: {best_score:.2f})")
                    elif best_match and best_score >= 0.5:
                        found_snippet = best_match
                        logger.warning(f"UseSnippetTool: Weak fuzzy match '{identifier}' to snippet '{best_match.title}' (score: {best_score:.2f})")
                
                # Priority 4: Description match (similar to action tool)
                if not found_snippet:
                    logger.info(f"UseSnippetTool: No name/trigger match found, trying description match for '{identifier}'...")
                    identifier_lower = identifier.lower().strip()
                    
                    best_match = None
                    best_score = 0
                    
                    for s in snippets:
                        score = 0
                        
                        # Refresh the object to ensure we have latest data
                        session.refresh(s)
                        
                        # Check if description contains any words from the search
                        if s.description:
                            desc_lower = s.description.lower()
                            search_words = [w for w in identifier_lower.split() if len(w) > 2]
                            
                            # Count word matches in description
                            for word in search_words:
                                if word in desc_lower:
                                    score += 2  # Increased weight for description matches
                            
                            # Bonus if the full search text is contained in description
                            if identifier_lower in desc_lower:
                                score += 10
                            
                            # Bonus for partial phrase matches
                            if len(identifier_lower) > 5:
                                # Check if significant portion of search is in description
                                words_in_desc = sum(1 for word in search_words if word in desc_lower)
                                if words_in_desc >= len(search_words) * 0.5:  # At least 50% of words match
                                    score += 5
                        
                        # Check if title contains any words from the search (lower weight)
                        if s.title:
                            title_lower = s.title.lower()
                            search_words = [w for w in identifier_lower.split() if len(w) > 2]
                            for word in search_words:
                                if word in title_lower:
                                    score += 1
                        
                        if score > best_score:
                            best_score = score
                            best_match = s
                    
                    if best_match and best_score > 0:
                        found_snippet = best_match
                        logger.info(f"UseSnippetTool: Found snippet by description match: '{found_snippet.title}' (score: {best_score}, description: '{found_snippet.description[:50] if found_snippet.description else 'None'}...')")
                            
                if not found_snippet:
                    # Priority 5: Semantic/Numeric Conversion Match
                    # Handle "50" -> "fifty" and "fifty" -> "50"
                    alt_identifiers = []
                    
                    # Case A: Identifier is digits (e.g. "50") -> Try word ("fifty")
                    if identifier.isdigit():
                        try:
                            val = int(identifier)
                            word = number_to_word(val)
                            if word != identifier:
                                alt_identifiers.append(word)
                        except (ValueError, KeyError):
                            pass
                            
                    # Case B: Identifier is word (e.g. "fifty") -> Try digits ("50")
                    else:
                        num = word_to_number(identifier)
                        if num:
                            alt_identifiers.append(str(num))
                            
                    # Search with alternative identifiers
                    if alt_identifiers:
                        logger.debug(f"🔍 DEBUG: Exact match failed. Trying alternatives: {alt_identifiers}")
                        for alt in alt_identifiers:
                            # Title match
                            for s in snippets:
                                if s.title.lower() == alt.lower():
                                    found_snippet = s
                                    break
                            if found_snippet: break
                            
                            # Trigger match
                            for s in snippets:
                                try:
                                    triggers = json.loads(s.additional_trigger_words)
                                    if any(t.lower() == alt.lower() for t in triggers):
                                        found_snippet = s
                                        break
                                except (json.JSONDecodeError, ValueError, TypeError):
                                    continue
                            if found_snippet: break

                if not found_snippet:
                    # All matching attempts failed
                    return f"Error: Snippet '{identifier}' not found. Check the snippet name, description, or trigger words."
                
                content = found_snippet.snippet
                title = found_snippet.title
                
                # Log snippet details
                preview = content[:200] + "..." if len(content) > 200 else content
                logger.info(f"Snippet found: '{title}' (ID: {found_snippet.id})")
                logger.debug(f"Snippet preview: {preview}")
                
            finally:
                session.close()
            
            if not content:
                return f"Error: Snippet '{title}' has no content."
                
            # Execute Action
            if not set_clipboard_content(content):
                return "Error: Failed to set clipboard content."
                
            if action == "paste":
                # Wait a bit for clipboard to stabilize
                time.sleep(0.1)
                cmd_key = get_platform_modifier_key()
                pyautogui.hotkey(cmd_key, 'v')
                return f"Pasted snippet '{title}'."
            else:
                return f"Copied snippet '{title}' to clipboard."

        except Exception as e:
            logger.error(f"Error using snippet: {e}", exc_info=True)
            return f"Error using snippet: {str(e)}"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        return self._run(text=text)


