"""
Type Text Tool

A tool that types text directly as keyboard input.
Supports explicit commands:
- "type 'text here'" - types the exact text in quotes
- "type <text here>" - types the exact text in angle brackets
- "type from clipboard" - types clipboard content
- "type text here" - types whatever comes after "type"

This is a DIRECT, EXPLICIT command - no ambiguity. When user says "type" followed by text, type that exact text.
"""

import logging
import re
from typing import Optional, Any
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from distr.core.audio.dictation import type_text
except Exception:
    def type_text(text: str, delay: float = 0.01):
        logger.error("TypeText: pynput/dictation backend is not available")
        return False


def get_clipboard_content() -> Optional[str]:
    """Get text content from system clipboard using platform-specific methods."""
    try:
        import platform
        system = platform.system()
        
        if system == "Darwin":  # macOS
            import subprocess
            result = subprocess.run(
                ['pbpaste'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.stdout if result.returncode == 0 else None
        elif system == "Windows":
            import subprocess
            result = subprocess.run(
                ['powershell', '-command', 'Get-Clipboard'],
                capture_output=True,
                text=True,
                timeout=1
            )
            return result.stdout.strip() if result.returncode == 0 else None
        else:  # Linux
            try:
                import subprocess
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


class TypeTextInput(BaseModel):
    """Input schema for type_text tool."""
    text: Optional[str] = Field(default=None, description="Text to type directly. If None, will extract from user command or use clipboard.")
    source: Optional[str] = Field(default="auto", description="Source of text: 'clipboard', 'direct', or 'auto' (extract from command).")


class TypeTextTool(BaseTool):
    """
    Tool to type text directly as keyboard input.
    
    ⚠️ CRITICAL: This is a DIRECT, EXPLICIT command - NO AMBIGUITY.
    
    EXACT COMMANDS (use these patterns):
    1. "type 'text here'" - Types the exact text in single/double quotes
    2. "type <text here>" - Types the exact text in angle brackets
    3. "type from clipboard" - Types clipboard content (EXPLICIT - must say "from clipboard")
    4. "type text here" - Types whatever text comes immediately after "type"
    
    RULES:
    - When user says "type" followed by text/quotes/brackets → EXTRACT that text and type it DIRECTLY
    - When user says "type from clipboard" → Type clipboard content (ONLY if explicitly says "from clipboard")
    - DO NOT use agent response text unless explicitly requested
    - DO NOT ask for confirmation - just type the text
    - This is a SENSITIVE tool - be very explicit about what you're typing
    
    EXAMPLES:
    - User: "type 'hello world'" → Type "hello world"
    - User: "type <test>" → Type "test"
    - User: "type hello world" → Type "hello world"
    - User: "type from clipboard" → Type clipboard content
    """
    
    name: str = "type_text"
    description: str = (
        "⚠️ DIRECT TYPING TOOL - NO AMBIGUITY ⚠️\n"
        "Types text directly as keyboard input. Use ONLY when user explicitly says 'type' followed by text.\n"
        "\n"
        "EXACT PATTERNS:\n"
        "1. 'type \"text\"' or 'type 'text'' → Type the quoted text\n"
        "2. 'type <text>' → Type the text in angle brackets\n"
        "3. 'type from clipboard' → Type clipboard content (ONLY if explicitly says 'from clipboard')\n"
        "4. 'type text here' → Type whatever comes after 'type'\n"
        "\n"
        "CRITICAL RULES:\n"
        "- Extract text DIRECTLY from user's command - do NOT use agent response\n"
        "- 'type from clipboard' is EXPLICIT - only use clipboard if user says 'from clipboard'\n"
        "- This is SENSITIVE - be explicit about what you're typing\n"
        "- DO NOT ask for confirmation - just execute\n"
        "- DO NOT use this for general transcription - only for explicit 'type' commands"
    )
    args_schema: type[BaseModel] = TypeTextInput
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    llm_service: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, llm_service=None, **kwargs):
        super().__init__(**kwargs)
        self.chat_manager = chat_manager
        self.llm_service = llm_service
    
    def get_triggers(self) -> list[str]:
        """Get explicit triggers for type command."""
        return [
            "type",
            "type '",
            'type "',
            "type <",
            "type from clipboard",
        ]
    
    def _get_agent_response_text(self) -> Optional[str]:
        """Get the most recent agent response text from LLM service."""
        if not self.llm_service:
            return None
        
        try:
            # Get the last assistant message from the LLM service
            if hasattr(self.llm_service, '_messages') and self.llm_service._messages:
                # Look for the last assistant message
                for msg in reversed(self.llm_service._messages):
                    if msg.get('role') == 'assistant':
                        content = msg.get('content', '')
                        if content:
                            return content.strip()
            
            # Fallback: check if there's a recent response in chat manager
            if self.chat_manager:
                try:
                    chat_id = self.chat_manager.get_current_chat()
                    if chat_id:
                        messages = self.chat_manager.get_messages(chat_id)
                        if messages:
                            # Get the last assistant message
                            for msg in reversed(messages):
                                if msg.get('role') == 'assistant':
                                    content = msg.get('content', '')
                                    if content:
                                        return content.strip()
                except Exception as e:
                    logger.warning(f"TypeText: Could not get messages from chat manager: {e}")
            
            return None
        except Exception as e:
            logger.error(f"TypeText: Error getting agent response: {e}", exc_info=True)
            return None
    
    def _extract_text_from_command(self, command: str) -> Optional[str]:
        """
        Extract text to type from user's command.
        
        Patterns:
        1. "type 'text'" or 'type "text"' - extract from quotes
        2. "type <text>" - extract from angle brackets
        3. "type from clipboard" - return None (special case)
        4. "type text here" - extract everything after "type"
        """
        if not command:
            return None
        
        command_lower = command.lower().strip()
        
        # Check for explicit "type from clipboard" first
        if "type from clipboard" in command_lower or "type from the clipboard" in command_lower:
            return None  # Signal to use clipboard
        
        # Pattern 1: Extract from single or double quotes
        # Match: type 'text' or type "text"
        quote_patterns = [
            r"type\s+['\"](.+?)['\"]",  # type 'text' or type "text"
            r"type\s+['\"](.+?)$",  # type 'text (unclosed quote at end)
        ]
        for pattern in quote_patterns:
            match = re.search(pattern, command, re.IGNORECASE | re.DOTALL)
            if match:
                extracted = match.group(1).strip()
                if extracted:
                    logger.info(f"TypeText: Extracted from quotes: '{extracted[:50]}...'")
                    return extracted
        
        # Pattern 2: Extract from angle brackets
        # Match: type <text>
        bracket_pattern = r"type\s+<(.+?)>"
        match = re.search(bracket_pattern, command, re.IGNORECASE | re.DOTALL)
        if match:
            extracted = match.group(1).strip()
            if extracted:
                logger.info(f"TypeText: Extracted from brackets: '{extracted[:50]}...'")
                return extracted
        
        # Pattern 3: Extract everything after "type" (but not "type from clipboard")
        # Match: type text here
        type_pattern = r"type\s+(.+?)$"
        match = re.search(type_pattern, command, re.IGNORECASE | re.DOTALL)
        if match:
            extracted = match.group(1).strip()
            # Exclude "from clipboard" cases
            if extracted and "from clipboard" not in extracted.lower():
                logger.info(f"TypeText: Extracted after 'type': '{extracted[:50]}...'")
                return extracted
        
        return None
    
    def _run(self, text: Optional[str] = None, source: str = "auto", **kwargs) -> str:
        """
        Type text directly as keyboard input.
        
        Args:
            text: Optional text to type directly. If None, will extract from user command.
            source: Source of text - 'clipboard', 'direct', or 'auto' (extract from command)
        
        Returns:
            Status message indicating success or error
        """
        try:
            text_to_type = None
            original_text = kwargs.get('text', '') or kwargs.get('transcription', '') or kwargs.get('original_text', '')
            
            # Priority 1: If text is provided directly in tool call, use it
            if text and text.strip():
                text_to_type = text.strip()
                logger.info(f"TypeText: Using provided text parameter ({len(text_to_type)} chars)")
            
            # Priority 2: Extract from user's command (explicit patterns)
            elif source == "auto" or source == "direct":
                extracted = self._extract_text_from_command(original_text)
                if extracted:
                    text_to_type = extracted
                    logger.info(f"TypeText: Extracted from command ({len(text_to_type)} chars)")
                elif "from clipboard" in original_text.lower():
                    # Explicit "type from clipboard" command
                    text_to_type = get_clipboard_content()
                    if not text_to_type or not text_to_type.strip():
                        return "Error: Clipboard is empty. Please copy text to clipboard first."
                    text_to_type = text_to_type.strip()
                    logger.info(f"TypeText: Using clipboard (explicit 'from clipboard' command) ({len(text_to_type)} chars)")
            
            # Priority 3: Explicit clipboard source
            elif source == "clipboard":
                text_to_type = get_clipboard_content()
                if not text_to_type or not text_to_type.strip():
                    return "Error: Clipboard is empty. Please copy text to clipboard first."
                text_to_type = text_to_type.strip()
                logger.info(f"TypeText: Using clipboard (explicit source) ({len(text_to_type)} chars)")
            
            # Validate we have text to type
            if not text_to_type or not text_to_type.strip():
                return "Error: No text to type. Please provide text in quotes, brackets, or use 'type from clipboard'."
            
            # Type the text using dictation utils
            logger.info(f"TypeText: Typing {len(text_to_type)} characters as keyboard input")
            success = type_text(text_to_type)
            
            if success:
                return f"Typed {len(text_to_type)} characters as keyboard input."
            else:
                return f"Error: Failed to type text. Please check logs for details."
            
        except Exception as e:
            logger.error(f"Error in TypeTextTool: {e}", exc_info=True)
            return f"Error typing text: {str(e)}"
    
    async def _arun(self, text: Optional[str] = None, source: str = "auto", **kwargs) -> str:
        """Async version of _run."""
        return self._run(text=text, source=source, **kwargs)
