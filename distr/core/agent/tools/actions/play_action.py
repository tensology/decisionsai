"""
Play Action Tool for LangChain.

This tool plays/executes a recorded action by name.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
import logging
import json
from distr.core.db import get_session, Action
from distr.core.paths import RECORDINGS_DIR
from distr.core.signals import signal_manager
from pathlib import Path

logger = logging.getLogger(__name__)


class PlayActionInput(BaseModel):
    """Input schema for play_action tool."""
    text: Optional[str] = Field(default="", description="The full user request text (used to extract action name or description if action_name not provided)")
    action_name: Optional[str] = Field(default="", description="The name of the action to play/execute, or a description of what the action does (e.g., 'the action that opens cursor')")


class PlayActionTool(BaseTool):
    """Tool for playing/executing a recorded action."""
    
    name: str = "play_action"
    description: str = """Play or execute a recorded action by name or description.
    
    Usage:
    - "run action [name]" -> Plays the action with the given name
    - "play action [name]" -> Same as above
    - "execute action [name]" -> Same as above
    - "run the action that does [description]" -> Finds and plays the action matching the description
    - "play the action that [description]" -> Same as above
    - "action [name]" -> Same as above (if context is clear)
    
    The tool will first try to match by exact name, then by partial name, then by description.
    If you need to see all available actions, use the list_actions tool first.
    """
    args_schema: type[BaseModel] = PlayActionInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue
    
    def get_triggers(self) -> list[str]:
        """Get triggers for play action."""
        return [
            "run action", "play action", "execute action",
            "run action", "play action", "execute action"
        ]
    
    def _run(self, text: str = "", action_name: str = "", **kwargs) -> str:
        """Execute play action."""
        try:
            # Extract action name/description from text if not provided
            if not action_name and text:
                # Try to extract action name from phrases like "run action cursor" or "play action test"
                text_lower = text.lower().strip()
                
                # Handle "run the action that does X" or "play the action that X" patterns
                description_patterns = [
                    "run the action that does",
                    "play the action that does",
                    "execute the action that does",
                    "run the action that",
                    "play the action that",
                    "execute the action that",
                    "run action that does",
                    "play action that does",
                    "execute action that does",
                    "run action that",
                    "play action that",
                    "execute action that"
                ]
                
                for pattern in description_patterns:
                    if pattern in text_lower:
                        # Extract everything after the pattern
                        idx = text_lower.find(pattern)
                        if idx != -1:
                            action_name = text[idx + len(pattern):].strip()
                            # Remove trailing punctuation
                            import string
                            action_name = action_name.rstrip(string.punctuation).strip()
                            logger.info(f"PlayActionTool: Extracted description '{action_name}' from pattern '{pattern}'")
                            break
                
                # If no description pattern found, try simple prefixes
                if not action_name:
                    for prefix in ["run action", "play action", "execute action", "agent, run action", "agent, play action"]:
                        if text_lower.startswith(prefix):
                            action_name = text[len(prefix):].strip()
                            break
                
                # If still no name, try to find it after "action"
                if not action_name:
                    if "action" in text_lower:
                        parts = text.split()
                        try:
                            action_idx = [p.lower() for p in parts].index("action")
                            if action_idx + 1 < len(parts):
                                action_name = " ".join(parts[action_idx + 1:]).strip()
                        except ValueError:
                            pass
                
                # If still no name, use the whole text (might be just the action name)
                if not action_name:
                    action_name = text.strip()
            
            # Strip punctuation and whitespace from action name
            import string
            if action_name:
                # Remove trailing punctuation (periods, commas, etc.)
                original_name = action_name
                action_name = action_name.rstrip(string.punctuation).strip()
                if original_name != action_name:
                    logger.info(f"PlayActionTool: Stripped punctuation from '{original_name}' -> '{action_name}'")
            
            if not action_name:
                logger.error("PlayActionTool: No action name extracted from text")
                return "Error: No action name provided. Please specify which action to play."
            
            logger.info(f"PlayActionTool: Looking for action with name/trigger: '{action_name}' (from text: '{text}')")
            
            # Find action in database
            with get_session() as session:
                from difflib import SequenceMatcher
                
                def similarity(a: str, b: str) -> float:
                    """Calculate similarity ratio between two strings."""
                    return SequenceMatcher(None, a.lower(), b.lower()).ratio()
                
                action = None
                action_name_lower = action_name.lower().strip()
                
                # First, try exact title match
                action = session.query(Action).filter(Action.title.ilike(action_name)).first()
                if action:
                    logger.info(f"PlayActionTool: Found action by exact title match: '{action.title}'")
                
                # If no exact match, try partial title match
                if not action:
                    action = session.query(Action).filter(Action.title.ilike(f"%{action_name}%")).first()
                    if action:
                        logger.info(f"PlayActionTool: Found action by partial title match: '{action.title}'")
                
                # If still no match, try fuzzy matching on titles and trigger words
                if not action:
                    logger.info(f"PlayActionTool: Trying fuzzy match for '{action_name}'...")
                    all_actions = session.query(Action).all()
                    best_match = None
                    best_score = 0.0
                    
                    for a in all_actions:
                        # Check title similarity
                        if a.title:
                            title_score = similarity(action_name, a.title)
                            if title_score > best_score:
                                best_score = title_score
                                best_match = a
                        
                        # Check trigger words similarity
                        if a.additional_trigger_words:
                            try:
                                trigger_words = json.loads(a.additional_trigger_words)
                                if isinstance(trigger_words, list):
                                    for trigger in trigger_words:
                                        if trigger:
                                            trigger_score = similarity(action_name, str(trigger))
                                            if trigger_score > best_score:
                                                best_score = trigger_score
                                                best_match = a
                            except (json.JSONDecodeError, TypeError):
                                pass
                    
                    # Accept match if score is above threshold (0.7 = 70% similar)
                    if best_match and best_score >= 0.7:
                        action = best_match
                        logger.info(f"PlayActionTool: Fuzzy matched '{action_name}' to action '{action.title}' (score: {best_score:.2f})")
                    elif best_match and best_score >= 0.5:
                        action = best_match
                        logger.warning(f"PlayActionTool: Weak fuzzy match '{action_name}' to action '{best_match.title}' (score: {best_score:.2f})")
                
                # If still no match, try matching by description
                if not action:
                    logger.info(f"PlayActionTool: No name match found, trying description match for '{action_name}'...")
                    # Use a fresh query to ensure we get the latest data (no session caching)
                    all_actions = session.query(Action).all()
                    action_name_lower = action_name.lower().strip()
                    
                    # Try to match against descriptions
                    best_match = None
                    best_score = 0
                    
                    for a in all_actions:
                        score = 0
                        
                        # Refresh the object to ensure we have latest data
                        session.refresh(a)
                        
                        # Check if description contains any words from the search
                        if a.description:
                            desc_lower = a.description.lower()
                            search_words = [w for w in action_name_lower.split() if len(w) > 2]
                            
                            # Count word matches in description
                            for word in search_words:
                                if word in desc_lower:
                                    score += 2  # Increased weight for description matches
                            
                            # Bonus if the full search text is contained in description
                            if action_name_lower in desc_lower:
                                score += 10
                            
                            # Bonus for partial phrase matches
                            if len(action_name_lower) > 5:
                                # Check if significant portion of search is in description
                                words_in_desc = sum(1 for word in search_words if word in desc_lower)
                                if words_in_desc >= len(search_words) * 0.5:  # At least 50% of words match
                                    score += 5
                        
                        # Check if title contains any words from the search (lower weight)
                        if a.title:
                            title_lower = a.title.lower()
                            search_words = [w for w in action_name_lower.split() if len(w) > 2]
                            for word in search_words:
                                if word in title_lower:
                                    score += 1
                        
                        if score > best_score:
                            best_score = score
                            best_match = a
                    
                    if best_match and best_score > 0:
                        action = best_match
                        logger.info(f"PlayActionTool: Found action by description match: '{action.title}' (score: {best_score}, description: '{action.description[:50] if action.description else 'None'}...')")
                
                if not action:
                    logger.warning(f"PlayActionTool: No action found matching '{action_name}'")
                    return f"Error: Action '{action_name}' not found. Use the list_actions tool to see all available actions, or check the action name/description."
                
                # Check if this is an instruction action or a recorded action
                if action.is_instruction:
                    # This is an instruction action - send instruction to LLM
                    instruction_text = action.instruction_text or ""
                    if not instruction_text.strip():
                        return f"Error: Action '{action_name}' has no instruction text."
                    
                    logger.info(f"PlayActionTool: Executing instruction action '{action.title}' with text: {instruction_text[:100]}...")
                    
                    # Send instruction to LLM via event queue
                    if self.event_queue:
                        try:
                            self.event_queue.put(('send_text_input', {'text': instruction_text}), block=False)
                            logger.info(f"PlayActionTool: Sent instruction to LLM via event queue for action '{action.title}'")
                        except Exception as e:
                            logger.error(f"PlayActionTool: Failed to send instruction to LLM: {e}")
                            return f"Error: Failed to send instruction to LLM: {str(e)}"
                    else:
                        # Fallback: try direct signal
                        try:
                            signal_manager.send_text_input.emit(instruction_text, False, None, None)
                            logger.info(f"PlayActionTool: Emitted instruction signal for action '{action.title}'")
                        except Exception as e:
                            logger.error(f"PlayActionTool: Failed to emit instruction signal: {e}")
                            return f"Error: No event queue available and signal emission failed: {str(e)}"
                    
                    return f"Executing instruction action {action.title}."
                else:
                    # This is a recorded action - play the recording
                    if not action.recording_filename:
                        return f"Error: Action '{action_name}' has no recording file. Please record the action first."
                    
                    # Check if recording file exists
                    recording_path = Path(RECORDINGS_DIR) / action.recording_filename
                    if not recording_path.exists():
                        return f"Error: Recording file for action '{action_name}' not found at {recording_path}"
                    
                    # Send event via queue to play the action (cross-process communication)
                    # The main process will route this to ActionPlaybackService which handles playback
                    # The service runs playback in a separate process, independent of UI
                    if self.event_queue:
                        try:
                            self.event_queue.put(('play_action_by_name', {'action_name': action.title}), block=False)
                            logger.info(f"PlayActionTool: Sent play_action_by_name event for '{action.title}' (ID: {action.id}) - service will handle playback")
                        except Exception as e:
                            logger.error(f"PlayActionTool: Failed to send play_action_by_name event: {e}")
                            return f"Error: Failed to send play action event: {str(e)}"
                    else:
                        # Fallback: try direct signal (only works if in same process)
                        # This will be handled by ActionPlaybackService via signal connection
                        try:
                            signal_manager.play_action_by_name.emit(action.title)
                            logger.info(f"PlayActionTool: Emitted signal to play action '{action.title}' (ID: {action.id}) - service will handle playback")
                        except Exception as e:
                            logger.error(f"PlayActionTool: Failed to emit signal: {e}")
                            return f"Error: No event queue available and signal emission failed: {str(e)}"
                    
                    # Return immediately - playback is starting in the background.
                    return f"Running action {action.title}."
                
        except Exception as e:
            logger.error(f"Error playing action: {e}", exc_info=True)
            return f"Error playing action: {str(e)}"
    
    async def _arun(self, text: str = "", action_name: str = "", **kwargs) -> str:
        return self._run(text=text, action_name=action_name)

