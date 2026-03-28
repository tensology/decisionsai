import re
import logging
import platform
from typing import List, Dict, Any, Optional, Tuple
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, ConfigDict

logger = logging.getLogger(__name__)


class BaseActionInput(BaseModel):
    """Input schema for BaseActionTool - used by all config-based tools."""
    text: Optional[str] = Field(default="", description="The full user request text (optional, used for context)")
    transcription: Optional[List[str]] = Field(default=None, description="Optional transcription list (rarely used)")


def get_platform_modifier_key() -> str:
    """
    Get the appropriate modifier key based on the operating system.
    Returns 'command' for macOS (Darwin) and 'ctrl' for Windows/Linux.
    """
    system = platform.system()
    if system == 'Darwin':  # macOS
        return 'command'
    else:  # Windows, Linux, etc.
        return 'ctrl'


class BaseActionTool(BaseTool):
    """Base class for action tools that wrap existing action functions"""
    
    name: str = Field(description="Tool name")
    description: str = Field(description="Tool description")
    args_schema: type[BaseModel] = BaseActionInput
    
    # Store non-Pydantic attributes as private instance attributes
    _action_config: dict = None
    _chat_manager: object = None
    
    model_config = ConfigDict(arbitrary_types_allowed=True, extra='allow')
    
    def __init__(self, name: str = None, description: str = None, action_config: dict = None, chat_manager=None, **kwargs):
        # If name/description not provided, extract from action_config
        if action_config and (name is None or description is None):
            if name is None:
                name = action_config.get('trigger', action_config.get('name', 'unknown_action'))
            if description is None:
                method = action_config.get('method', '')
                description = f"Execute action: {name} (method: {method})"
        
        # Ensure we have name and description
        if name is None:
            name = 'unknown_action'
        if description is None:
            description = 'Unknown action'
        
        super().__init__(name=name, description=description, **kwargs)
        # Store non-Pydantic attributes using object.__setattr__ to bypass Pydantic validation
        object.__setattr__(self, '_action_config', action_config)
        object.__setattr__(self, '_chat_manager', chat_manager)
    
    def get_triggers(self) -> List[str]:
        """
        Get list of trigger phrases for this tool.
        Override this in subclasses to provide specific triggers.
        """
        # For config-based tools, return the trigger from config
        if self._action_config:
            trigger = self._action_config.get('trigger')
            if trigger:
                return [trigger.lower()]
        return []

    def _run(self, text: str = "", transcription: list = None, **kwargs) -> str:
        """Synchronous run method to execute the configured action"""
        if not self._action_config:
            return "Error: No action configuration found for this tool."
            
        try:
            method_path = self._action_config.get('method', '')
            if not method_path:
                return f"Error: No method defined for action {self.name}"
                
            # Import the module and get the function
            import importlib
            module_name, function_name = method_path.rsplit('.', 1)
            module = importlib.import_module(f"distr.core.actions.handlers.{module_name}")
            func = getattr(module, function_name)
            
            # Prepare arguments
            # Pass chat_manager if available
            # Pass action_config
            # Pass kwargs which might contain additional params from LLM
            
            # Most action functions expect: (chat_manager, action_config, kwargs)
            # or just (chat_manager, action_config)
            
            logger.info(f"BaseActionTool: Executing {method_path} for {self.name}")
            
            # Call the function
            # We pass kwargs as the third argument which acts as the context/params dict
            result = func(self._chat_manager, self._action_config, kwargs)
            
            return "Done" if result is None else str(result)
            
        except Exception as e:
            logger.error(f"Error executing action {self.name}: {e}", exc_info=True)
            return f"Error executing {self.name}: {str(e)}"
    
    async def _arun(self, text: str = "", transcription: list = None, **kwargs) -> str:
        """Async run method"""
        return self._run(text=text, transcription=transcription, **kwargs)


def fast_tool_matcher(transcription: str, tools: List[BaseTool], tools_dict: Dict[str, BaseTool]) -> Optional[Tuple[BaseTool, Dict[str, Any], float]]:
    """
    Dynamic fast matcher - matches transcription against tool triggers.
    
    Strategy:
    - Iterate over all tools and check their triggers (via get_triggers()).
    - Match if transcription contains the trigger phrase (for some tools) or equals it.
    - Prioritize longer matches (more specific).
    
    Returns:
        Tuple of (tool, arguments_dict, confidence_score) if match found, else None
    """
    if not transcription or not tools:
        return None
    
    # Use the same normalization as routing logic
    from distr.core.agent.services.llm.utils import normalize_text
    transcription_norm = normalize_text(transcription)
    transcription_lower = transcription.lower().strip()

    # Skip fast matching when the user references prior context — these need
    # the LLM to orchestrate multi-step tool chains (e.g. read email → create ticket).
    _CONTEXT_REFS = re.compile(
        r'\b(from\s+(that|this|the|my)\s+(email|message|conversation|chat|thread|result))'
        r'|(\b(that|this|the)\s+(email|message|one|result)\b)'
        r'|(\bbased\s+on\s+(that|this|the|my)\b)',
        re.IGNORECASE,
    )
    if _CONTEXT_REFS.search(transcription_lower):
        logger.debug("Fast matcher: skipping — contextual reference detected in '%s'", transcription[:80])
        return None
    
    # Pre-calculate word set for faster lookup
    transcription_words = set(re.findall(r'\w+', transcription_norm))
    
    best_match = None
    best_confidence = 0.0
    best_args = {}
    longest_trigger_len = 0
    
    for tool in tools:
        # Skip tools that don't implement get_triggers (or return empty)
        if not hasattr(tool, 'get_triggers'):
            continue
            
        triggers = tool.get_triggers()
        if not triggers:
            continue
            
        for trigger in triggers:
            trigger = trigger.lower().strip()
            if not trigger:
                continue
                
            confidence = 0.0
            args = {}
            
            # Check for exact match
            if transcription_norm == trigger:
                confidence = 0.99
                args = {'text': transcription}
            
            # Check for "trigger <args>" pattern (starts with trigger)
            elif transcription_norm.startswith(trigger + " "):
                confidence = 0.95
                args = {'text': transcription}
                
            # Check for "trigger" appearing in text with word boundaries
            # But verify it's not just a substring of another word (e.g. "read" in "bread")
            # CRITICAL: Don't match "copy" if it's part of a file operation (e.g. "copy that file", "copy file")
            elif trigger in transcription_norm:
                pattern = r'\b' + re.escape(trigger) + r'\b'
                if re.search(pattern, transcription_norm):
                    # Special case: If trigger is "copy" and text mentions file/folder/directory, it's a file operation, not text editing
                    if trigger == "copy" and re.search(r'\b(file|folder|directory|it|that|this)\s+(to|into|in|on)', transcription_norm, re.IGNORECASE):
                        continue  # Skip this match - it's a file operation
                    # If the transcription is much longer than the trigger, it's likely
                    # a conversational sentence that happens to contain the trigger phrase.
                    # Reduce confidence proportionally.
                    trigger_words = len(trigger.split())
                    text_words = len(transcription_norm.split())
                    if text_words > trigger_words * 4:
                        confidence = 0.60  # Too much surrounding context — let the LLM handle it
                    else:
                        confidence = 0.90
                    args = {'text': transcription}
            
            # Special handling for question forms if the trigger is embedded
            # e.g. "can you please move mouse center" -> trigger "move mouse center"
            # We strip common prefixes to check for match
            if confidence < 0.90:
                prefixes = ['can you', 'could you', 'please', 'would you', 'will you', 'do you mind']
                clean_text = transcription_norm
                for p in prefixes:
                    if clean_text.startswith(p):
                        clean_text = clean_text[len(p):].strip()
                
                if clean_text == trigger:
                    confidence = 0.92
                    args = {'text': transcription}
                elif clean_text.startswith(trigger + " "):
                    confidence = 0.90
                    args = {'text': transcription}

            if confidence > best_confidence:
                # Prefer longer triggers (more specific)
                # e.g. "create snippet" (len 14) > "snippet" (len 7)
                if len(trigger) >= longest_trigger_len:
                    best_confidence = confidence
                    best_match = tool
                    best_args = args
                    longest_trigger_len = len(trigger)
                elif confidence > best_confidence + 0.15: # Significantly better confidence overrides length
                    best_confidence = confidence
                    best_match = tool
                    best_args = args
                    # Don't update longest_trigger_len if we switched to a shorter but higher confidence match
    
    # Threshold: 0.85 allows for slightly more flexible matches (like questions handled above)
    if best_match and best_confidence >= 0.85:
        logger.info(f"Fast matcher: Found {best_match.name} with {best_confidence*100:.1f}% confidence for '{transcription}'")
        return (best_match, best_args, best_confidence)
    
    return None
