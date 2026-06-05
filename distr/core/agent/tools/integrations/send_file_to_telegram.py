"""
Send File to Telegram Tool

A tool that finds files on the local machine and sends them via Telegram.
Supports images (jpg, png, gif, webp) and documents (pdf, doc, docx, txt, etc.).
"""

import logging
import os
import glob
import mimetypes
from pathlib import Path
from typing import Optional, Any
from langchain.tools import BaseTool
from pydantic import Field

logger = logging.getLogger(__name__)


class SendFileToTelegramTool(BaseTool):
    """Tool for finding and sending local files via Telegram."""
    
    name: str = "send_file_to_telegram"
    description: str = """Send a file from your local machine via Telegram.
    
    ⚠️ CRITICAL: DO NOT CALL THIS TOOL DIRECTLY IF YOU NEED TO SEARCH FOR A FILE!
    - If the user describes a file without giving the exact filename (e.g., "SOP & CO rules file"), you MUST use execute_code FIRST to search for it
    - Only call this tool directly if:
      * The user provides an exact filename (e.g., "send me report.pdf")
      * You already have the file path from execute_code (tool chaining)
      * The file path is explicitly provided in the user's message
    
    WHEN TO USE execute_code FIRST (REQUIRED):
    - User says: "There's an SOP & CO rules file in my downloads. Send it to me" → Use execute_code to search Downloads first
    - User says: "Send me a random picture from my Pictures folder" → Use execute_code to find random file first
    - User says: "There's a PDF in my Documents folder, send it" → Use execute_code to search for PDF files first
    - After execute_code finds the file, it returns the path with [ACTION REQUIRED], then you chain to this tool
    
    WHEN TO USE THIS TOOL DIRECTLY (ONLY IF):
    - User provides exact filename: "Send me report.pdf" → Can call directly with file_name="report.pdf"
    - You have file path from execute_code: Pass file_path="/path/to/file" from the execute_code result
    - File path is in user's message: Extract and pass as file_path parameter
    
    The tool will:
    1. Accept file paths directly (preferred - from execute_code results)
    2. Search for the file by name in common locations (Downloads, Desktop, Documents) if file_name is provided
    3. Use keyword-based search if text parameter contains file description (fallback only)
    4. If found, send it via Telegram
    5. Supports multiple file types:
       - Images: jpg, png, gif, webp, bmp, svg, tiff
       - Documents: pdf, doc, docx, txt, xls, xlsx, csv, ppt, pptx, zip, rar
       - Audio: mp3, wav, ogg, m4a, aac, flac, wma, opus
       - Video: mp4, avi, mov, mkv, wmv, flv, webm, m4v
    
    TOOL CHAINING (PRIMARY USE CASE):
    - This tool is designed to be called AFTER execute_code finds a file
    - If execute_code returns a file path, pass it directly via file_path parameter
    - If execute_code returns text with "Result: /path/to/file", the tool will extract it automatically
    - Example workflow: execute_code to find file → send_file_to_telegram with file_path from result
    
    Input parameters:
    - file_path (str, preferred): Direct file path from execute_code result. ALWAYS use this when you have the path from execute_code
    - file_name (str, optional): Exact filename if user provided it (e.g., "report.pdf")
    - text (str, optional): User's original message - only use as fallback if file_path and file_name are not available
    
    Examples:
    - file_path="~/Downloads/SOP_CO_Rules.pdf" or file_path="/path/to/file.pdf" → uses path directly (from execute_code)
    - file_name="report.pdf" → searches for report.pdf (only if user gave exact filename)
    - text="Result: ~/Pictures/random_image.png" → extracts path from execute_code result text
    """
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    event_queue: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, event_queue=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
        self._event_queue = event_queue
    
    def _search_file(self, file_name: str) -> Optional[str]:
        """
        Search for a file by name in common locations.
        
        Args:
            file_name: Name or partial name of the file to find
            
        Returns:
            Full path to the file if found, None otherwise
        """
        # Remove quotes if present
        file_name = file_name.strip('"\'')
        
        # If it's already a full path and exists, return it
        if os.path.isabs(file_name) and os.path.exists(file_name):
            return file_name
        
        # Get common search directories
        home_dir = os.path.expanduser("~")
        search_dirs = [
            os.path.join(home_dir, "Downloads"),
            os.path.join(home_dir, "Desktop"),
            os.path.join(home_dir, "Documents"),
            os.path.join(home_dir, "Documents", "Downloads"),  # Some systems
            os.getcwd(),  # Current working directory
        ]
        
        # Also search in subdirectories of these locations (limited depth)
        search_patterns = []
        for search_dir in search_dirs:
            if os.path.exists(search_dir):
                # Exact filename match
                search_patterns.append(os.path.join(search_dir, file_name))
                # With wildcard for partial matches
                search_patterns.append(os.path.join(search_dir, f"*{file_name}*"))
                # In subdirectories (one level deep)
                for subdir in ["**"]:
                    search_patterns.append(os.path.join(search_dir, subdir, file_name))
                    search_patterns.append(os.path.join(search_dir, subdir, f"*{file_name}*"))
        
        # Search for files
        found_files = []
        for pattern in search_patterns:
            try:
                matches = glob.glob(pattern, recursive=True)
                # Limit recursive depth to avoid too many results
                if "**" in pattern:
                    matches = [m for m in matches if os.path.relpath(m, os.path.dirname(pattern)).count(os.sep) <= 2]
                found_files.extend(matches)
            except Exception as e:
                logger.debug(f"Error searching pattern {pattern}: {e}")
        
        # Remove duplicates and filter to only files (not directories)
        found_files = list(set([f for f in found_files if os.path.isfile(f)]))
        
        if not found_files:
            return None
        
        # If multiple matches, prefer exact filename matches
        exact_matches = [f for f in found_files if os.path.basename(f).lower() == file_name.lower()]
        if exact_matches:
            # Prefer files in Downloads or Desktop
            for preferred_dir in ["Downloads", "Desktop"]:
                for match in exact_matches:
                    if preferred_dir in match:
                        return match
            return exact_matches[0]
        
        # Return first match if no exact match
        return found_files[0]
    
    def _search_file_by_keywords(self, keywords: list, search_dirs: list) -> Optional[str]:
        """
        Search for files matching keywords in their filename.
        
        Args:
            keywords: List of keywords to search for (e.g., ['SOP', 'CO', 'rules'])
            search_dirs: List of directories to search in
            
        Returns:
            Full path to the file if found, None otherwise
        """
        found_files = []
        for search_dir in search_dirs:
            if not os.path.exists(search_dir):
                continue
            
            # Search for files containing all keywords (case-insensitive)
            try:
                for root, dirs, files in os.walk(search_dir):
                    # Limit depth to 2 levels to avoid too many results
                    depth = root[len(search_dir):].count(os.sep)
                    if depth > 2:
                        dirs[:] = []  # Don't recurse deeper
                        continue
                    
                    for file in files:
                        file_lower = file.lower()
                        # Check if file contains all keywords
                        if all(keyword.lower() in file_lower for keyword in keywords if keyword):
                            found_files.append(os.path.join(root, file))
            except Exception as e:
                logger.debug(f"Error searching {search_dir} for keywords {keywords}: {e}")
        
        if not found_files:
            return None
        
        # Prefer files in Downloads or Desktop
        for preferred_dir in ["Downloads", "Desktop"]:
            for match in found_files:
                if preferred_dir in match:
                    return match
        
        # Return first match
        return found_files[0]
    
    def _get_file_type(self, file_path: str) -> str:
        """Determine file type (image, document, audio, video, etc.)"""
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type:
            if mime_type.startswith('image/'):
                return 'image'
            elif mime_type.startswith('audio/'):
                return 'audio'
            elif mime_type.startswith('video/'):
                return 'video'
            elif mime_type in ['application/pdf', 'application/msword', 
                              'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                              'application/vnd.ms-excel',
                              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                              'application/vnd.ms-powerpoint',
                              'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                              'text/plain', 'application/rtf',
                              'application/vnd.oasis.opendocument.text',
                              'application/vnd.oasis.opendocument.spreadsheet',
                              'application/vnd.oasis.opendocument.presentation']:
                return 'document'
        
        # Fallback to extension
        ext = os.path.splitext(file_path)[1].lower()
        # Images
        if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.ico', '.tiff', '.tif']:
            return 'image'
        # Audio
        elif ext in ['.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.wma', '.opus', '.mp4a']:
            return 'audio'
        # Video
        elif ext in ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.mpg', '.mpeg']:
            return 'video'
        # Documents
        elif ext in ['.pdf', '.doc', '.docx', '.txt', '.rtf', '.odt',
                    '.xls', '.xlsx', '.csv', '.ods',
                    '.ppt', '.pptx', '.odp',
                    '.zip', '.rar', '.7z', '.tar', '.gz']:
            return 'document'
        
        return 'document'  # Default to document
    
    def _run(self, file_name: str = "", text: str = "", file_path: Optional[str] = None, **kwargs) -> str:
        """Find and send file via Telegram."""
        try:
            if not text and kwargs.get("last_user_message"):
                text = str(kwargs.get("last_user_message") or "")
            # If file_path is provided directly (e.g., from execute_code or screenshot_analyzer result), 
            # it means the user explicitly requested it via a tool chain - skip connection check and try to send
            # (the actual send will handle real connection failures gracefully)
            file_path_provided = file_path and os.path.exists(file_path)
            
            # Only check connection if file_path is NOT provided (user is searching for a file by name)
            if not file_path_provided:
                # Check if Telegram is connected
                telegram_connected = False
                if self._chat_manager and hasattr(self._chat_manager, 'telegram_manager'):
                    telegram_connected = self._chat_manager.telegram_manager.is_connected()
                
                # Check if user explicitly requested Telegram (even from desktop)
                user_wants_telegram = False
                if text:
                    telegram_keywords = ['telegram', 'tell the gram', 'send to telegram', 'send by telegram', 'send via telegram']
                    text_lower = text.lower()
                    user_wants_telegram = any(keyword in text_lower for keyword in telegram_keywords)
                
                # Also check if this request came from Telegram
                import threading
                is_telegram_request = bool(kwargs.get("is_telegram_request")) or (
                    hasattr(threading.current_thread(), 'telegram_request') and threading.current_thread().telegram_request
                )
                
                if not is_telegram_request:
                    # Check all threads
                    import threading as threading_module
                    for thread in threading_module.enumerate():
                        if hasattr(thread, 'telegram_request') and thread.telegram_request:
                            is_telegram_request = True
                            break
                
                if not telegram_connected:
                    if not is_telegram_request and not user_wants_telegram:
                        # Desktop request, no Telegram mention, and not connected - return error
                        return "Error: Telegram is not connected. Please connect Telegram in settings before sending files. If you want to send a file, make sure Telegram is connected first."
                    else:
                        # User explicitly wants Telegram OR request came from Telegram - try anyway (might reconnect or connection status might be stale)
                        logger.warning(f"SendFileToTelegramTool: Telegram connection check returned False, but user wants Telegram (is_telegram_request={is_telegram_request}, user_wants_telegram={user_wants_telegram}) - attempting anyway")
            else:
                # file_path provided from tool chain - skip connection check, just try to send
                logger.info(f"SendFileToTelegramTool: file_path provided from tool chain - skipping connection check, attempting to send")
            
            # If file_path is provided directly (e.g., from execute_code result), use it
            if file_path_provided:
                logger.info(f"SendFileToTelegramTool: Using provided file_path: {file_path}")
                target_file = Path(file_path)
            elif file_name:
                # Use provided file_name
                logger.info(f"SendFileToTelegramTool: Searching for file: {file_name}")
                target_file = self._search_file(file_name)
                if not target_file:
                    return f"Error: File '{file_name}' not found. I searched in Downloads, Desktop, Documents, and current directory."
            elif text:
                # Try to extract file path or filename from text
                # First, check if text contains a file path (from execute_code result)
                import re
                
                # Pattern 1: Look for absolute paths (e.g., ~/Pictures/image.jpg, /path/to/file, C:\path\to\file)
                path_pattern = r'(/[^\s]+|~/[^\s]+|[A-Z]:\\[^\s]+)'
                path_match = re.search(path_pattern, text)
                if path_match:
                    potential_path = os.path.expanduser(path_match.group(1))
                    if os.path.exists(potential_path) and os.path.isfile(potential_path):
                        logger.info(f"SendFileToTelegramTool: Found file path in text: {potential_path}")
                        target_file = Path(potential_path)
                    else:
                        target_file = None
                else:
                    target_file = None
                
                # Pattern 2: If no path found, look for "Result:" section (from execute_code)
                if target_file is None:
                    result_match = re.search(r'Result:\s*(.+)', text, re.MULTILINE | re.DOTALL)
                    if result_match:
                        result_value = result_match.group(1).strip()
                        # Check if result is a file path
                        expanded_result = os.path.expanduser(result_value)
                        if os.path.exists(expanded_result) and os.path.isfile(expanded_result):
                            logger.info(f"SendFileToTelegramTool: Found file path in Result section: {expanded_result}")
                            target_file = Path(expanded_result)
                        else:
                            # Try treating result as filename
                            target_file = self._search_file(result_value)
                
                # Pattern 3: If still no file, try extracting filename from text
                if target_file is None:
                    text_lower = text.lower()
                    patterns = [
                        r'send\s+(?:me\s+)?(?:the\s+)?(?:file\s+)?([^\s]+\.\w+)',
                        r'send\s+(?:me\s+)?([^\s]+\.\w+)',
                        r'([^\s]+\.\w+)',
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, text_lower)
                        if match:
                            file_name = match.group(1)
                            target_file = self._search_file(file_name)
                            if target_file:
                                break
                    
                    # Pattern 4: If still no file, try keyword-based search
                    # Extract meaningful keywords from the text (skip common words)
                    if target_file is None:
                        import re
                        # Extract keywords: words that are likely part of a filename
                        # Skip common words and very short words
                        stop_words = {'send', 'me', 'the', 'file', 'please', 'telegram', 'result', 'output', 
                                     'there', 'is', 'are', 'in', 'my', 'downloads', 'folder', 'a', 'an', 'to', 'it',
                                     's', 'co', 'and', 'or', 'of', 'for', 'with', 'from'}
                        # Split by common separators and extract words
                        # Handle "SOP & CO" -> ["SOP", "CO"]
                        text_cleaned = re.sub(r'[&+]', ' ', text)  # Replace & and + with space
                        words = re.findall(r'\b\w+\b', text_cleaned.lower())
                        keywords = [w for w in words if len(w) > 1 and w not in stop_words]
                        # Also try preserving case for acronyms (SOP, CO, etc.)
                        text_cleaned_upper = re.sub(r'[&+]', ' ', text)
                        words_upper = re.findall(r'\b[A-Z]{2,}\b', text_cleaned_upper)  # Find acronyms (2+ uppercase letters)
                        keywords.extend([w.upper() for w in words_upper if w.upper() not in [k.upper() for k in keywords]])
                        
                        if keywords:
                            logger.info(f"SendFileToTelegramTool: Trying keyword-based search with: {keywords}")
                            home_dir = os.path.expanduser("~")
                            search_dirs = [
                                os.path.join(home_dir, "Downloads"),
                                os.path.join(home_dir, "Desktop"),
                                os.path.join(home_dir, "Documents"),
                            ]
                            target_file = self._search_file_by_keywords(keywords, search_dirs)
                            if target_file:
                                logger.info(f"SendFileToTelegramTool: Found file via keyword search: {target_file}")
                    
                    # Pattern 5: If still no file, try searching individual words
                    if target_file is None:
                        words = text.split()
                        for word in words:
                            word = word.strip('.,!?;:"\'&')
                            if len(word) > 3 and not word.lower() in ['send', 'me', 'the', 'file', 'please', 'telegram', 'result', 'output', 'downloads']:
                                target_file = self._search_file(word)
                                if target_file:
                                    break
            else:
                # Clear telegram_file_sent flag since we're returning an error (no file sent)
                import threading
                if hasattr(threading.current_thread(), 'telegram_file_sent'):
                    threading.current_thread().telegram_file_sent = False
                if self._chat_manager and hasattr(self._chat_manager, 'llm_service') and self._chat_manager.llm_service:
                    if hasattr(self._chat_manager.llm_service, '_tts_service') and self._chat_manager.llm_service._tts_service:
                        self._chat_manager.llm_service._tts_service._telegram_file_sent = False
                return "Error: No file name, file path, or text provided. Please specify which file you want to send."
            
            if not target_file:
                # Clear telegram_file_sent flag since we're returning an error (no file sent)
                import threading
                if hasattr(threading.current_thread(), 'telegram_file_sent'):
                    threading.current_thread().telegram_file_sent = False
                if self._chat_manager and hasattr(self._chat_manager, 'llm_service') and self._chat_manager.llm_service:
                    if hasattr(self._chat_manager.llm_service, '_tts_service') and self._chat_manager.llm_service._tts_service:
                        self._chat_manager.llm_service._tts_service._telegram_file_sent = False
                return "Error: Could not find the file. Please provide a file name, file path, or ensure the file exists in common directories."
            
            # Convert Path to string for further processing
            file_path = str(target_file)
            
            if not os.path.exists(file_path):
                return f"Error: File found but does not exist: {file_path}"
            
            # Check file size (Telegram has limits, but we'll try anyway)
            file_size = os.path.getsize(file_path)
            file_size_mb = file_size / (1024 * 1024)

            if file_size <= 0:
                return f"Error: Refusing to send empty file: {file_path}"
            
            if file_size_mb > 50:  # Telegram limit is around 50MB for documents
                return f"Error: File is too large ({file_size_mb:.1f} MB). Telegram limit is 50MB."
            
            logger.info(f"SendFileToTelegramTool: Sending file: {file_path} ({file_size_mb:.2f} MB)")
            
            # Determine file type
            file_type = self._get_file_type(file_path)
            
            # CRITICAL: Set flag that a file is being sent.
            # This prevents TTS service and LLM from sending "Done" message that would override the file
            import threading
            threading.current_thread().telegram_file_sent = True
            # Also set on TTS service instance if available (for cross-thread access)
            if self._chat_manager and hasattr(self._chat_manager, 'llm_service') and self._chat_manager.llm_service:
                if hasattr(self._chat_manager.llm_service, '_tts_service') and self._chat_manager.llm_service._tts_service:
                    self._chat_manager.llm_service._tts_service._telegram_file_sent = True
                    logger.info(f"SendFileToTelegramTool: Set _telegram_file_sent=True on TTS service instance")
            
            # Send via event queue
            if self._event_queue:
                if file_type == 'image':
                    # Send as image file (use send_file_to_telegram event to bypass cursor drawing)
                    self._event_queue.put(('send_file_to_telegram', {
                        'file_path': file_path,
                        'file_name': os.path.basename(file_path),
                        'file_type': 'image',
                        'explicit_artifact_intent': True,
                    }))
                elif file_type == 'audio':
                    # Send as audio file
                    self._event_queue.put(('send_file_to_telegram', {
                        'file_path': file_path,
                        'file_name': os.path.basename(file_path),
                        'file_type': 'audio',
                        'explicit_artifact_intent': True,
                    }))
                elif file_type == 'video':
                    # Send as video file
                    self._event_queue.put(('send_file_to_telegram', {
                        'file_path': file_path,
                        'file_name': os.path.basename(file_path),
                        'file_type': 'video',
                        'explicit_artifact_intent': True,
                    }))
                else:
                    # Send as document (PDF, Excel, Word, etc.)
                    self._event_queue.put(('send_file_to_telegram', {
                        'file_path': file_path,
                        'file_name': os.path.basename(file_path),
                        'file_type': 'document',
                        'explicit_artifact_intent': True,
                    }))
                
                return f"Found and sending {file_type}: {os.path.basename(file_path)} ({file_size_mb:.2f} MB)"
            else:
                return "Error: Event queue not available. Cannot send file to Telegram."
                
        except Exception as e:
            logger.error(f"Error in SendFileToTelegramTool: {e}", exc_info=True)
            return f"Error sending file: {str(e)}"
    
    async def _arun(self, file_name: str = "", text: str = "", file_path: Optional[str] = None, **kwargs) -> str:
        """Async run method"""
        # Ignore extra kwargs like 'last_user_message' that may be passed by LLM service
        return self._run(file_name=file_name, text=text, file_path=file_path)
