"""
Summarize Clipboard Tool

A tool that summarizes clipboard content using an independent LLM.
Supports "summarize this" (copies selection, summarizes, pastes) and
"summarize from clipboard" (uses existing clipboard, no paste).
"""

import logging
import time
from typing import Optional, Any

from langchain.tools import BaseTool
from pydantic import Field

import pyautogui
# Disable pyautogui FAILSAFE to prevent mouse operations from being blocked
pyautogui.FAILSAFE = False
from distr.core.agent.tools.base import get_platform_modifier_key

logger = logging.getLogger(__name__)


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


def set_clipboard_content(text: str) -> bool:
    """Set text content to system clipboard using platform-specific methods."""
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


class SummarizeClipboardTool(BaseTool):
    """
    Tool to summarize clipboard content using an independent LLM.
    
    Supports:
    - "summarize this" - copies selected text, summarizes it, places in clipboard, and pastes
    - "summarize from clipboard" - uses existing clipboard content, summarizes, places in clipboard (no paste)
    """
    
    name: str = "summarize_clipboard"
    description: str = (
        "Summarizes text from the clipboard or spoken message using an independent LLM. "
        "Use this tool when the user says 'summarize this', 'summarize and paste this', 'summarize from clipboard', or 'summarize and read'. "
        "For 'summarize this', the tool will copy selected text, summarize it, and paste the summary. "
        "For 'summarize and paste this', the tool will summarize the spoken message itself and paste the summary. "
        "For 'summarize from clipboard', it uses existing clipboard content and places the summary back in the clipboard. "
        "For 'summarize and read', it returns the summary text to be spoken by the agent. "
        "The summary will be conversational and natural. "
        "MUST call this tool immediately when the user requests a summary. "
        "Do NOT ask the user to provide text - the tool will automatically copy the selected text or use the spoken message and process it."
    )
    
    llm_model: str = Field(default="qwen3:8b", exclude=True)
    llm_service: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, llm_model: str = "qwen3:8b", llm_service=None, **kwargs):
        super().__init__(**kwargs)
        self.llm_model = llm_model or "qwen3:8b"
        self.llm_service = llm_service
    
    def _run(self, text: str = "", **kwargs) -> str:
        """
        Summarize clipboard content.
        
        Args:
            text: The user's command text to determine behavior
                - "summarize this" -> copy selection, summarize, paste
                - "summarize and paste this" -> summarize the spoken message itself, paste
                - "summarize from clipboard" -> use existing clipboard, summarize, no paste
                - "summarize and read" -> summarize and return text for TTS
        
        Returns:
            Status message indicating success or error, or the summary text itself
        """
        try:
            # Determine behavior based on text
            text_lower = text.lower() if text else ""
            use_clipboard_directly = "summarize from clipboard" in text_lower or ("summarize" in text_lower and "clipboard" in text_lower and "this" not in text_lower)
            should_read = "read" in text_lower
            # "summarize and paste this" means summarize the spoken message itself
            summarize_spoken_message = "summarize and paste this" in text_lower or ("summarize" in text_lower and "paste" in text_lower and "this" in text_lower and "from clipboard" not in text_lower)
            should_paste = ("summarize this" in text_lower or summarize_spoken_message) and not use_clipboard_directly and not should_read
            
            logger.info(f"Summarize: use_clipboard_directly={use_clipboard_directly}, should_paste={should_paste}, should_read={should_read}, summarize_spoken_message={summarize_spoken_message}")
            
            # Step 1: Get text to summarize
            clipboard_text = None
            
            if summarize_spoken_message:
                # Extract the message from the spoken text (after "summarize and paste this" or similar)
                import re
                # Try to extract the message part
                patterns = [
                    r'summarize\s+and\s+paste\s+this\s+(.+)',
                    r'summarize\s+paste\s+this\s+(.+)',
                    r'summarize\s+and\s+paste\s+(.+)',
                ]
                
                extracted_message = None
                for pattern in patterns:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        extracted_message = match.group(1).strip()
                        break
                
                if extracted_message and len(extracted_message) > 5:
                    # We have a message after "this"
                    clipboard_text = extracted_message
                    logger.info(f"Summarize: Using extracted spoken message ({len(clipboard_text)} chars): {clipboard_text[:100]}...")
                else:
                    # No message after "this" - use the whole spoken text (remove the command part)
                    # Remove common command phrases
                    cleaned_text = re.sub(r'^(can\s+you\s+|could\s+you\s+|please\s+)?summar(ize|ise)\s+(and\s+)?paste\s+this\s*\.?\s*', '', text, flags=re.IGNORECASE).strip()
                    if cleaned_text and len(cleaned_text) > 5:
                        clipboard_text = cleaned_text
                        logger.info(f"Summarize: Using cleaned spoken text ({len(clipboard_text)} chars): {clipboard_text[:100]}...")
                    else:
                        # Fallback: use the whole text
                        clipboard_text = text
                        logger.info(f"Summarize: Using full text as fallback ({len(clipboard_text)} chars)")
            elif not use_clipboard_directly:
                # Copy selected text
                logger.info("Summarize: Copying selected text (Cmd+C)")
                try:
                    cmd_key = get_platform_modifier_key()
                    pyautogui.hotkey(cmd_key, 'c')
                    time.sleep(0.2)  # Wait for clipboard to update
                except Exception as e:
                    logger.error(f"Summarize: Error copying text: {e}", exc_info=True)
                    return f"Error copying selected text: {str(e)}"
                
                # Get clipboard content
                clipboard_text = get_clipboard_content()
            else:
                # Use clipboard directly
                clipboard_text = get_clipboard_content()
            
            if not clipboard_text or not clipboard_text.strip():
                if summarize_spoken_message:
                    return "Error: Could not extract message from your speech. Please try again."
                else:
                    return "Error: No text found in clipboard. Please select text and try again."
            
            logger.info(f"Summarize: Got clipboard content ({len(clipboard_text)} chars)")
            
            # Step 3: Summarize using independent LLM
            try:
                # Determine if we should use OpenAI or Ollama based on model name
                from distr.core.llm_factory import is_openai_model as _is_openai
                is_openai_model = _is_openai(self.llm_model)
                
                # Create a prompt to summarize the text conversationally
                prompt = f"""You are a helpful assistant that summarizes text. Summarize the following text in a conversational, natural way. Make it sound like you're explaining it to a friend - casual, clear, and easy to understand. Keep it concise but capture the main points.

IMPORTANT: Write the summary directly. Do not use quotes around the summary. Do not add explanations before or after the summary. Just write the summary text itself.

Text to summarize:
{clipboard_text}

Summary:"""
                
                logger.info(f"Summarize: Sending {len(clipboard_text)} characters to LLM for summarization (model: {self.llm_model}, is_openai: {is_openai_model})")
                
                # Call LLM synchronously - use OpenAI or Ollama based on model
                if is_openai_model and self.llm_service:
                    # Use OpenAI
                    try:
                        from openai import OpenAI
                        # Get API key from the service's AsyncOpenAI client
                        api_key = None
                        if hasattr(self.llm_service, 'client') and self.llm_service.client:
                            api_key = getattr(self.llm_service.client, 'api_key', None)
                            if not api_key:
                                internal_client = getattr(self.llm_service.client, '_client', None)
                                if internal_client:
                                    api_key = getattr(internal_client, 'api_key', None)
                        
                        if api_key:
                            openai_client = OpenAI(api_key=api_key)
                            request_params = {
                                "model": self.llm_model,
                                "messages": [{"role": "user", "content": prompt}]
                            }
                            # Check if model is o1/o3 series
                            if self.llm_model and ('o1' in self.llm_model.lower() or 'o3' in self.llm_model.lower()):
                                request_params["max_completion_tokens"] = 500
                            else:
                                request_params["temperature"] = 0.7
                                request_params["max_tokens"] = 500
                            
                            try:
                                response = openai_client.chat.completions.create(**request_params)
                                raw_response = response.choices[0].message.content.strip()
                            except Exception as api_error:
                                # If max_tokens is not supported, retry with max_completion_tokens
                                error_str = str(api_error).lower()
                                if 'max_tokens' in error_str and 'max_completion_tokens' in error_str:
                                    logger.info(f"Summarize: Model requires max_completion_tokens, retrying...")
                                    request_params.pop('max_tokens', None)
                                    request_params.pop('temperature', None)
                                    request_params["max_completion_tokens"] = 500
                                    response = openai_client.chat.completions.create(**request_params)
                                    raw_response = response.choices[0].message.content.strip()
                                else:
                                    raise
                        else:
                            raise ValueError("OpenAI API key not found")
                    except Exception as e:
                        logger.warning(f"Summarize: Could not use OpenAI ({e}), falling back to Ollama")
                        is_openai_model = False
                
                if not is_openai_model:
                    # Use Ollama
                    import ollama
                    client = ollama.Client()
                    
                    # Call LLM synchronously (this is independent of the pipeline)
                    # keep_alive=-1 keeps the model loaded in memory indefinitely to avoid reload delays
                    response = client.chat(
                        model=self.llm_model,
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
                
                # Extract the summary from the response
                if not is_openai_model:
                    # Ollama response structure - extract from response
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
                # Note: For OpenAI, raw_response is already extracted above (line 252 or 262)
                
                if not raw_response:
                    logger.warning("Summarize: LLM returned empty response")
                    return "Error: LLM did not return a summary."
                
                # Validate response - check for invalid responses like just quotes or punctuation
                raw_response_cleaned = raw_response.strip()
                # Remove common quote marks and check if there's actual content
                test_content = raw_response_cleaned.strip('"\'`"""\'\'\'')
                if len(test_content) < 10 or raw_response_cleaned in ['"""', "'''", '""', "''", '`', '"', "'"]:
                    logger.warning(f"Summarize: LLM returned invalid response (just quotes/punctuation): {raw_response_cleaned}")
                    # Try once more with a more explicit prompt
                    retry_prompt = f"""Summarize this text in 2-3 sentences. Write the summary directly without quotes or explanations:

{clipboard_text}

Summary:"""
                    try:
                        if is_openai_model and self.llm_service:
                            # Retry with OpenAI
                            from openai import OpenAI
                            api_key = None
                            if hasattr(self.llm_service, 'client') and self.llm_service.client:
                                api_key = getattr(self.llm_service.client, 'api_key', None)
                                if not api_key:
                                    internal_client = getattr(self.llm_service.client, '_client', None)
                                    if internal_client:
                                        api_key = getattr(internal_client, 'api_key', None)
                            
                            if api_key:
                                openai_client = OpenAI(api_key=api_key)
                                request_params = {
                                    "model": self.llm_model,
                                    "messages": [{"role": "user", "content": retry_prompt}]
                                }
                                if self.llm_model and ('o1' in self.llm_model.lower() or 'o3' in self.llm_model.lower()):
                                    request_params["max_completion_tokens"] = 500
                                else:
                                    request_params["temperature"] = 0.7
                                    request_params["max_tokens"] = 500
                                
                                retry_response = openai_client.chat.completions.create(**request_params)
                                raw_response = retry_response.choices[0].message.content.strip()
                            else:
                                raise ValueError("OpenAI API key not found")
                        else:
                            # Retry with Ollama
                            import ollama
                            retry_client = ollama.Client()
                            retry_response = retry_client.chat(
                                model=self.llm_model,
                                messages=[{"role": "user", "content": retry_prompt}],
                                options={"keep_alive": -1}
                            )
                            raw_response = retry_response.get('message', {}).get('content', '').strip()
                        
                        if not raw_response or len(raw_response.strip('"\'`')) < 10:
                            return f"Error: LLM returned invalid summary. Got: {raw_response[:50]}"
                    except Exception as retry_e:
                        logger.error(f"Summarize: Retry failed: {retry_e}")
                        return f"Error: LLM returned invalid summary: {raw_response_cleaned}"
                
                # Log the raw response for debugging
                logger.info(f"Summarize: Raw LLM response ({len(raw_response)} chars): {raw_response[:200]}...")
                
                # Post-process: Extract just the summary if LLM included explanations or quotes
                import re
                summary_text = raw_response.strip()
                
                # Step 1: Remove common explanation prefixes and extract the actual summary
                explanation_patterns = [
                    (r'^it appears[^"]*', 'it appears'),
                    (r'^however[^"]*', 'however'),
                    (r'^here\'s[^"]*', "here's"),
                    (r'^here is[^"]*', 'here is'),
                    (r'^i can help[^"]*', 'i can help'),
                    (r'^the following[^"]*', 'the following'),
                    (r'^the summary is[^"]*', 'the summary is'),
                    (r'^summary[^"]*:', 'summary'),
                ]
                
                for pattern, label in explanation_patterns:
                    if re.match(pattern, summary_text, re.IGNORECASE):
                        logger.info(f"Summarize: Detected explanation prefix: {label}")
                        # Try to extract quoted text (usually the actual summary)
                        # Match both single and double quotes, including triple quotes
                        quoted_matches = []
                        # Double quotes (including triple)
                        quoted_matches.extend(re.findall(r'"""([^"]+)"""', summary_text, re.DOTALL))
                        quoted_matches.extend(re.findall(r'"([^"]+)"', summary_text))
                        # Single quotes (including triple)
                        quoted_matches.extend(re.findall(r"'''([^']+)'''", summary_text, re.DOTALL))
                        quoted_matches.extend(re.findall(r"'([^']+)'", summary_text))
                        # Backticks
                        quoted_matches.extend(re.findall(r'`([^`]+)`', summary_text))
                        
                        if quoted_matches:
                            # Use the longest quoted text (usually the actual summary)
                            summary_text = max(quoted_matches, key=len).strip()
                            logger.info(f"Summarize: Extracted quoted text from explanation ({len(summary_text)} chars)")
                            break
                        
                        # If no quotes, try to find text after colon, dash, or newline
                        for separator in [':', '—', '–', '-', '\n']:
                            if separator in summary_text:
                                parts = summary_text.split(separator, 1)
                                if len(parts) > 1:
                                    candidate = parts[1].strip()
                                    if candidate and len(candidate) > 10:
                                        summary_text = candidate.strip()
                                        logger.info(f"Summarize: Extracted text after '{separator}' ({len(summary_text)} chars)")
                                        break
                                if len(summary_text) > 10:
                                    break
                        if len(summary_text) > 10:
                            break
                
                # Step 2: Strip quotes from the beginning and end (handle various quote types)
                # Remove leading/trailing quotes (single, double, triple, backticks)
                quote_chars = ['"', "'", '`', '"""', "'''"]
                original_length = len(summary_text)
                for quote in quote_chars:
                    # Remove from start
                    while summary_text.startswith(quote):
                        summary_text = summary_text[len(quote):].lstrip()
                    # Remove from end
                    while summary_text.endswith(quote):
                        summary_text = summary_text[:-len(quote)].rstrip()
                
                # Also handle cases where quotes might be on separate lines
                summary_text = summary_text.strip()
                if summary_text.startswith('"') and summary_text.endswith('"'):
                    summary_text = summary_text[1:-1].strip()
                if summary_text.startswith("'") and summary_text.endswith("'"):
                    summary_text = summary_text[1:-1].strip()
                if summary_text.startswith('`') and summary_text.endswith('`'):
                    summary_text = summary_text[1:-1].strip()
                
                # Step 3: Remove any remaining explanation suffixes
                suffix_patterns = [
                    r'\s*\([^)]*summary[^)]*\)\s*$',
                    r'\s*\[[^\]]*summary[^\]]*\]\s*$',
                ]
                for pattern in suffix_patterns:
                    summary_text = re.sub(pattern, '', summary_text, flags=re.IGNORECASE).strip()
                
                # Step 4: Final validation - ensure we have actual content
                if len(summary_text.strip()) < 10:
                    logger.warning(f"Summarize: Post-processing resulted in too-short summary ({len(summary_text)} chars)")
                    # Fallback to original if post-processing removed too much
                    if len(raw_response.strip()) > len(summary_text):
                        summary_text = raw_response.strip()
                        # Just do basic quote stripping
                        for quote in ['"""', "'''", '`', '"', "'"]:
                            if summary_text.startswith(quote) and summary_text.endswith(quote):
                                summary_text = summary_text[len(quote):-len(quote)].strip()
                
                if original_length != len(summary_text):
                    logger.info(f"Summarize: Post-processing cleaned response from {original_length} to {len(summary_text)} chars")
                
                logger.info(f"Summarize: Final summary text ({len(summary_text)} chars)")
                
                # Step 4: Handle output based on mode
                if should_read:
                    # For "summarize and read" - return the summary text directly for TTS
                    # DO NOT put it back in clipboard to avoid infinite loops
                    logger.info("Summarize: Returning summary text for TTS (not updating clipboard)")
                    return summary_text  # Return the summary directly for TTS
                
                # For paste or clipboard-only modes, update clipboard
                if not set_clipboard_content(summary_text):
                    return "Error: Failed to update clipboard with summary."
                
                logger.info("Summarize: Updated clipboard with summary")
                
                # Verify clipboard was set correctly
                verify_clipboard = get_clipboard_content()
                if verify_clipboard != summary_text:
                    logger.warning(f"Summarize: Clipboard verification failed. Expected {len(summary_text)} chars, got {len(verify_clipboard) if verify_clipboard else 0} chars")
                else:
                    logger.info(f"CLIPBOARD VERIFIED: {len(summary_text)} characters ready to paste")
                
                # Step 5: Handle output (Paste or just Clipboard)
                if should_paste:
                    logger.info("Summarize: Pasting summary (Cmd+V)")
                    time.sleep(0.2)  # Longer delay to ensure clipboard is fully updated
                    try:
                        cmd_key = get_platform_modifier_key()
                        pyautogui.hotkey(cmd_key, 'v')
                        time.sleep(0.1)  # Small delay after paste to ensure it completes
                        logger.info("PASTE COMMAND EXECUTED")
                        logger.info("Summarize: Pasted summary")
                    except Exception as e:
                        logger.error(f"Summarize: Error executing paste: {e}", exc_info=True)
                        return f"Summarized {len(clipboard_text)} characters but failed to paste: {str(e)}"
                    return f"Summarized {len(clipboard_text)} characters and pasted the result."
                else:
                    return f"Summarized {len(clipboard_text)} characters. The summary is now in your clipboard."
                
            except ImportError:
                return "Error: Ollama library not available. Please install it: pip install ollama"
            except Exception as e:
                logger.error(f"Error summarizing text with LLM: {e}", exc_info=True)
                return f"Error summarizing text: {str(e)}"
            
        except Exception as e:
            logger.error(f"Error in SummarizeClipboardTool: {e}", exc_info=True)
            return f"Error executing summarize: {str(e)}"
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        # Filter out any unexpected arguments
        return self._run(text=text)

