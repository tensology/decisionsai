"""
Create Cursor Ticket Tool for LangChain.

When the user says "tell cursor …" (voice fast-action or LLM), this tool writes a cleaned
ticket markdown file. If a project is active with a folder_location, the file goes to
``<project>/.tickets/`` (same as create_project_ticket and the VS Code extension). Otherwise
it falls back to ``~/.cursor/decisionsai/tickets/``.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import os
import re
import subprocess
import platform
from datetime import datetime

logger = logging.getLogger(__name__)

def get_clipboard_content() -> Optional[str]:
    """Get text content from system clipboard using platform-specific methods."""
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

class CreateCursorTicketTool(BaseTool):
    """Tool for creating a ticket file in the active project's .tickets or Cursor's tickets folder."""

    name: str = "create_cursor_ticket"
    description: str = """Create a ticket file when user says 'can you tell cursor', 'create a ticket', 'tell cursor what's in the clipboard', or 'tell cursor that instruction'.
    If a project is active (in use) with a folder path, the file is written to that project's .tickets/ folder.
    Otherwise the file is written under ~/.cursor/decisionsai/tickets/.
    The tool cleans up and summarizes the content into a well-formatted ticket.
    
    Usage:
    - "can you tell cursor [message]" -> Creates a cleaned up ticket file with the message content
    - "create a ticket [message]" -> Creates a ticket file with the message content
    - "tell cursor what's in the clipboard" -> Creates a ticket from clipboard content
    - "tell cursor that instruction" -> Creates a ticket summarizing recent conversation
    """
    
    llm_service: Optional[Any] = Field(default=None, exclude=True)
    llm_model: str = Field(default="qwen3:8b", exclude=True)
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, llm_service=None, llm_model: str = "qwen3:8b", chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self.llm_service = llm_service
        self.llm_model = llm_model or "qwen3:8b"
        self.chat_manager = chat_manager
        self._last_ticket_path = None
        self._last_tickets_folder = None
    
    def _get_recent_conversation_summary(self, max_messages: int = 10) -> Optional[str]:
        """Get a summary of recent conversation messages."""
        if not self.chat_manager:
            logger.warning("CreateCursorTicket: Chat manager not available for conversation summary")
            return None
        
        try:
            current_chat_id = self.chat_manager.get_current_chat()
            if not current_chat_id:
                logger.warning("CreateCursorTicket: No current chat ID available")
                return None
            
            # Get chat history
            history = self.chat_manager.get_chat_history(current_chat_id)
            
            # Filter out system messages and get recent user/assistant messages
            recent_messages = [
                msg for msg in history 
                if msg.get('role') in ['user', 'assistant']
            ][-max_messages:]
            
            if not recent_messages:
                logger.warning("CreateCursorTicket: No recent messages found")
                return None
            
            # Build conversation text
            conversation_text = []
            for msg in recent_messages:
                role = msg.get('role', 'unknown')
                content = msg.get('content', '').strip()
                if content:
                    if role == 'user':
                        conversation_text.append(f"User: {content}")
                    elif role == 'assistant':
                        conversation_text.append(f"Assistant: {content}")
            
            return "\n".join(conversation_text)
            
        except Exception as e:
            logger.error(f"CreateCursorTicket: Error getting conversation summary: {e}", exc_info=True)
            return None
    
    def _generate_cleaned_ticket(self, raw_content: str, is_clipboard: bool = False, is_conversation: bool = False) -> str:
        """Use LLM to generate a cleaned up, summarized ticket from the raw content."""
        try:
            # Determine if we should use OpenAI or Ollama based on model name
            from distr.core.llm_factory import is_openai_model as _is_openai
            is_openai_model = _is_openai(self.llm_model)
            
            # Build the prompt based on content type
            if is_conversation:
                prompt = f"""You are a helpful assistant that creates clean, well-formatted tickets from conversation history. 

The recent conversation:
{raw_content}

IMPORTANT: The user said "tell cursor that instruction" or similar, which means they want you to identify and extract THE SPECIFIC INSTRUCTION OR REQUEST from this conversation. Look for:
- What the user asked for or requested
- What task or feature they want implemented
- What problem they want solved
- What they want you to do

Create a clean, professional ticket that:
- Starts with a clear, descriptive title on the first line (e.g., "Title: [Brief description of the request]")
- Identifies and extracts the SPECIFIC instruction, request, or task from the conversation
- Focuses on what the user actually wants done (not just a general summary)
- Includes relevant context and details needed to understand the request
- Uses proper formatting and structure
- Organizes the information logically
- Is concise but complete
- Captures the main request or instruction clearly and specifically

If the conversation contains multiple topics, focus on the most recent or most prominent instruction/request.

Write the ticket content directly. Start with "Title: [descriptive title]" on the first line, then provide the detailed description. Do not use quotes around the ticket. Do not add explanations before or after. Just write the cleaned up ticket text itself.

Cleaned ticket:"""
            elif is_clipboard:
                prompt = f"""You are a helpful assistant that creates clean, well-formatted tickets from clipboard content. 

The clipboard contains: "{raw_content}"

Create a clean, professional ticket that:
- Starts with a clear, descriptive title on the first line (e.g., "Title: [Brief description]")
- Summarizes the key points clearly
- Uses proper formatting and structure
- Organizes the information logically
- Is concise but complete
- Preserves important technical details, code snippets, or specific information

Write the ticket content directly. Start with "Title: [descriptive title]" on the first line, then provide the detailed description. Do not use quotes around the ticket. Do not add explanations before or after. Just write the cleaned up ticket text itself.

Cleaned ticket:"""
            else:
                prompt = f"""You are a helpful assistant that creates clean, well-formatted tickets from user messages. 

The user said: "{raw_content}"

Create a clean, professional ticket that:
- Starts with a clear, descriptive title on the first line (e.g., "Title: [Brief description]")
- Summarizes the key points clearly
- Uses proper formatting and structure
- Removes filler words, hesitations, and speech artifacts
- Organizes the information logically
- Is concise but complete

Write the ticket content directly. Start with "Title: [descriptive title]" on the first line, then provide the detailed description. Do not use quotes around the ticket. Do not add explanations before or after. Just write the cleaned up ticket text itself.

Cleaned ticket:"""
            
            logger.info(f"CreateCursorTicket: Generating cleaned ticket from {len(raw_content)} chars (model: {self.llm_model}, is_openai: {is_openai_model})")
            
            # Call LLM synchronously - use OpenAI or Ollama based on model
            if is_openai_model and self.llm_service:
                # Use OpenAI
                try:
                    from openai import OpenAI
                    # Get API key from the service's AsyncOpenAI client
                    # The client is an AsyncOpenAI instance, we need to access its api_key
                    api_key = None
                    if hasattr(self.llm_service, 'client') and self.llm_service.client:
                        # AsyncOpenAI stores api_key in _client._api_key or similar
                        api_key = getattr(self.llm_service.client, 'api_key', None)
                        if not api_key:
                            # Try to get from the internal client
                            internal_client = getattr(self.llm_service.client, '_client', None)
                            if internal_client:
                                api_key = getattr(internal_client, 'api_key', None)
                    
                    if api_key:
                        openai_client = OpenAI(api_key=api_key)
                        # Build request parameters - some models (o1) use max_completion_tokens instead of max_tokens
                        request_params = {
                            "model": self.llm_model,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": prompt
                                }
                            ]
                        }
                        # Check if model is o1/o3 series (uses max_completion_tokens, no temperature)
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
                                logger.info(f"CreateCursorTicket: Model requires max_completion_tokens, retrying...")
                                request_params.pop('max_tokens', None)
                                request_params.pop('temperature', None)
                                request_params["max_completion_tokens"] = 500
                                response = openai_client.chat.completions.create(**request_params)
                                raw_response = response.choices[0].message.content.strip()
                            else:
                                raise  # Re-raise if it's a different error
                    else:
                        raise ValueError("OpenAI API key not found")
                except Exception as e:
                    logger.warning(f"CreateCursorTicket: Could not use OpenAI ({e}), falling back to Ollama")
                    is_openai_model = False
            
            if not is_openai_model:
                # Use Ollama - normalize model name if it's an OpenAI model name
                import ollama
                client = ollama.Client()
                
                # If model name looks like OpenAI, use a default Ollama model
                ollama_model = self.llm_model
                if _is_openai(ollama_model):
                    ollama_model = "qwen3:8b"  # Default fallback
                    logger.info(f"CreateCursorTicket: Using default Ollama model {ollama_model} instead of {self.llm_model}")
                
                response = client.chat(
                    model=ollama_model,
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
                
                # Extract the cleaned ticket from the response
                raw_response = response.get('message', {}).get('content', '').strip()
                
                # Fallback: if the structure is different, try to get it from response directly
                if not raw_response:
                    raw_response = response.get('content', '').strip()
                    if not raw_response and isinstance(response, dict):
                        for key in ['response', 'text', 'output']:
                            if key in response:
                                raw_response = str(response[key]).strip()
                                break
            
            if not raw_response:
                logger.warning("CreateCursorTicket: LLM returned empty response, using raw content")
                return raw_content
            
            # Post-process: Remove quotes and explanations if present
            cleaned_ticket = raw_response.strip()
            
            # Remove common quote marks
            quote_chars = ['"""', "'''", '`', '"', "'"]
            for quote in quote_chars:
                if cleaned_ticket.startswith(quote) and cleaned_ticket.endswith(quote):
                    cleaned_ticket = cleaned_ticket[len(quote):-len(quote)].strip()
            
            # Remove explanation prefixes
            explanation_patterns = [
                r'^here\'s[^"]*',
                r'^here is[^"]*',
                r'^the ticket is[^"]*',
                r'^ticket[^"]*:',
            ]
            
            for pattern in explanation_patterns:
                if re.match(pattern, cleaned_ticket, re.IGNORECASE):
                    # Try to extract text after colon or newline
                    for separator in [':', '—', '–', '-', '\n']:
                        if separator in cleaned_ticket:
                            parts = cleaned_ticket.split(separator, 1)
                            if len(parts) > 1 and len(parts[1].strip()) > 10:
                                cleaned_ticket = parts[1].strip()
                                break
            
            # Final validation
            if len(cleaned_ticket.strip()) < 10:
                logger.warning("CreateCursorTicket: Post-processing resulted in too-short ticket, using raw content")
                return raw_content
            
            logger.info(f"CreateCursorTicket: Generated cleaned ticket ({len(cleaned_ticket)} chars)")
            return cleaned_ticket
            
        except ImportError:
            logger.warning("CreateCursorTicket: Ollama not available, using raw content")
            return raw_content
        except Exception as e:
            logger.error(f"CreateCursorTicket: Error generating cleaned ticket: {e}", exc_info=True)
            return raw_content  # Fallback to raw content on error
    
    def _run(self, text: str = "", **kwargs) -> str:
        """Execute create cursor ticket action."""
        try:
            logger.info(f"CreateCursorTicket: _run called with text: '{text}'")
            
            if not text:
                return "Error: No text provided to create cursor ticket."
            
            text_lower = text.lower() if text else ""
            
            # Check if user wants to open the last created ticket file
            if any(phrase in text_lower for phrase in [
                "open the ticket",
                "open that ticket",
                "open the ticket file",
                "open that file",
                "open the file"
            ]) and self._last_ticket_path and os.path.exists(self._last_ticket_path):
                if self._open_ticket_file(self._last_ticket_path):
                    return f"Opened ticket file: {os.path.basename(self._last_ticket_path)}"
                else:
                    return f"Error: Could not open ticket file: {self._last_ticket_path}"
            
            # Check if user wants to open the tickets folder
            if any(phrase in text_lower for phrase in [
                "open the tickets folder",
                "open that folder",
                "open the folder",
                "open tickets folder",
                "show me the tickets folder"
            ]) and self._last_tickets_folder and os.path.exists(self._last_tickets_folder):
                if self._open_tickets_folder(self._last_tickets_folder):
                    return f"Opened tickets folder: {self._last_tickets_folder}"
                else:
                    return f"Error: Could not open tickets folder: {self._last_tickets_folder}"
            
            # Check if user wants to summarize recent conversation ("that instruction")
            use_conversation = any(phrase in text_lower for phrase in [
                "that instruction",
                "that request",
                "what i just said",
                "what we just discussed",
                "the conversation",
                "what was said",
                "that thing",
                "that"
            ])
            
            # Check if user wants to use clipboard content
            use_clipboard = any(phrase in text_lower for phrase in [
                "what's in the clipboard",
                "what is in the clipboard",
                "whats in the clipboard",
                "from the clipboard",
                "from clipboard",
                "clipboard content",
                "the clipboard"
            ])
            
            if use_conversation:
                # Get recent conversation summary - get more messages for better context
                logger.info("CreateCursorTicket: Detected conversation summary request")
                conversation_text = self._get_recent_conversation_summary(max_messages=20)  # Increased from 10 to 20
                if not conversation_text:
                    return "Error: Could not retrieve recent conversation. Please try again or specify your message directly."
                
                raw_content = conversation_text
                is_clipboard = False
                is_conversation = True
                logger.info(f"CreateCursorTicket: Using conversation summary ({len(raw_content)} chars)")
            elif use_clipboard:
                # Get clipboard content
                logger.info("CreateCursorTicket: Detected clipboard request")
                clipboard_text = get_clipboard_content()
                if not clipboard_text or not clipboard_text.strip():
                    return "Error: Clipboard is empty. Please copy some content first."
                
                raw_content = clipboard_text.strip()
                is_clipboard = True
                is_conversation = False
                logger.info(f"CreateCursorTicket: Using clipboard content ({len(raw_content)} chars)")
            else:
                # Extract the message after "can you tell cursor", "tell cursor", or "create a ticket"
                pattern = r'can\s+you\s+tell\s+cursor\s+(.+)'
                match = re.search(pattern, text, re.IGNORECASE)
                
                if not match:
                    # Try "tell cursor"
                    pattern = r'tell\s+cursor\s+(.+)'
                    match = re.search(pattern, text, re.IGNORECASE)
                
                if not match:
                    # Try "create a ticket"
                    pattern = r'create\s+(?:a\s+)?ticket\s+(.+)'
                    match = re.search(pattern, text, re.IGNORECASE)
                
                if not match:
                    logger.warning(f"CreateCursorTicket: Could not extract message from text: '{text}'")
                    return "Error: Could not extract message from command. Try 'tell cursor [message]' or 'create a ticket [message]'."
                
                raw_content = match.group(1).strip()
                is_clipboard = False
                is_conversation = False
                
                if not raw_content:
                    logger.warning(f"CreateCursorTicket: Empty content after extraction from text: '{text}'")
                    return "Error: No message found after command."
                
                logger.info(f"CreateCursorTicket: Extracted message: '{raw_content[:100]}...'")
            
            # Generate cleaned up ticket using LLM
            content_type = 'conversation' if is_conversation else ('clipboard' if is_clipboard else 'message')
            logger.info(f"CreateCursorTicket: Processing {content_type} content: {raw_content[:100]}...")
            ticket_content = self._generate_cleaned_ticket(raw_content, is_clipboard=is_clipboard, is_conversation=is_conversation)
            
            # Extract title if present (format: "Title: [title text]")
            title = None
            description = ticket_content
            
            title_match = re.match(r'^Title:\s*(.+?)(?:\n|$)', ticket_content, re.IGNORECASE | re.MULTILINE)
            if title_match:
                title = title_match.group(1).strip()
                # Remove the title line from description
                description = re.sub(r'^Title:\s*.+?\n', '', ticket_content, flags=re.IGNORECASE | re.MULTILINE).strip()
                if not description:
                    description = ticket_content  # Fallback if removal left nothing
            
            # Prefer active project's .tickets (same as create_project_ticket / extension watchers)
            tickets_dir = None
            project_label = None
            try:
                from distr.core.agent.services.rag.project import get_active_project
                ap = get_active_project()
                folder = (ap or {}).get("folder_location") or ""
                folder = folder.strip()
                if folder:
                    tickets_dir = os.path.join(folder, ".tickets")
                    project_label = (ap or {}).get("name") or "project"
                    logger.info("CreateCursorTicket: using active project .tickets at %s", tickets_dir)
            except Exception as e:
                logger.warning("CreateCursorTicket: could not resolve active project: %s", e)

            if not tickets_dir:
                home_dir = os.path.expanduser("~")
                tickets_dir = os.path.join(home_dir, ".cursor", "decisionsai", "tickets")
                logger.info("CreateCursorTicket: using global Cursor tickets dir %s", tickets_dir)

            os.makedirs(tickets_dir, exist_ok=True)
            
            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Use extracted title if available, otherwise create from description
            if title:
                safe_title = re.sub(r'[^\w\s-]', '', title[:50])
            else:
                safe_title = re.sub(r'[^\w\s-]', '', description[:50])
            safe_title = re.sub(r'[-\s]+', '-', safe_title).strip('-')
            if not safe_title:
                safe_title = "ticket"
            
            filename = f"{timestamp}_{safe_title}.md"
            filepath = os.path.join(tickets_dir, filename)
            
            # Format the ticket content with proper structure
            if title:
                formatted_content = f"""# Ticket

**Created:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Description

**Title:** {title}

{description}
"""
            else:
                formatted_content = f"""# Ticket

**Created:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Description

{description}
"""
            
            # Write the ticket file
            # Prepend decisions-meta comment if running inside a workflow
            meta_header = self._build_decisions_meta()
            if meta_header:
                formatted_content = meta_header + formatted_content
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(formatted_content)
            
            logger.info(f"Created cursor ticket: {filepath}")

            # Store paths for later opening
            self._last_ticket_path = filepath
            self._last_tickets_folder = tickets_dir

            # Return success message with option to open file or folder
            result = f"Successfully created ticket file: {filename}\n"
            result += f"Location: {filepath}\n"
            if project_label:
                result += f"(In active project \"{project_label}\" under .tickets)\n"
            result += f"\nWould you like me to:\n"
            result += f"  • Open the ticket file (say 'open the ticket' or 'open that file')\n"
            result += f"  • Open the tickets folder (say 'open the tickets folder' or 'open that folder')"
            
            return result
            
        except Exception as e:
            logger.error(f"Error creating cursor ticket: {e}", exc_info=True)
            return f"Error creating ticket: {str(e)}"
    
    def _build_decisions_meta(self) -> str:
        """Build a decisions-meta HTML comment if running inside a workflow run.
        
        Checks DECISIONS_WORKFLOW_RUN_ID and DECISIONS_WORKFLOW_STEP_ID env vars
        set by the workflow service. Returns empty string if not in a workflow context.
        """
        run_id = os.environ.get("DECISIONS_WORKFLOW_RUN_ID")
        step_id = os.environ.get("DECISIONS_WORKFLOW_STEP_ID")
        if not run_id:
            return ""
        try:
            import json as _json
            api_base = os.environ.get("DECISIONS_API_BASE", "http://localhost:5555")
            meta = {
                "run_id": int(run_id),
                "step_id": int(step_id) if step_id else 0,
                "api_base": api_base,
            }
            return f"<!-- decisions-meta: {_json.dumps(meta)} -->\n"
        except (ValueError, TypeError) as e:
            logger.warning(f"CreateCursorTicket: Could not build decisions-meta: {e}")
            return ""
    
    def _open_ticket_file(self, filepath: str) -> bool:
        """Open the ticket file with default application."""
        try:
            system = platform.system()
            if system == 'Darwin':  # macOS
                subprocess.run(['open', filepath], check=True)
            elif system == 'Windows':
                os.startfile(filepath)
            else:  # Linux
                subprocess.run(['xdg-open', filepath], check=True)
            return True
        except Exception as e:
            logger.error(f"Error opening ticket file: {e}", exc_info=True)
            return False
    
    def _open_tickets_folder(self, folder_path: str) -> bool:
        """Open the tickets folder in file manager."""
        try:
            system = platform.system()
            if system == 'Darwin':  # macOS
                subprocess.run(['open', folder_path], check=True)
            elif system == 'Windows':
                os.startfile(folder_path)
            else:  # Linux
                subprocess.run(['xdg-open', folder_path], check=True)
            return True
        except Exception as e:
            logger.error(f"Error opening tickets folder: {e}", exc_info=True)
            return False
    
    async def _arun(self, text: str = "", **kwargs) -> str:
        return self._run(text=text)

