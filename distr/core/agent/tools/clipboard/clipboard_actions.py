"""
Clipboard-based Actions Tools for LangChain.

These tools handle clipboard-based actions like explain, elaborate, and read.
They copy to clipboard first, then process the clipboard content.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import pyautogui
# Disable pyautogui FAILSAFE to prevent mouse operations from being blocked
pyautogui.FAILSAFE = False
import time
from distr.core.agent.tools.base import get_platform_modifier_key

logger = logging.getLogger(__name__)


def get_clipboard_content():
    """Get content from clipboard using platform-specific methods."""
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


class ClipboardActionTool(BaseTool):
    """Tool for clipboard-based actions: explain, elaborate, read."""
    
    name: str = "clipboard_action"
    description: str = """EXECUTE clipboard actions: explain, elaborate, or get clipboard content.
    
    CRITICAL: This tool is ONLY for "explain this", "elaborate this", and "get clipboard" - NOT for copy/cut/paste.
    For "copy this", "cut this", "paste" → Use text_editing tool instead.
    
    DO NOT call this tool for copy/cut/paste operations - use text_editing tool.
    
    When user says "explain this", "elaborate this", "what's in the clipboard", "get the clipboard", "show clipboard" - YOU MUST CALL THIS TOOL IMMEDIATELY.
    DO NOT explain what the tool does. DO NOT describe the tool. DO NOT ask questions. JUST CALL IT.
    
    NOTE: "read this" is handled automatically and should NOT be called via this tool.
    
    The tool automatically:
    - For "explain this" / "elaborate this": Copies current selection (Cmd+C), gets clipboard content, processes it (explain/elaborate -> LLM)
    - For "get clipboard" / "what's in the clipboard": Returns clipboard content directly to conversation
    
    REQUIRED CALLS:
    - "explain this" -> CALL with action="explain" (do not explain, just call)
    - "elaborate this" -> CALL with action="elaborate" (do not explain, just call)
    - "what's in the clipboard" / "get the clipboard" / "show clipboard" -> CALL with action="get" (returns clipboard content)
    
    Available actions: explain, elaborate, get.
    DO NOT use this tool for copy/cut/paste - use text_editing tool instead.
    CALL THE TOOL - never describe it."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    llm_service: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, llm_service=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
        self._llm_service = llm_service
        self._read_task = None
        
    def get_triggers(self) -> list[str]:
        """Get triggers for clipboard actions."""
        return [
            "explain this", "elaborate this", "read this", 
            "what's in the clipboard", "what is in the clipboard",
            "get the clipboard", "get clipboard", "show clipboard",
            "read my clipboard", "read the clipboard", 
            "see what's in", "see what is in", "see my clipboard"
        ]
    
    def _run(self, action: str = "", text: str = "", **kwargs) -> str:
        """Execute clipboard-based action."""
        try:
            # Extract action from text if not provided
            # Also check if action is empty but we have a user request - try to infer from the last user message
            if not action:
                if text:
                    text_lower = text.lower().strip()
                    logger.info(f"Extracting action from text: '{text_lower}'")
                else:
                    # If both action and text are empty, this is likely a tool call without proper arguments
                    # Try to get the last user message from the LLM service if available
                    if self._llm_service and hasattr(self._llm_service, '_messages') and self._llm_service._messages:
                        # Get the last user message
                        for msg in reversed(self._llm_service._messages):
                            if msg.get('role') == 'user':
                                text = msg.get('content', '')
                                text_lower = text.lower().strip()
                                logger.info(f"No action/text provided, extracting from last user message: '{text_lower}'")
                                break
                
                if "explain" in text_lower and "this" in text_lower:
                    action = "explain"
                    logger.info("Detected: explain")
                elif "elaborate" in text_lower and ("on this" in text_lower or "this" in text_lower):
                    action = "elaborate"
                    logger.info("Detected: elaborate")
                elif "read" in text_lower and "this" in text_lower:
                    action = "read"
                    logger.info("Detected: read")
                elif any(phrase in text_lower for phrase in ["what's in the clipboard", "what is in the clipboard", "get the clipboard", "get clipboard", "get what's in", "get what is in", "go get", "go get what", "go get what's", "go get what is", "show clipboard", "show me clipboard", "what's on the clipboard", "read my clipboard", "read the clipboard", "see what's in", "see what is in", "see my clipboard"]):
                    action = "get"
                    logger.info("Detected: get clipboard")
                elif "get" in text_lower and "clipboard" in text_lower:
                    # Fallback: if both "get" and "clipboard" appear, it's likely a get request
                    action = "get"
                    logger.info("Detected: get clipboard (fallback - contains 'get' and 'clipboard')")
                elif "explain" in text_lower:
                    action = "explain"
                    logger.info("Detected: explain (fallback)")
                elif "elaborate" in text_lower:
                    action = "elaborate"
                    logger.info("Detected: elaborate (fallback)")
                elif "read" in text_lower:
                    action = "read"
                    logger.info("Detected: read (fallback)")
            
            if not action:
                logger.warning(f"Could not extract action from: action='{action}', text='{text}'")
                return "Error: No action specified. Available: explain, elaborate, read, get"
            
            logger.info(f"Executing clipboard action: {action}")
            
            # For "get" action, use existing clipboard content directly (no copy needed)
            if action == "get":
                clipboard_text = get_clipboard_content()
                if not clipboard_text or not clipboard_text.strip():
                    return "The clipboard is empty."
                logger.info(f"Got clipboard content ({len(clipboard_text)} chars) for get action")
                # Return clipboard content in a clear format that the LLM can reference
                # Use a clear marker so the LLM knows this is the clipboard content
                return f"CLIPBOARD CONTENT:\n\n{clipboard_text}\n\nThis is the current clipboard content. When the user refers to 'that list', 'what you see', 'the clipboard', or similar phrases, they are referring to this content above."
            
            # Step 1: Press Cmd+C to copy (for explain/elaborate/read)
            # Use keyDown/keyUp separately for more reliable execution on macOS
            cmd_key = get_platform_modifier_key()
            logger.info(f"Pressing {cmd_key}+C to copy selection")
            
            pyautogui.keyDown(cmd_key)
            pyautogui.press('c')
            pyautogui.keyUp(cmd_key)
            time.sleep(0.3)  # Increased delay to 0.3s to ensure clipboard updates
            
            # Step 2: Get clipboard content
            clipboard_text = get_clipboard_content()
            if not clipboard_text:
                logger.warning("Clipboard action: Failed to get clipboard content (empty or None)")
                return "Error: Could not get clipboard content. Make sure you have text selected."

            logger.info(f"Clipboard action: Got {len(clipboard_text)} chars")
            
            # For "read" action, preserve EXACT text (including whitespace, newlines, etc.)
            # For "explain" and "elaborate", we can strip whitespace
            if action == "read":
                # Keep exact text for reading - no modification, no stripping
                exact_clipboard_text = clipboard_text
                if not exact_clipboard_text or not exact_clipboard_text.strip():
                    return "Error: Clipboard is empty. Make sure you have text selected before using this command."
                logger.info(f"Got clipboard content for reading ({len(exact_clipboard_text)} chars, EXACT): '{exact_clipboard_text[:100]}...'")
                
                # Call async handler directly if running in async context (which we usually are via _arun)
                # But since _run is sync, we return a special marker or create a task if possible
                # Better approach: return the text and let _arun handle the async call
                return f"READ_ACTION:{exact_clipboard_text}"
            elif action == "explain":
                # For explain/elaborate, strip whitespace is okay
                clipboard_text = clipboard_text.strip()
                if not clipboard_text:
                    return "Error: Clipboard is empty. Make sure you have text selected before using this command."
                logger.info(f"Got clipboard content ({len(clipboard_text)} chars): '{clipboard_text[:100]}...'")
                return self._handle_explain(clipboard_text)
            elif action == "elaborate":
                # For explain/elaborate, strip whitespace is okay
                clipboard_text = clipboard_text.strip()
                if not clipboard_text:
                    return "Error: Clipboard is empty. Make sure you have text selected before using this command."
                logger.info(f"Got clipboard content ({len(clipboard_text)} chars): '{clipboard_text[:100]}...'")
                return self._handle_elaborate(clipboard_text)
            else:
                return f"Error: Unknown action '{action}'. Available: explain, elaborate, read"
            
        except Exception as e:
            logger.error(f"Error in ClipboardActionTool: {e}", exc_info=True)
            return f"Error executing clipboard action: {str(e)}"
    
    def _handle_explain(self, text: str) -> str:
        """Send text to LLM for explanation."""
        if not self._llm_service:
            return "Error: LLM service not available"
        
        try:
            # Add user message to LLM conversation
            # The message will be processed by the normal LLM flow after tool execution
            prompt = f"Please explain the following:\n\n{text}"
            self._llm_service._messages.append({"role": "user", "content": prompt})
            
            logger.info(f"Added explain request to LLM conversation for {len(text)} characters")
            
            # Trigger LLM generation immediately instead of waiting for tool execution to complete
            import asyncio
            async def trigger_llm():
                if self._llm_service._generation_task and not self._llm_service._generation_task.done():
                    # Cancel existing generation
                    self._llm_service._cancelled = True
                    self._llm_service._generation_task.cancel()
                    try:
                        await self._llm_service._generation_task
                    except asyncio.CancelledError:
                        pass
                    self._llm_service._cancelled = False
                
                # Start new generation with the clipboard content
                self._llm_service._generation_task = asyncio.create_task(self._llm_service._generate_response())
            
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(trigger_llm())
            except RuntimeError:
                asyncio.run(trigger_llm())
            
            # Return a simple success message
            return f"Copied {len(text)} characters from clipboard. Processing explanation..."
        except Exception as e:
            logger.error(f"Error handling explain: {e}", exc_info=True)
            return f"Error sending to LLM: {str(e)}"
    
    def _handle_elaborate(self, text: str) -> str:
        """Send text to LLM for elaboration."""
        if not self._llm_service:
            return "Error: LLM service not available"
        
        try:
            # Add user message to LLM conversation
            # The message will be processed by the normal LLM flow after tool execution
            prompt = f"Please elaborate on the following:\n\n{text}"
            self._llm_service._messages.append({"role": "user", "content": prompt})
            
            logger.info(f"Added elaborate request to LLM conversation for {len(text)} characters")
            
            # Trigger LLM generation immediately instead of waiting for tool execution to complete
            import asyncio
            async def trigger_llm():
                if self._llm_service._generation_task and not self._llm_service._generation_task.done():
                    # Cancel existing generation
                    self._llm_service._cancelled = True
                    self._llm_service._generation_task.cancel()
                    try:
                        await self._llm_service._generation_task
                    except asyncio.CancelledError:
                        pass
                    self._llm_service._cancelled = False
                
                # Start new generation with the clipboard content
                self._llm_service._generation_task = asyncio.create_task(self._llm_service._generate_response())
            
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(trigger_llm())
            except RuntimeError:
                asyncio.run(trigger_llm())
            
            # Return a simple success message
            return f"Copied {len(text)} characters from clipboard. Processing elaboration..."
        except Exception as e:
            logger.error(f"Error handling elaborate: {e}", exc_info=True)
            return f"Error sending to LLM: {str(e)}"
    
    async def _handle_read_async(self, text: str) -> str:
        """Send text to TTS for reading - EXACT text, streamed in chunks like LLM."""
        if not self._llm_service:
            logger.error("Clipboard read error: LLM service not available")
            return "Error: LLM service not available (needed to push TextFrame)"
        
        logger.info(f"Starting clipboard read: {len(text)} chars")
        
        # Add to chat history as a user message (simulating a paste) so the user sees what is being read
        if self._chat_manager:
            try:
                chat_id = self._chat_manager.get_current_chat()
                if chat_id:
                    display_text = f"📋 Clipboard Content:\n\n{text}"
                    self._chat_manager.add_user_message(chat_id, display_text)
                    logger.info(f"Added clipboard content to chat {chat_id}")
            except Exception as e:
                logger.error(f"Error adding to chat history: {e}")
        
        try:
            # Send TextFrame to TTS via the LLM service's pipeline
            # Sanitize emojis for TTS but preserve text content
            # Stream it in chunks like the LLM does for better responsiveness
            from pipecat.frames.frames import TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame
            from distr.core.agent.services.llm.utils import clean_text_for_tts
            import asyncio
            import re
            
            # Sanitize emojis for TTS (but preserve text content)
            exact_text = clean_text_for_tts(text) if text else ""
            
            # CRITICAL: Send start frame FIRST - TTS expects StartFrame → TextFrame(s) → EndFrame
            logger.info("TTS: Sending LLMFullResponseStartFrame")
            await self._llm_service.push_frame(LLMFullResponseStartFrame(), self._llm_service._pipeline_direction)
            
            # Stream text in chunks (by sentences or words) for better responsiveness
            # Split by sentences first, then by words if needed
            # Use regex to split on sentence boundaries while preserving the text exactly
            sentence_pattern = r'([.!?]+[\s\n]+|[\n]{2,})'
            sentences = re.split(sentence_pattern, exact_text)
            
            # Recombine sentences with their punctuation
            chunks = []
            for i in range(0, len(sentences), 2):
                if i < len(sentences):
                    chunk = sentences[i]
                    if i + 1 < len(sentences):
                        chunk += sentences[i + 1]  # Add punctuation/separator back
                    if chunk.strip():
                        chunks.append(chunk)
            
            # If no sentence breaks found, split by words (max 10 words per chunk)
            if len(chunks) == 1 and len(chunks[0].split()) > 10:
                words = chunks[0].split()
                chunks = []
                for i in range(0, len(words), 10):
                    chunk = ' '.join(words[i:i+10])
                    if i + 10 < len(words):
                        chunk += ' '  # Add space for next chunk
                    chunks.append(chunk)
            
            if not chunks and exact_text:
                chunks = [exact_text]
                
            logger.info(f"TTS: Streaming {len(chunks)} chunks")
            
            # Send each chunk as a separate TextFrame with small delays (like LLM streaming)
            for i, chunk in enumerate(chunks):
                if self._llm_service._cancelled:
                    logger.info("TTS: Read cancelled, stopping chunk streaming")
                    break
                
                # Send chunk - EXACT text, no cleaning, no modification
                logger.info(f"TTS: Push chunk {i+1}/{len(chunks)}: '{chunk[:20]}...'")
                await self._llm_service.push_frame(TextFrame(text=chunk), self._llm_service._pipeline_direction)
                
                # Small delay between chunks for streaming effect (similar to LLM)
                await asyncio.sleep(0.05)
            
            # Send end frame to trigger final TTS processing
            logger.info("TTS: Sending LLMFullResponseEndFrame")
            await self._llm_service.push_frame(LLMFullResponseEndFrame(), self._llm_service._pipeline_direction)
            
            logger.info(f"Streaming EXACT text ({len(exact_text)} chars) to TTS in chunks - preserving exact content: '{exact_text[:100]}...'")
            return f"Reading {len(exact_text)} characters exactly as copied"
        except Exception as e:
            logger.error(f"Error handling read: {e}", exc_info=True)
            return f"Error sending to TTS: {str(e)}"
    
    async def _arun(self, action: str = "", text: str = "", **kwargs) -> str:
        # Execute _run to determine action and get data
        logger.info(f"ClipboardActionTool._arun called with action='{action}', text='{text[:50] if text else ''}'")
        
        result = self._run(action=action, text=text)
        logger.info(f"ClipboardActionTool._run returned: {str(result)[:100]}")
        
        # If _run returned a READ_ACTION marker, execute the async read handler
        if isinstance(result, str) and result.startswith("READ_ACTION:"):
            content_to_read = result[len("READ_ACTION:"):]
            
            # Fire-and-forget the read task so we don't block the response
            import asyncio
            logger.info(f"Spawning async read task for {len(content_to_read)} chars")
            logger.info(f"ClipboardActionTool: Creating _read_task for {len(content_to_read)} chars")
            self._read_task = asyncio.create_task(self._handle_read_async(content_to_read))
            logger.info(f"ClipboardActionTool: _read_task created: {self._read_task}")
            
            return f"Reading {len(content_to_read)} characters..."
            
        return result
