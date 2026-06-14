"""
Rework Clipboard Tool for LangChain.

This tool reworks/improves clipboard content using an independent LLM and places it back in the clipboard.
"""

from typing import Optional
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


class ReworkClipboardTool(BaseTool):
    """Tool for reworking/improving clipboard content using an independent LLM."""
    
    name: str = "rework_clipboard"
    description: str = """EXECUTE reworking/rewording clipboard content using an independent LLM.
    
    CRITICAL: When user says "rework this", "reword this", "rework from clipboard", or "reword from clipboard" - YOU MUST CALL THIS TOOL IMMEDIATELY.
    DO NOT explain what the tool does. DO NOT describe the tool. DO NOT ask questions. JUST CALL IT.
    
    NOTE: "rework" and "reword" are treated as the same action.
    
    The tool automatically:
    - For "rework this" / "reword this": Copies current selection (Cmd+C), gets clipboard content, reworks it via LLM, updates clipboard, then pastes (Cmd+V)
    - For "rework from clipboard" / "reword from clipboard": Uses current clipboard content directly (no copy), reworks it via LLM, updates clipboard
    
    REQUIRED CALLS:
    - "rework this" / "reword this" -> CALL immediately (do not explain, just call)
    - "rework from clipboard" / "reword from clipboard" -> CALL immediately (uses clipboard directly, no copy)
    
    CALL THE TOOL - never describe it."""
    
    llm_model: Optional[str] = Field(default="qwen3:8b", description="LLM model to use for reworking")
    
    # Default local model for fast reworking — always prefer local for speed
    LOCAL_REWORK_MODEL: str = "qwen3:8b"
    
    def __init__(self, llm_model="qwen3:8b", **kwargs):
        super().__init__(**kwargs)
        # Use a local model for reworking regardless of the main conversation model.
        # Cloud models are too slow for a synchronous clipboard operation.
        is_local = self._is_local_model(llm_model)
        self.llm_model = llm_model if is_local else self.LOCAL_REWORK_MODEL

    @staticmethod
    def _is_local_model(model_name: str) -> bool:
        """Check if a model is a local Ollama model (not cloud/routed)."""
        if not model_name:
            return False
        m = model_name.lower()
        cloud_indicators = [':cloud', ':pro', '/huggingface', '/openrouter', 'gpt-', 'o1', 'o3', 'o4',
                           'claude-', 'gemini-', 'chatgpt-']
        return not any(ind in m for ind in cloud_indicators)
    
    def _run(self, text: str = "", **kwargs) -> str:
        """Execute rework clipboard action."""
        try:
            # Check if user said "rework from clipboard" or "reword from clipboard" - if so, skip copying step
            text_lower = text.lower() if text else ""
            # Normalize "reword" to "rework" for consistent processing
            text_lower = text_lower.replace("reword", "rework")
            # "rework from clipboard" or "rework clipboard" → use clipboard directly (no copy, no paste)
            use_clipboard_directly = "clipboard" in text_lower and "rework" in text_lower
            # "rework this" → copy selection, rework, then paste (only if NOT using clipboard directly)
            # Must have "this" but NOT "clipboard" to paste
            should_paste = "this" in text_lower and "rework" in text_lower and "clipboard" not in text_lower
            
            # Step 1: Press Cmd+C to copy (unless using clipboard directly)
            if not use_clipboard_directly:
                cmd_key = get_platform_modifier_key()
                logger.info(f"Rework: Pressing {cmd_key}+C to copy selection")
                pyautogui.hotkey(cmd_key, 'c')
                time.sleep(0.15)  # Wait for clipboard to update
            else:
                logger.info("Rework: Using clipboard directly (no copy needed)")
            
            # Step 2: Get clipboard content
            clipboard_text = get_clipboard_content()
            if not clipboard_text or not clipboard_text.strip():
                logger.warning("Rework: Clipboard is empty")
                return "Error: Clipboard is empty. Make sure you have text selected before using this command."
            
            logger.info(f"Rework: Got clipboard content ({len(clipboard_text)} chars)")
            
            # Step 3: Send to independent LLM for reworking
            try:
                import ollama
                
                # Use an independent Ollama client (not the pipeline one)
                client = ollama.Client()
                
                # Validate and get the model to use
                # If the provided model is not an Ollama model (e.g., OpenAI/OpenRouter models),
                # fall back to a default Ollama model
                model_to_use = self.llm_model
                
                # Check if model is likely not an Ollama model (contains gpt-, claude-, etc.)
                non_ollama_indicators = ['gpt-', 'claude-', 'gemini', 'openrouter', 'anthropic', 'google']
                if any(indicator in model_to_use.lower() for indicator in non_ollama_indicators):
                    logger.warning(f"Rework: Model '{model_to_use}' is not an Ollama model, falling back to default")
                    model_to_use = "qwen3:8b"
                else:
                    # Try to verify the model exists in Ollama
                    try:
                        # List available models to check if the requested model exists
                        available_models = [m['name'] for m in client.list().get('models', [])]
                        if model_to_use not in available_models:
                            logger.warning(f"Rework: Model '{model_to_use}' not found in Ollama, falling back to default")
                            model_to_use = "qwen3:8b"
                    except Exception as e:
                        logger.warning(f"Rework: Could not verify Ollama models, using '{model_to_use}': {e}")
                        # If we can't verify, try the model anyway but be ready to fall back
                        pass
                
                logger.info(f"Rework: Using Ollama model '{model_to_use}' for reworking")
                
                # Create a prompt to rework/improve the text
                # CRITICAL: Instruct LLM to ONLY return the reworked text, no explanations or examples
                prompt = f"""Rewrite the following text to make it clearer, more professional, and better written. Preserve all the original meaning and key information. Return ONLY the rewritten text with no explanations, no examples, no quotes, and no additional commentary.

Text to rewrite:
{clipboard_text}

Rewritten text:"""
                
                logger.info(f"Rework: Sending {len(clipboard_text)} characters to LLM for reworking")
                
                # Call LLM synchronously (this is independent of the pipeline)
                # Use chat() method which returns a proper response object
                # keep_alive=-1 keeps the model loaded in memory indefinitely to avoid reload delays
                try:
                    response = client.chat(
                        model=model_to_use,
                        messages=[
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],
                        options={
                            "keep_alive": -1  # Keep model loaded in memory indefinitely
                        }
                    )
                except Exception as model_error:
                    # If the model fails, try with the default
                    if model_to_use != "qwen3:8b":
                        logger.warning(f"Rework: Model '{model_to_use}' failed, trying default: {model_error}")
                        model_to_use = "qwen3:8b"
                        response = client.chat(
                            model=model_to_use,
                            messages=[
                                {
                                    "role": "user",
                                    "content": prompt
                                }
                            ],
                            options={
                                "keep_alive": -1
                            }
                        )
                    else:
                        raise
                
                # Extract the reworked text from the response
                # The response.message.content contains the full text
                raw_response = response.get('message', {}).get('content', '').strip()
                
                # Fallback: if the structure is different, try to get it from response directly
                if not raw_response:
                    raw_response = response.get('content', '').strip()
                    if not raw_response and isinstance(response, dict):
                        # Try to find content in any nested structure
                        for key in ['response', 'text', 'output']:
                            if key in response:
                                raw_response = str(response[key]).strip()
                                break
                
                if not raw_response:
                    logger.warning("Rework: LLM returned empty response")
                    return "Error: LLM did not return reworked text."
                
                # Log the raw response for debugging
                logger.info(f"Rework: Raw LLM response ({len(raw_response)} chars): {raw_response[:200]}...")
                
                # Post-process: Extract just the reworked text if LLM included explanations
                import re
                reworked_text = raw_response.strip()
                
                # Remove common LLM explanation prefixes and extract actual reworked text
                # Check if response starts with explanation text
                explanation_patterns = [
                    r'^it appears[^"]*',
                    r'^however[^"]*',
                    r'^here\'s[^"]*',
                    r'^here is[^"]*',
                    r'^i can help[^"]*',
                    r'^the following[^"]*',
                ]
                
                for pattern in explanation_patterns:
                    if re.match(pattern, reworked_text, re.IGNORECASE):
                        # Try to extract quoted text (usually the actual reworked version)
                        quoted_text = re.findall(r'"([^"]+)"', reworked_text)
                        if quoted_text and len(quoted_text) > 0:
                            # Use the longest quoted text (usually the reworked version)
                            reworked_text = max(quoted_text, key=len).strip()
                            logger.info(f"Rework: Extracted quoted text from explanation ({len(reworked_text)} chars)")
                            break
                        # If no quotes, try to find text after colon or marker
                        if ':' in reworked_text:
                            parts = reworked_text.split(':', 1)
                            if len(parts) > 1:
                                candidate = parts[1].strip()
                                # Remove quotes if present
                                candidate = re.sub(r'^["\']', '', candidate)
                                candidate = re.sub(r'["\']$', '', candidate)
                                if candidate and len(candidate) > len(clipboard_text) * 0.5:  # At least 50% of original length
                                    reworked_text = candidate.strip()
                                    logger.info(f"Rework: Extracted text after colon ({len(reworked_text)} chars)")
                                    break
                
                # Final cleanup: remove any remaining explanation text at the start
                # If the response is suspiciously short compared to input, check for issues
                if len(reworked_text) < len(clipboard_text) * 0.3:
                    logger.warning(f"Rework: Response seems too short ({len(reworked_text)} vs {len(clipboard_text)} chars)")
                    # Try harder to extract the actual reworked text
                    # Look for the longest sentence or paragraph
                    sentences = re.split(r'[.!?]\s+', raw_response)
                    if sentences:
                        longest = max(sentences, key=len)
                        if len(longest) > len(reworked_text) and len(longest) > 20:
                            reworked_text = longest.strip()
                            logger.info(f"Rework: Using longest sentence from response ({len(reworked_text)} chars)")
                    
                    # If still too short, use the entire raw response (maybe LLM just gave a short answer)
                    if len(reworked_text) < len(clipboard_text) * 0.3:
                        logger.warning(f"Rework: Still too short after extraction. Using full raw response.")
                        reworked_text = raw_response.strip()
                
                logger.info(f"Rework: Final reworked text ({len(reworked_text)} chars)")
                
                # Step 4: Place reworked text back into clipboard
                if not set_clipboard_content(reworked_text):
                    return "Error: Failed to update clipboard with reworked text."
                
                logger.info("Rework: Updated clipboard with reworked text")
                
                # Verify clipboard was set correctly
                verify_clipboard = get_clipboard_content()
                if verify_clipboard != reworked_text:
                    logger.warning(f"Rework: Clipboard verification failed. Expected {len(reworked_text)} chars, got {len(verify_clipboard) if verify_clipboard else 0} chars")
                else:
                    logger.info(f"CLIPBOARD VERIFIED: {len(reworked_text)} characters ready to paste")
                
                # Step 5: If "rework this" version, paste it (Cmd+V)
                if should_paste:
                    logger.info("Rework: Pasting reworked text (Cmd+V)")
                    time.sleep(0.2)  # Longer delay to ensure clipboard is fully updated
                    try:
                        cmd_key = get_platform_modifier_key()
                        pyautogui.hotkey(cmd_key, 'v')
                        time.sleep(0.1)  # Small delay after paste to ensure it completes
                        logger.info("PASTE COMMAND EXECUTED")
                        logger.info("Rework: Pasted reworked text")
                    except Exception as e:
                        logger.error(f"Rework: Error executing paste: {e}", exc_info=True)
                        return f"Reworked {len(clipboard_text)} characters but failed to paste: {str(e)}"
                    return f"Reworked {len(clipboard_text)} characters and pasted the result."
                else:
                    return f"Reworked {len(clipboard_text)} characters. The improved text is now in your clipboard."
                
            except ImportError:
                return "Error: Ollama library not available. Please install it: pip install ollama"
            except Exception as e:
                logger.error(f"Error reworking text with LLM: {e}", exc_info=True)
                return f"Error reworking text: {str(e)}"
            
        except Exception as e:
            logger.error(f"Error in ReworkClipboardTool: {e}", exc_info=True)
            return f"Error executing rework: {str(e)}"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        # Filter out any unexpected arguments
        return self._run(text=text)

