"""
Fast Action Detector - Pre-LLM pattern matching for instant tool routing.

This module provides ultra-fast detection of common commands before sending to LLM,
dramatically reducing latency for frequent operations like clipboard actions.

The detector distinguishes between:
- "this" context (copy selection first, then process)
- "clipboard" context (use existing clipboard content)
- Conversational queries (route to LLM for context-aware response)
"""

import re
import logging
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ActionType(Enum):
    """Types of actions that can be detected."""
    CLIPBOARD_GET = "clipboard_get"           # Get/read clipboard content
    CLIPBOARD_COPY = "clipboard_copy"         # Copy selection
    CLIPBOARD_CUT = "clipboard_cut"           # Cut selection
    CLIPBOARD_PASTE = "clipboard_paste"       # Paste
    CLIPBOARD_EXPLAIN = "clipboard_explain"   # Explain clipboard/selection
    CLIPBOARD_ELABORATE = "clipboard_elaborate"  # Elaborate on clipboard/selection
    CLIPBOARD_SUMMARIZE = "clipboard_summarize"  # Summarize clipboard/selection
    CLIPBOARD_REWRITE = "clipboard_rewrite"   # Rewrite clipboard/selection (paste back)
    CLIPBOARD_REWORD = "clipboard_reword"     # Reword clipboard/selection (paste back)
    CLIPBOARD_READ = "clipboard_read"         # Read clipboard aloud (TTS)
    SNIPPET_CREATE = "snippet_create"         # Create snippet from clipboard
    SNIPPET_USE = "snippet_use"               # Use/paste a snippet
    ACTION_CREATE = "action_create"           # Create new action
    ACTION_PLAY = "action_play"               # Play/execute an action
    ACTION_START_RECORDING = "action_start_recording"  # Start recording action
    ACTION_STOP_RECORDING = "action_stop_recording"    # Stop recording action
    CLEAR_CHAT = "clear_chat"                 # Clear chat history
    NEW_CHAT = "new_chat"                     # Start new chat
    EXIT_APP = "exit_app"                     # Exit application
    OPEN_WINDOW = "open_window"                # Open/focus application or URL
    CHANGE_ORACLE = "change_oracle"           # Change oracle/globe image
    CHANGE_MODE = "change_mode"               # Change input mode (PTT/Continuous)
    MOUSE_MOVEMENT = "mouse_movement"          # Move mouse
    MOUSE_ACTION = "mouse_action"             # Mouse click/scroll
    KEYBOARD_SHORTCUT = "keyboard_shortcut"    # Keyboard shortcuts
    MEDIA_CONTROL = "media_control"            # Media playback control
    CARET_MOVEMENT = "caret_movement"          # Cursor/caret movement
    SPECIAL_KEY = "special_key"                # Special key presses
    FUNCTION_KEY = "function_key"              # Function key presses (F1-F12)
    SAVE_AUDIO = "save_audio"                  # Save text as audio
    FILE_OPERATIONS = "file_operations"        # Fast file operations (list, read, etc.)
    FILE_COPY = "file_copy"                    # Copy file/folder
    FILE_MOVE = "file_move"                    # Move file/folder
    FILE_DELETE = "file_delete"                # Delete file/folder
    FILE_RENAME = "file_rename"                # Rename file/folder
    FILE_CREATE = "file_create"                # Create file/folder (complex, needs execute_code)
    FILE_CONVERT = "file_convert"              # Convert audio/video files to different formats
    DOCUMENT_CONVERT = "document_convert"      # Convert documents (MD → PDF/DOCX/Google Doc)
    IMAGE_GENERATE = "image_generate"          # Generate image using image_generator tool
    SCREENSHOT_ANALYZE = "screenshot_analyze"   # Analyze screenshot with vision model
    CURSOR_TICKET = "cursor_ticket"            # Create ticket file in Cursor tickets folder
    AUDIO_TRANSCRIBE = "audio_transcribe"      # Transcribe audio files
    CONVERSATIONAL = "conversational"         # Pass to LLM for response
    UNKNOWN = "unknown"                       # Need LLM to determine


@dataclass
class DetectedAction:
    """Result of action detection."""
    action_type: ActionType
    tool_name: str
    tool_args: Dict[str, Any]
    needs_copy_first: bool  # Whether to copy selection before processing
    response_type: str  # "done", "tts", "paste_back", "llm_response"
    confidence: float  # 0.0 to 1.0
    original_text: str


class FastActionDetector:
    """
    Fast pre-LLM action detection for common commands.
    
    This runs before the LLM to instantly route common patterns,
    reducing latency from ~2s to <50ms for known actions.
    """
    
    def __init__(self):
        # Compile regex patterns for speed
        self._compile_patterns()
        
    def _compile_patterns(self):
        """Pre-compile regex patterns for fast matching."""
        # Context indicators
        self.THIS_PATTERN = re.compile(r'\b(this|that|these|those|it)\b', re.IGNORECASE)
        self.CLIPBOARD_PATTERN = re.compile(r'\b(clipboard|clip board|copied|clipped)\b', re.IGNORECASE)
        self.SELECTION_PATTERN = re.compile(r'\b(selected|selection|highlighted|text)\b', re.IGNORECASE)
        
        # Action patterns - ordered by specificity (most specific first)
        self.action_patterns = [
            # === CURSOR TICKET ACTIONS ===
            # Clipboard variants (more specific, must come first)
            (re.compile(r'\btell\s+cursor\s+(what\'?s?\s+in\s+the\s+clipboard|what\s+is\s+in\s+the\s+clipboard|whats\s+in\s+the\s+clipboard|from\s+the\s+clipboard|from\s+clipboard|clipboard\s+content|the\s+clipboard)', re.IGNORECASE),
             ActionType.CURSOR_TICKET, "create_cursor_ticket", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\bcan\s+you\s+tell\s+cursor\s+(what\'?s?\s+in\s+the\s+clipboard|what\s+is\s+in\s+the\s+clipboard|whats\s+in\s+the\s+clipboard|from\s+the\s+clipboard|from\s+clipboard|clipboard\s+content|the\s+clipboard)', re.IGNORECASE),
             ActionType.CURSOR_TICKET, "create_cursor_ticket", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            # Regular message variants
            (re.compile(r'\bcan\s+you\s+tell\s+cursor\s+(.+)', re.IGNORECASE),
             ActionType.CURSOR_TICKET, "create_cursor_ticket", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\btell\s+cursor\s+(.+)', re.IGNORECASE),
             ActionType.CURSOR_TICKET, "create_cursor_ticket", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === AUDIO TRANSCRIPTION ===
            # "transcribe this", "transcribe that", "transcribe it", "transcribe the audio"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?transcribe\s+(this|that|it|the\s+audio)\b', re.IGNORECASE),
             ActionType.AUDIO_TRANSCRIBE, "audio_transcriber", {"audio_file_path": None}, False, "llm_response"),
            # "transcribe this file", "transcribe that file"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?transcribe\s+(this|that|the)\s+file\b', re.IGNORECASE),
             ActionType.AUDIO_TRANSCRIBE, "audio_transcriber", {"audio_file_path": None}, False, "llm_response"),
            # "transcribe the audio file", "transcribe audio"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?transcribe\s+(the\s+)?audio\b', re.IGNORECASE),
             ActionType.AUDIO_TRANSCRIBE, "audio_transcriber", {"audio_file_path": None}, False, "llm_response"),
            
            # === SNIPPET ACTIONS ===
            (re.compile(r'\b(create|make|save|new)\b.*\bsnippet\b', re.IGNORECASE), 
             ActionType.SNIPPET_CREATE, "create_snippet", {}, False, "done"),
            (re.compile(r'\b(use|paste|insert|get)\b.*\bsnippet\b', re.IGNORECASE), 
             ActionType.SNIPPET_USE, "use_snippet", {}, False, "done"),
            (re.compile(r'\bsnippet\b.*\b(create|make|save)\b', re.IGNORECASE), 
             ActionType.SNIPPET_CREATE, "create_snippet", {}, False, "done"),
            
            # === ACTION ACTIONS ===
            # More specific patterns first - "run action X", "play action X", "execute action X"
            (re.compile(r'\b(run|play|execute)\s+action\s+([^\n]+)', re.IGNORECASE), 
             ActionType.ACTION_PLAY, "play_action", {"text": "__ORIGINAL_TEXT__", "action_name": "__MATCH_GROUP_LAST__"}, False, "action_playback"),
            # Generic patterns - "run action", "play action", "execute action" (no extra words between verb and "action")
            # Negative lookahead prevents matching conversational phrases like "how to run this action in Python"
            (re.compile(r'\b(run|play|execute)\s+action\b(?!\s+in\b|\s+with\b|\s+from\b|\s+using\b|\s+on\b)', re.IGNORECASE), 
             ActionType.ACTION_PLAY, "play_action", {"text": "__ORIGINAL_TEXT__"}, False, "action_playback"),
            (re.compile(r'\b(create|make|save|new)\b.*\baction\b', re.IGNORECASE), 
             ActionType.ACTION_CREATE, "create_action", {}, False, "done"),
            (re.compile(r'\baction\b.*\b(create|make|save)\b', re.IGNORECASE), 
             ActionType.ACTION_CREATE, "create_action", {}, False, "done"),
            (re.compile(r'\b(start|begin)\b.*\brecording\b', re.IGNORECASE), 
             ActionType.ACTION_START_RECORDING, "start_recording", {}, False, "done"),
            (re.compile(r'\b(stop|end|finish)\b.*\brecording\b', re.IGNORECASE), 
             ActionType.ACTION_STOP_RECORDING, "stop_recording", {}, False, "done"),
            
            # === CLIPBOARD READ (TTS) ===
            # IMPORTANT: More specific patterns must come FIRST
            # "read this", "read that", "read it" - tool handles copy internally
            # needs_copy_first=False because clipboard_action handles copy for "this" context
            (re.compile(r'^read\s+(this|that|it)\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_READ, "clipboard_action", {"action": "read", "text": "__ORIGINAL_TEXT__"}, False, "tts"),
            (re.compile(r'\bread\s+(this|that|it)\s*(aloud|out\s*loud|to\s*me)?\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_READ, "clipboard_action", {"action": "read", "text": "__ORIGINAL_TEXT__"}, False, "tts"),
            # "read from clipboard", "read the clipboard", "read my clipboard", "read clipboard"
            # This reads the clipboard content ALOUD via TTS
            # NOTE: Must come AFTER "summarize and read" patterns to avoid false matches
            # Use negative lookahead to exclude "summarize and read" cases
            (re.compile(r'^(?!.*\bsummar(ize|ise)\b).*\bread\b.*\b(from\s+)?(the\s+|my\s+)?clipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_READ, "clipboard_action", {"action": "get"}, False, "tts_clipboard"),
            
            # === CLIPBOARD GET (show content, LLM responds) ===
            # "what's in the clipboard", "what is in the clipboard", "show clipboard"
            (re.compile(r'\b(what\'?s?\s+(in|on)|show|see)\b.*\bclipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_GET, "clipboard_action", {"action": "get"}, False, "llm_response"),
            # "get the clipboard", "get clipboard"
            (re.compile(r'\bget\b.*\bclipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_GET, "clipboard_action", {"action": "get"}, False, "llm_response"),
            
            # === FILE OPERATIONS (fast direct execution for simple operations) ===
            # "take a look at what's in", "look at what's in", "see what's in"
            (re.compile(r'\b(take\s+a\s+look\s+at|look\s+at|see)\s+(what\'?s?\s+in|what\s+is\s+in)\s+(my\s+)?(desktop|documents|downloads|pictures|music|videos|folder|directory)\b', re.IGNORECASE), 
             ActionType.FILE_OPERATIONS, "file_operations", {"operation": "list", "path": "__EXTRACT_PATH__"}, False, "llm_response"),
            # "list files", "list the files", "show files", "what files", "tell me what files"
            (re.compile(r'\b(list|show|what)\s+(the\s+)?files?\b.*\b(in|on|that\s+are)\b', re.IGNORECASE), 
             ActionType.FILE_OPERATIONS, "file_operations", {"operation": "list", "path": "__EXTRACT_PATH__"}, False, "llm_response"),
            # "list files in my downloads", "files in my desktop"
            (re.compile(r'\bfiles?\s+(in|on)\s+(my\s+)?(desktop|documents|downloads|pictures|music|videos|folder|directory)\b', re.IGNORECASE), 
             ActionType.FILE_OPERATIONS, "file_operations", {"operation": "list", "path": "__EXTRACT_PATH__"}, False, "llm_response"),
            # NOTE: Simple file operations (copy, move, delete, rename) are NOT fast-detected
            # EXCEPT delete — LLMs frequently misroute "delete <file>" to text_editing
            # "delete the file", "delete it", "can you delete it", "delete that file"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?(delete|remove)\s+(it|that|this|the\s+\w+|that\s+\w+|this\s+\w+)\b.*\b(file|image|photo|picture|document|folder|video)?\b', re.IGNORECASE),
             ActionType.FILE_DELETE, "file_operations", {"operation": "delete", "path": "__EXTRACT_PATH__"}, False, "llm_response"),
            # "delete the whatsapp image", "remove the screenshot", "delete that pdf"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?(delete|remove)\s+.{0,60}\b(file|image|photo|picture|screenshot|document|pdf|video|folder)\b', re.IGNORECASE),
             ActionType.FILE_DELETE, "file_operations", {"operation": "delete", "path": "__EXTRACT_PATH__"}, False, "llm_response"),
            # Other file ops (copy, move, rename) go to LLM which routes to execute_code or file_operations
            
            # === DOCUMENT CONVERSION (MD → PDF/DOCX, must come BEFORE audio conversion) ===
            # "convert to pdf", "convert this to pdf", "convert that to a pdf", "make a pdf"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?(convert|turn|export|make)\s+(this|that|it|the\s+file)?\s*(in)?to\s+(a\s+)?(pdf|docx|word)\s*(document|file|doc)?\b', re.IGNORECASE),
             ActionType.DOCUMENT_CONVERT, "convert_document", {"output_format": "__EXTRACT_DOC_FORMAT__"}, False, "llm_response"),
            # "make a pdf of this", "create a pdf from this"
            (re.compile(r'\b(make|create|generate)\s+(a\s+)?(pdf|docx|word)\s*(document|file|doc)?\s*(of|from)\b', re.IGNORECASE),
             ActionType.DOCUMENT_CONVERT, "convert_document", {"output_format": "__EXTRACT_DOC_FORMAT__"}, False, "llm_response"),
            # "export as pdf", "save as pdf"
            (re.compile(r'\b(export|save)\s+(this\s+|that\s+|it\s+)?as\s+(a\s+)?(pdf|docx|word)\b', re.IGNORECASE),
             ActionType.DOCUMENT_CONVERT, "convert_document", {"output_format": "__EXTRACT_DOC_FORMAT__"}, False, "llm_response"),

            # === FILE CONVERSION (must come BEFORE generic execute_code patterns) ===
            # Audio/video format conversion patterns - extract target format from text
            # IMPORTANT: More specific patterns must come FIRST
            # "convert this to mp3", "convert that to mp3", "convert it to mp3" (single file)
            (re.compile(r'\bconvert\s+(this|that|it|the\s+file)\s+to\s+(mp3|wav|flac|m4a|ogg|opus|aac|wma|text)\b', re.IGNORECASE), 
             ActionType.FILE_CONVERT, "file_converter", {"target_format": "__EXTRACT_TARGET_FORMAT__", "convert_all": False}, False, "llm_response"),
            # "convert flac to mp3", "convert flac files to mp3", "convert those flac files to mp3"
            (re.compile(r'\bconvert\b.*\b(flac|mp3|wav|m4a|ogg|opus|aac|wma)\s+(files?\s+)?to\s+(mp3|wav|flac|m4a|ogg|opus|aac|wma|text)\b', re.IGNORECASE), 
             ActionType.FILE_CONVERT, "file_converter", {"target_format": "__EXTRACT_TARGET_FORMAT__", "convert_all": True}, False, "llm_response"),
            # "convert the files I just dropped to mp3"
            (re.compile(r'\bconvert\b.*\b(files?\s+)?(I\s+)?(just\s+)?(dropped|gave|provided)\s+to\s+(mp3|wav|flac|m4a|ogg|opus|aac|wma|text)\b', re.IGNORECASE), 
             ActionType.FILE_CONVERT, "file_converter", {"target_format": "__EXTRACT_TARGET_FORMAT__", "convert_all": True}, False, "llm_response"),
            # "convert to mp3", "convert files to mp3", "convert those files to mp3", "convert the files to mp3"
            (re.compile(r'\bconvert\b.*\b(files?\s+)?to\s+(mp3|wav|flac|m4a|ogg|opus|aac|wma|text)\b', re.IGNORECASE), 
             ActionType.FILE_CONVERT, "file_converter", {"target_format": "__EXTRACT_TARGET_FORMAT__", "convert_all": True}, False, "llm_response"),
            
            # === IMAGE GENERATION (must come BEFORE "create file" pattern) ===
            # "create an image", "generate an image", "make an image", "create image", "generate image"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?(create|generate|make|draw|design)\s+(an\s+)?image\b', re.IGNORECASE), 
             ActionType.IMAGE_GENERATE, "image_generator", {"prompt": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            # "create a picture", "generate a picture", "make a picture"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?(create|generate|make|draw|design)\s+(a\s+)?picture\b', re.IGNORECASE), 
             ActionType.IMAGE_GENERATE, "image_generator", {"prompt": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            # "create an icon", "generate an icon", "make an icon", "create icon", "generate icon"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?(create|generate|make|draw|design)\s+(an\s+)?icon\b', re.IGNORECASE), 
             ActionType.IMAGE_GENERATE, "image_generator", {"prompt": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            # "create a logo", "generate a logo", "make a logo"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?(create|generate|make|draw|design)\s+(a\s+)?logo\b', re.IGNORECASE), 
             ActionType.IMAGE_GENERATE, "image_generator", {"prompt": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            # "create an SVG", "generate an SVG", "make an SVG"
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?(create|generate|make|draw|design)\s+(an\s+)?svg\b', re.IGNORECASE), 
             ActionType.IMAGE_GENERATE, "image_generator", {"prompt": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            
            # "create file", "make file", "new file" - route to LLM for proper handling
            # NOTE: Do NOT fast-detect this - it needs LLM to understand context and generate proper content
            # The LLM will use file_operations or execute_code appropriately based on the request
            # REMOVED: This was incorrectly passing raw text as Python code to execute_code
            # (re.compile(r'\b(create|make|new)\b.*\b(file|folder|directory)\b', re.IGNORECASE), 
            #  ActionType.FILE_CREATE, "execute_code", {"code": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            
            # === COPY/CUT/PASTE (text editing only) ===
            (re.compile(r'^copy(\s+this)?\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_COPY, "text_editing", {"operation": "copy"}, False, "done"),
            (re.compile(r'^cut(\s+this)?\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_CUT, "text_editing", {"operation": "cut"}, False, "done"),
            (re.compile(r'^paste\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_PASTE, "text_editing", {"operation": "paste"}, False, "done"),
            
            # === TEXT EDITING (additional) ===
            # "select all"
            (re.compile(r'^select\s+all\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_COPY, "text_editing", {"operation": "select_all"}, False, "done"),
            # "undo"
            (re.compile(r'^undo\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_COPY, "text_editing", {"operation": "undo"}, False, "done"),
            # "redo"
            (re.compile(r'^redo\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_COPY, "text_editing", {"operation": "redo"}, False, "done"),
            # "backspace", "back space"
            (re.compile(r'^(back\s*space|backspace)\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_COPY, "text_editing", {"operation": "backspace"}, False, "done"),
            # "delete"
            (re.compile(r'^delete\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_COPY, "text_editing", {"operation": "delete"}, False, "done"),
            # "clear line", "delete line"
            (re.compile(r'^(clear|delete)\s+line\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_COPY, "text_editing", {"operation": "__CLEAR_DELETE_MATCH__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "force delete"
            (re.compile(r'^force\s+delete\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_COPY, "text_editing", {"operation": "force_delete"}, False, "done"),
            
            # === REWRITE/REWORD (paste back) ===
            # "rewrite this", "reword this", "can you reword this" - tool handles copy & paste based on text
            # Note: tool_args will be updated with original text in detect()
            # needs_copy_first=False because the tool handles copying internally
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?(rewrite|re-write)\b.*\b(this|that|it)\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_REWRITE, "rework_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?(reword|re-word)\b.*\b(this|that|it)\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_REWORD, "rework_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "rewrite the clipboard", "reword clipboard", "reword from clipboard"
            (re.compile(r'\b(rewrite|re-write)\b.*\bclipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_REWRITE, "rework_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(reword|re-word)\b.*\b(from\s+)?clipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_REWORD, "rework_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === EXPLAIN ===
            # "explain this", "explain that", "can you explain this" - tool handles copy internally
            # needs_copy_first=False because clipboard_action handles copy for "this" context
            (re.compile(r'^(can\s+you\s+|could\s+you\s+|please\s+)?explain\s+(this|that|it)\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_EXPLAIN, "clipboard_action", {"action": "explain", "text": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?explain\b.*\b(this|that|it)\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_EXPLAIN, "clipboard_action", {"action": "explain", "text": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            # "explain the clipboard", "explain what's in the clipboard", "explain what is in the clipboard"
            (re.compile(r'\bexplain\b.*\b(what\'?s?\s+(in|on)|what\s+is\s+in)\s+the\s+clipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_EXPLAIN, "clipboard_action", {"action": "explain", "text": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            (re.compile(r'\bexplain\b.*\bclipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_EXPLAIN, "clipboard_action", {"action": "explain", "text": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            
            # === ELABORATE ===
            # "elaborate on this", "elaborate this", "can you elaborate on this" - tool handles copy internally
            (re.compile(r'^(can\s+you\s+|could\s+you\s+|please\s+)?elaborate\s+(on\s+)?(this|that|it)\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_ELABORATE, "clipboard_action", {"action": "elaborate", "text": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?elaborate\b.*\b(this|that|it)\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_ELABORATE, "clipboard_action", {"action": "elaborate", "text": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            # "elaborate on the clipboard", "elaborate clipboard", "elaborate on what's in the clipboard"
            (re.compile(r'\belaborate\b.*\b(what\'?s?\s+(in|on)|what\s+is\s+in)\s+the\s+clipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_ELABORATE, "clipboard_action", {"action": "elaborate", "text": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            (re.compile(r'\belaborate\b.*\bclipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_ELABORATE, "clipboard_action", {"action": "elaborate", "text": "__ORIGINAL_TEXT__"}, False, "llm_response"),
            
            # === SUMMARIZE ===
            # IMPORTANT: More specific patterns must come FIRST
            # "summarize and paste this" - summarize the spoken message and paste it (MOST SPECIFIC)
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?summar(ize|ise)\s+and\s+paste\s+this\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_SUMMARIZE, "summarize_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\bsummar(ize|ise)\s+and\s+paste\s+this\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_SUMMARIZE, "summarize_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "summarize and read from clipboard", "summarize and read this" - must come before generic patterns
            (re.compile(r'\bsummar(ize|ise)\b.*\b(and\s+)?read\b.*\b(from\s+)?clipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_SUMMARIZE, "summarize_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "tts"),
            # "summarize from clipboard and read" - read comes AFTER clipboard
            (re.compile(r'\bsummar(ize|ise)\b.*\b(from\s+)?clipboard\b.*\b(and\s+)?read\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_SUMMARIZE, "summarize_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "tts"),
            (re.compile(r'\bsummar(ize|ise)\b.*\b(and\s+)?read\b.*\b(this|that|it)\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_SUMMARIZE, "summarize_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "tts"),
            # "summarize this", "summarize that", "can you summarize this" - tool handles copy internally
            # Note: tool_args will be updated with original text in detect()
            (re.compile(r'^(can\s+you\s+|could\s+you\s+|please\s+)?summar(ize|ise)\s+(this|that|it)\.?$', re.IGNORECASE), 
             ActionType.CLIPBOARD_SUMMARIZE, "summarize_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(can\s+you\s+|could\s+you\s+|please\s+)?summar(ize|ise)\b.*\b(this|that|it)\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_SUMMARIZE, "summarize_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "summarize the clipboard", "summarize from clipboard"
            (re.compile(r'\bsummar(ize|ise)\b.*\b(from\s+)?clipboard\b', re.IGNORECASE), 
             ActionType.CLIPBOARD_SUMMARIZE, "summarize_clipboard", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === DIRECT SCREENSHOT CAPTURE (no analysis - just send it) ===
            # "give me a screenshot", "give me a screenshot of screen 1", "send me a screenshot"
            (re.compile(r'\b(give|send|show)\s+(me\s+)?a?\s*screenshot\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "all", "direct_send": True}, False, "done"),
            # "take a screenshot and send it", "take a picture and send it to me"
            (re.compile(r'\btake\s+a\s+(screenshot|picture)\b.*\b(send|give)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "all", "direct_send": True}, False, "done"),
            # "screenshot of screen 1/2/3", "screenshot screen 1"
            (re.compile(r'\bscreenshot\s+(of\s+)?screen\s+\d+\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "all", "direct_send": True}, False, "done"),
            # "take a picture of screen 1/2/3 and send it"
            (re.compile(r'\btake\s+a\s+(picture|screenshot)\s+(of\s+)?screen\s+\d+\b.*\b(send|give)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "all", "direct_send": True}, False, "done"),
            
            # === SCREENSHOT ANALYSIS ===
            # "can you see my screen", "can you see what's on my screen", "can you see the screen"
            (re.compile(r'\bcan\s+you\s+see\b.*?(my\s+)?(screen|display|monitor|screens)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "all"}, False, "llm_response"),
            # "look at my screen", "see my screen", "analyze my screen"
            (re.compile(r'\b(look\s+at|see|analyze|check|examine)\b.*?(my\s+)?(screen|display|monitor|screens)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "all"}, False, "llm_response"),
            # "look at this window", "analyze this window", "see this window"
            (re.compile(r'\b(look\s+at|see|analyze|check|examine)\b.*\b(this|that|the)\s+window\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "window"}, False, "llm_response"),
            # "what do you see", "what's on the screen", "describe the screen"
            (re.compile(r'\b(what\s+do\s+you\s+see|what\'?s?\s+on\s+(the\s+)?screen|describe\s+(the\s+)?screen|what\'?s?\s+on\s+my\s+screen)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "full"}, False, "llm_response"),
            # "take a screenshot and tell me what you see", "take a screenshot ... what you see"
            (re.compile(r'\btake\s+a\s+(screenshot|picture)\b.*\b(tell\s+me\s+)?what\s+you\s+see\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "all"}, False, "llm_response"),
            # "why is this not working", "what's wrong with this", "why isn't this loading"
            (re.compile(r'\b(why\s+is\s+(this|that|it)\s+not\s+working|what\'?s?\s+wrong\s+with\s+(this|that|it)|why\s+isn\'?t\s+(this|that|it)\s+loading)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "full"}, False, "llm_response"),
            # "read this screen", "what does this say", "read what's on the screen"
            (re.compile(r'\b(read\s+(this|that|the)\s+screen|what\s+does\s+(this|that|it)\s+say|read\s+what\'?s?\s+on\s+(the\s+)?screen)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "full"}, False, "llm_response"),
            
            # === VISION: CLICK/INTERACT WITH SCREEN ELEMENTS ===
            # "click the Submit button", "click on the search box", "press the OK button"
            (re.compile(r'\b(click|press|tap)\s+(on\s+)?(the\s+)?(.+?)\s+(button|link|icon|tab|menu|option|checkbox|toggle)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # "hover over the menu", "move mouse to the Save button"
            (re.compile(r'\b(hover\s+over|move\s+(?:(?:the|my)\s+)?(?:mouse|mask|cursor)\s+(?:to|over))\s+(the\s+)?(.+?)\s+(button|link|icon|tab|menu|element|logo)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # "move mouse to the end of the word/text/line/search", "move cursor to the beginning of the text"
            # Text-position commands that need vision to locate the position on screen
            (re.compile(r'\b(?:move|put)\s+(?:(?:the|my)\s+)?(?:mouse|mask|cursor|pointer)\s+(?:to|at)\s+(?:the\s+)?(?:end|beginning|start|middle)\s+(?:of\s+)?(?:the\s+)?(?:word|text|line|sentence|search|query|input|paragraph|url|address)', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # "move mouse to the search bar", "move cursor to the address bar", "move mouse to the text field"
            (re.compile(r'\b(?:move|put)\s+(?:(?:the|my)\s+)?(?:mouse|mask|cursor|pointer)\s+(?:to|at)\s+(?:the\s+)?(?:search|address|url|nav|navigation|title|tool|status|menu|task|side|scroll)\s*(?:bar|field|box|area|pane|panel)', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # Broad catch-all: "move mouse to [something]" that isn't a directional command
            # This catches any "move mouse to X" where X is a visual element/position needing screenshot analysis
            # MUST come AFTER all specific mouse_movement directional patterns (center, top, left, etc.)
            # Strategy: exclude when the remaining text after "to" is directional words optionally followed
            # by filler like "of my screen", "of the screen", "of the third screen", "please", etc.
            # "center of my screen" → directional + filler → NO MATCH (mouse_movement handles it)
            # "center of the third screen" → directional + ordinal screen → NO MATCH
            # "center of the picture" → has non-directional target → MATCH (visual target)
            # "center" / "top right" / "the left" → only directional words → NO MATCH
            (re.compile(r'\b(?:move|put)\s+(?:(?:the|my)\s+)?(?:mouse|mask|cursor|pointer)\s+(?:to|over|towards)\s+(?!(?:the\s+)?(?:(?:center|top|bottom|left|right|middle|far|up|down|screen)(?:\s+(?:center|top|bottom|left|right|middle|far|up|down|screen|\d+))*)(?:\s+(?:of\s+)?(?:the\s+|my\s+)?(?:(?:first|second|third|fourth|fifth)\s+)?(?:screen|display|monitor|desktop))?(?:\s*(?:please|now))?\s*[\.\,\?\!]?\s*$).{3,}', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # "double-click the file", "double click the icon"
            (re.compile(r'\bdouble[- ]?click\s+(on\s+)?(the\s+)?(.+?)\s*(button|link|icon|file|folder)?\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # "right-click the desktop", "right click on the file"
            (re.compile(r'\bright[- ]?click\s+(on\s+)?(the\s+)?(.+?)(?:\?|\.|$)', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            
            # === VISION: FIND/LOCATE ELEMENTS ===
            # "where is the Submit button", "find the search box", "locate the settings icon"
            (re.compile(r'\b(where\s+is|find|locate)\s+(the\s+)?(.+?)\s+(button|link|icon|tab|menu|field|box|input|element)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # "find the word X on screen", "search for the text X"
            (re.compile(r'\b(find|search\s+for|look\s+for)\s+(the\s+)?(word|text|phrase)\s+', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # "where is the Wi-Fi icon", "find the battery icon"
            (re.compile(r'\b(where\s+is|find|locate)\s+(the\s+)?(wi-?fi|wifi|bluetooth|battery|volume|sound|network|clock)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            
            # === VISION: READ ERRORS/NOTIFICATIONS ===
            # "what error is showing", "read the error message", "what went wrong"
            (re.compile(r'\b(what\s+error|read\s+the\s+error|what\s+went\s+wrong|what\'?s?\s+the\s+error)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "full"}, False, "llm_response"),
            # "what notification", "read the notification", "what just popped up"
            (re.compile(r'\b(what\s+notification|read\s+the\s+(notification|alert)|what\s+(just\s+)?popped\s+up|what\s+(just\s+)?appeared)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "full"}, False, "llm_response"),
            
            # === VISION: APP/STATE IDENTIFICATION ===
            # "what app is open", "which window is active", "what program is running"
            (re.compile(r'\b(what\s+app|which\s+app|what\s+application|which\s+window|what\s+program)\b.*\b(is\s+)?(open|active|running|focused)?\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "full"}, False, "llm_response"),
            # "is the toggle on", "is it loading", "is the button disabled"
            (re.compile(r'\bis\s+(the\s+)?(toggle|switch|checkbox|button|element)\s+(on|off|enabled|disabled|active|loading)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            (re.compile(r'\bis\s+it\s+(loading|spinning|processing|buffering|done|ready|finished)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            
            # === VISION: COUNT/COMPARE ===
            # "how many tabs are open", "count the icons"
            (re.compile(r'\b(how\s+many|count\s+the)\s+(tabs?|windows?|icons?|buttons?|items?|elements?|files?|notifications?|messages?|emails?)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "full"}, False, "llm_response"),
            # "what changed", "what's different", "compare the screens"
            (re.compile(r'\b(what\s+changed|what\'?s?\s+different|compare|did\s+(anything|something)\s+change)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "full"}, False, "llm_response"),
            
            # === VISION: SCROLL/DRAG ===
            # "scroll down to the footer", "scroll to Settings"
            (re.compile(r'\bscroll\s+(down|up)\s+to\s+(the\s+)?(.+?)(?:\?|\.|$)', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            (re.compile(r'\bscroll\s+to\s+(the\s+)?(.+?)(?:\?|\.|$)', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # "drag the file to the trash", "drag X to Y"
            (re.compile(r'\bdrag\s+(the\s+)?(.+?)\s+(to|into|onto)\s+(the\s+)?(.+?)(?:\?|\.|$)', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            
            # === VISION: FORM/MENU INTERACTION ===
            # "fill in the form", "type hello in the search box"
            (re.compile(r'\b(fill\s+(in\s+)?(the\s+)?form|fill\s+out\s+(the\s+)?form)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            (re.compile(r'\b(type|enter)\s+[\'"]?(.+?)[\'"]?\s+(in|into)\s+(the\s+)?(.+?)\s*(field|box|input)?\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # "open the File menu", "go to Settings > General"
            (re.compile(r'\b(open\s+the)\s+(.+?)\s+(menu|dropdown|submenu)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            # "what's on my other screen", "show me screen 2"
            (re.compile(r'\b(what\'?s?\s+on)\s+(my\s+)?(other|second|third)\s+(screen|monitor|display)\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "all"}, False, "llm_response"),
            # "what am I looking at"
            (re.compile(r'\bwhat\s+am\s+i\s+looking\s+at\b', re.IGNORECASE), 
             ActionType.SCREENSHOT_ANALYZE, "screenshot_analyzer", {"prompt": "__ORIGINAL_TEXT__", "region": "current_mouse_screen"}, False, "llm_response"),
            
            # === CHAT MANAGEMENT ===
            (re.compile(r'\b(clear|reset|wipe)\b.*\b(chat|history|conversation)\b', re.IGNORECASE), 
             ActionType.CLEAR_CHAT, "clear_chat", {"confirm": True}, False, "done"),
            (re.compile(r'\b(new|start|begin)\b.*\b(chat|conversation)\b', re.IGNORECASE), 
             ActionType.NEW_CHAT, "new_chat", {}, False, "done"),
            (re.compile(r'^(start\s+)?over\.?$', re.IGNORECASE), 
             ActionType.NEW_CHAT, "new_chat", {}, False, "done"),
            
            # === EXIT ===
            (re.compile(r'^(exit|quit|goodbye|bye|close)\s*(app|application|decisions)?\.?$', re.IGNORECASE), 
             ActionType.EXIT_APP, "exit_app", {}, False, "llm_response"),
            
            # === OPEN WINDOW/APP (fast detection for common apps) ===
            # Gmail
            (re.compile(r'\bopen\s+gmail\.?$', re.IGNORECASE), 
             ActionType.OPEN_WINDOW, "open_window", {"app_name": "gmail", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\bopen\s+my\s+gmail\.?$', re.IGNORECASE), 
             ActionType.OPEN_WINDOW, "open_window", {"app_name": "gmail", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # YouTube
            (re.compile(r'\bopen\s+youtube\.?$', re.IGNORECASE), 
             ActionType.OPEN_WINDOW, "open_window", {"app_name": "youtube", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # Google
            (re.compile(r'\bopen\s+google\.?$', re.IGNORECASE), 
             ActionType.OPEN_WINDOW, "open_window", {"app_name": "google", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # Twitter/X - extract app name from text
            (re.compile(r'\bopen\s+(twitter|x)\.?$', re.IGNORECASE), 
             ActionType.OPEN_WINDOW, "open_window", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === ORACLE/GLOBE CONTROL ===
            # Change oracle/globe forward - match "change oracle", "change globe", "next oracle", "next globe"
            (re.compile(r'\b(change|next)\s+(oracle|globe)\b', re.IGNORECASE), 
             ActionType.CHANGE_ORACLE, "oracle_globe", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            # Change oracle/globe backward - match "change previous oracle", "previous globe", "go back globe"
            (re.compile(r'\b(change\s+previous|previous|go\s+back)\s+(oracle|globe)\b', re.IGNORECASE), 
             ActionType.CHANGE_ORACLE, "oracle_globe", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === MODE CONTROL (PTT/Continuous) ===
            # "change mode", "switch mode", "toggle mode" - toggle between PTT and continuous
            (re.compile(r'\b(change|switch|toggle)\s+mode\.?$', re.IGNORECASE), 
             ActionType.CHANGE_MODE, "mode_control", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "PTT mode", "push to talk mode", "enable PTT", "switch to PTT"
            (re.compile(r'\b(ptt|push\s+to\s+talk|push-to-talk)\s+mode\.?$', re.IGNORECASE), 
             ActionType.CHANGE_MODE, "mode_control", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(enable|switch\s+to)\s+ptt\.?$', re.IGNORECASE), 
             ActionType.CHANGE_MODE, "mode_control", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "continuous mode", "hands free mode", "hands-free mode", "enable continuous", "switch to continuous"
            (re.compile(r'\b(continuous|hands\s+free|hands-free)\s+mode\.?$', re.IGNORECASE), 
             ActionType.CHANGE_MODE, "mode_control", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(enable|switch\s+to)\s+continuous\.?$', re.IGNORECASE), 
             ActionType.CHANGE_MODE, "mode_control", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === MOUSE ACTIONS (must come BEFORE mouse movement to avoid false matches) ===
            # "click", "click mouse", "left click"
            (re.compile(r'^(left\s+)?click(\s+mouse)?\.?$', re.IGNORECASE), 
             ActionType.MOUSE_ACTION, "mouse_actions", {"action": "click", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "right click", "right-click" - MUST come before mouse movement patterns
            (re.compile(r'\bright\s*[-]?click(\s+mouse)?\.?$', re.IGNORECASE), 
             ActionType.MOUSE_ACTION, "mouse_actions", {"action": "right_click", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "double click", "double-click"
            (re.compile(r'\bdouble\s*[-]?click(\s+mouse)?\.?$', re.IGNORECASE), 
             ActionType.MOUSE_ACTION, "mouse_actions", {"action": "double_click", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "scroll up", "scroll down"
            (re.compile(r'\bscroll\s+(up|down)\.?$', re.IGNORECASE), 
             ActionType.MOUSE_ACTION, "mouse_actions", {"action": "__SCROLL_MATCH__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === MOUSE MOVEMENT ===
            # "move mouse to screen X", "move to screen 1/2/3", "screen 1/2/3" - MUST come before "center" pattern
            (re.compile(r'\b(move\s+)?(mouse\s+)?(to\s+)?screen\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_to_screen", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move to the third screen", "the second screen", "first monitor" - ordinal patterns
            (re.compile(r'\b(?:move\s+)?(?:(?:the|my)\s+)?(?:mouse|mask|mass|miles|mice|moss)\s+(?:to\s+)?(?:the\s+)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(?:screen|monitor|display)\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_to_screen", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(?:move|go)\s+(?:to\s+)?(?:the\s+)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(?:screen|monitor|display)\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_to_screen", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse center", "move mouse to center", "center mouse"
            # "move mouse to the center of my screen", "can you move the mouse to the center"
            # Also handles STT artifacts like "move, move my mouse to the center"
            (re.compile(r'\b(?:can\s+you\s+)?move[\s,]+(?:the\s+|my\s+)?(?:mouse|mask\s+)?(?:to\s+)?(?:the\s+)?center\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_center", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(?:to\s+)?(?:the\s+)?center\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_center", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # Corner positions - REQUIRE "move" or "mouse" to be present (no accidental matches)
            # Handles STT errors (e.g., "mass" for "mouse", "miles" for "mouse")
            # "move mouse top right", "move to top right", "move the mouse to the top right"
            # EXPLICIT: Must have "move" or "mouse/mass/miles/mice/moss" 
            # FIXED: Made space after mouse optional and separate from the alternation
            (re.compile(r'\bmove\s+(the\s+)?(mouse|mask|mass|miles|mice|moss)(\s+)?(to\s+)?(the\s+)?top\s+right\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_top_right", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(to\s+)?(the\s+)?top\s+right\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_top_right", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse top left", "move to top left", "move the mouse to the top left"
            (re.compile(r'\bmove\s+(the\s+)?(mouse|mask|mass|miles|mice|moss)(\s+)?(to\s+)?(the\s+)?top\s+left\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_top_left", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(to\s+)?(the\s+)?top\s+left\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_top_left", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse bottom right", "move to bottom right", "move the mouse to the bottom right"
            (re.compile(r'\bmove\s+(the\s+)?(mouse|mask|mass|miles|mice|moss)(\s+)?(to\s+)?(the\s+)?bottom\s+right\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_bottom_right", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(to\s+)?(the\s+)?bottom\s+right\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_bottom_right", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse bottom left", "move to bottom left", "move the mouse to the bottom left"
            (re.compile(r'\bmove\s+(the\s+)?(mouse|mask|mass|miles|mice|moss)(\s+)?(to\s+)?(the\s+)?bottom\s+left\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_bottom_left", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(to\s+)?(the\s+)?bottom\s+left\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_bottom_left", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # Center-aligned positions - REQUIRE "move" or "mouse"
            # "move mouse top center", "move mouse to top center"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?top\s+center\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_top_center", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse bottom center", "move mouse to bottom center"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?bottom\s+center\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_bottom_center", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse left center", "move mouse to left center"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?left\s+center\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_left_center", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse right center", "move mouse to right center"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?right\s+center\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_right_center", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse middle left", "move mouse to middle left" - REQUIRE "move" or "mouse"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?middle\s+left\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_middle_left", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(to\s+)?(the\s+)?middle\s+left\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_middle_left", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse middle right", "move mouse to middle right" - REQUIRE "move" or "mouse"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?middle\s+right\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_middle_right", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(to\s+)?(the\s+)?middle\s+right\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_middle_right", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse top", "move mouse to top" - REQUIRE "move" or "mouse"
            # Use negative lookahead (?!\s+(left|right|center)) to avoid matching "top left", "top right"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?top(?!\s+(left|right|center))\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_top", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(to\s+)?(the\s+)?top(?!\s+(left|right|center))\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_top", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse bottom", "move mouse to bottom" - REQUIRE "move" or "mouse"
            # Use negative lookahead (?!\s+(left|right|center)) to avoid matching "bottom left", "bottom right"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?bottom(?!\s+(left|right|center))\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_bottom", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(to\s+)?(the\s+)?bottom(?!\s+(left|right|center))\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_bottom", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse far right", "move mouse to far right" - REQUIRE "move" or "mouse"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?far\s+right\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_right", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse far left", "move mouse to far left" - REQUIRE "move" or "mouse"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?far\s+left\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move_left", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse left edge", "move mouse to right edge" - REQUIRE "move" or "mouse"
            (re.compile(r'\bmove\s+(the\s+)?(mouse\s+)?(to\s+)?(the\s+)?(left|right)\s*(edge|side)\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse up", "move mouse down" - REQUIRE "move" or "mouse"
            # Use negative lookahead to prevent matching when followed by other direction words
            (re.compile(r'\bmove\s+(the\s+)?mouse\s+(up|down)(?!\s+(left|right|center|top|bottom|middle))\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(up|down)(?!\s+(left|right|center|top|bottom|middle))\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "move mouse left", "move mouse right" - REQUIRE "move" or "mouse", NOT followed by "click" or other direction words
            # CRITICAL: This must come LAST (after all corner patterns) to avoid matching "left" in "top left" or "right" in "top right"
            # The negative lookahead only checks what comes AFTER, not BEFORE, so we rely on pattern order and a safeguard check
            (re.compile(r'\bmove\s+(the\s+)?mouse\s+(to\s+)?(the\s+)?(left|right)(?!\s*(click|center|edge|side|top|bottom|middle))\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            (re.compile(r'\b(mouse|mask|mass|miles|mice|moss)\s+(to\s+)?(the\s+)?(left|right)(?!\s*(click|center|edge|side|top|bottom|middle))\b', re.IGNORECASE), 
             ActionType.MOUSE_MOVEMENT, "mouse_movement", {"action": "move", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === KEYBOARD SHORTCUTS ===
            # "new tab", "open new tab"
            (re.compile(r'\b(new|open\s+new)\s+tab\.?$', re.IGNORECASE), 
             ActionType.KEYBOARD_SHORTCUT, "keyboard_shortcut", {"shortcut": "new_tab", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "close tab", "close window"
            (re.compile(r'\bclose\s+(tab|window)\.?$', re.IGNORECASE), 
             ActionType.KEYBOARD_SHORTCUT, "keyboard_shortcut", {"shortcut": "close", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "next tab", "previous tab"
            (re.compile(r'\b(next|previous)\s+tab\.?$', re.IGNORECASE), 
             ActionType.KEYBOARD_SHORTCUT, "keyboard_shortcut", {"shortcut": "__TAB_MATCH__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "open spotlight", "spotlight"
            (re.compile(r'\b(open\s+)?spotlight\.?$', re.IGNORECASE), 
             ActionType.KEYBOARD_SHORTCUT, "keyboard_shortcut", {"shortcut": "spotlight", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "swap window", "swap windows", "next window", "other window", "cycle window", "switch window"
            (re.compile(r'\b(swap|next|other|cycle|switch)\s+window(s)?\.?$', re.IGNORECASE), 
             ActionType.KEYBOARD_SHORTCUT, "keyboard_shortcut", {"shortcut": "swap_window", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === MEDIA CONTROL ===
            # "play", "pause", "stop"
            (re.compile(r'^(play|pause|stop)\.?$', re.IGNORECASE), 
             ActionType.MEDIA_CONTROL, "media_control", {"action": "__MATCH_GROUP__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "volume up", "volume down", "mute"
            (re.compile(r'\b(volume\s+(up|down)|mute)\.?$', re.IGNORECASE), 
             ActionType.MEDIA_CONTROL, "media_control", {"action": "__VOLUME_MATCH__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "next track", "previous track"
            (re.compile(r'\b(next|previous)\s+track\.?$', re.IGNORECASE), 
             ActionType.MEDIA_CONTROL, "media_control", {"action": "__MATCH_GROUP__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "refresh", "reload"
            (re.compile(r'^(refresh|reload)\.?$', re.IGNORECASE), 
             ActionType.MEDIA_CONTROL, "media_control", {"action": "refresh", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === CARET MOVEMENT ===
            # "up", "down", "left", "right" (arrow keys)
            (re.compile(r'^(up|down|left|right)\.?$', re.IGNORECASE), 
             ActionType.CARET_MOVEMENT, "caret_movement", {"direction": "__MATCH_GROUP__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "page up", "page down"
            (re.compile(r'^page\s+(up|down)\.?$', re.IGNORECASE), 
             ActionType.CARET_MOVEMENT, "caret_movement", {"direction": "__PAGE_MATCH__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "home", "end"
            (re.compile(r'^(home|end)\.?$', re.IGNORECASE), 
             ActionType.CARET_MOVEMENT, "caret_movement", {"direction": "__MATCH_GROUP__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "delete forward"
            (re.compile(r'^delete\s+forward\.?$', re.IGNORECASE), 
             ActionType.CARET_MOVEMENT, "caret_movement", {"direction": "delete_forward", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === SPECIAL KEYS ===
            # "space", "spacebar", "space bar"
            (re.compile(r'^(space\s*bar|spacebar|space)\.?$', re.IGNORECASE), 
             ActionType.SPECIAL_KEY, "special_key", {"key": "space", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "enter", "enter this", "press enter"
            (re.compile(r'^(press\s+)?(enter\s+this|enter)\.?$', re.IGNORECASE), 
             ActionType.SPECIAL_KEY, "special_key", {"key": "enter", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "tab", "press tab"
            (re.compile(r'^(press\s+)?tab\.?$', re.IGNORECASE), 
             ActionType.SPECIAL_KEY, "special_key", {"key": "tab", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "escape", "esc", "cancel", "press escape"
            (re.compile(r'^(press\s+)?(escape|esc|cancel)\.?$', re.IGNORECASE), 
             ActionType.SPECIAL_KEY, "special_key", {"key": "escape", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "alt", "press alt"
            (re.compile(r'^(press\s+)?alt\.?$', re.IGNORECASE), 
             ActionType.SPECIAL_KEY, "special_key", {"key": "alt", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "control", "ctrl", "press control"
            (re.compile(r'^(press\s+)?(control|ctrl)\.?$', re.IGNORECASE), 
             ActionType.SPECIAL_KEY, "special_key", {"key": "control", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "command", "cmd", "press command"
            (re.compile(r'^(press\s+)?(command|cmd)\.?$', re.IGNORECASE), 
             ActionType.SPECIAL_KEY, "special_key", {"key": "command", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === FUNCTION KEYS ===
            # "press F1" through "press F12"
            (re.compile(r'^press\s+f(\d{1,2})\.?$', re.IGNORECASE), 
             ActionType.FUNCTION_KEY, "function_key", {"key": "__FKEY_MATCH__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "F1" through "F12" (standalone)
            (re.compile(r'^f(\d{1,2})\.?$', re.IGNORECASE), 
             ActionType.FUNCTION_KEY, "function_key", {"key": "__FKEY_MATCH__", "text": "__ORIGINAL_TEXT__"}, False, "done"),
            
            # === SAVE AUDIO ===
            # "save clipboard to audio", "save clipboard as audio"
            (re.compile(r'\bsave\s+(the\s+)?clipboard\s+(to|as)\s+audio\.?$', re.IGNORECASE), 
             ActionType.SAVE_AUDIO, "save_audio", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
            # "save this as audio", "save as audio"
            (re.compile(r'\bsave\s+(this\s+)?as\s+audio\.?$', re.IGNORECASE), 
             ActionType.SAVE_AUDIO, "save_audio", {"text": "__ORIGINAL_TEXT__"}, False, "done"),
        ]
        
        # Conversational indicators (should NOT trigger tools)
        self.conversational_patterns = [
            re.compile(r'\b(what|how|why|when|where|who)\b.*\?', re.IGNORECASE),
            re.compile(r'^(tell me|can you|could you|would you)\b', re.IGNORECASE),
            re.compile(r'\b(story|joke|explain\s+how|explain\s+why)\b', re.IGNORECASE),
            re.compile(r'\b(think|opinion|believe|feel)\b.*\b(about|of)\b', re.IGNORECASE),
            re.compile(r'^(what|how|why|who)\s+(is|are|was|were|do|does|did)\b', re.IGNORECASE),
        ]
        
        # Context reference patterns (needs chat history)
        self.context_reference_patterns = [
            re.compile(r'\b(that|it|this)\b.*\b(list|content|text|result|answer)\b', re.IGNORECASE),
            re.compile(r'\b(what do you think|what about|how about)\b', re.IGNORECASE),
            re.compile(r'^(and|also|now|then|next)\b', re.IGNORECASE),
        ]
    
    def detect(self, text: str, has_recent_clipboard_context: bool = False) -> DetectedAction:
        """
        Detect action from user input text.
        
        Args:
            text: User's spoken/typed input
            has_recent_clipboard_context: Whether clipboard content is in recent chat history
            
        Returns:
            DetectedAction with tool info or UNKNOWN if LLM needed
        """
        text = text.strip()
        text_lower = text.lower()
        
        logger.debug(f"FastActionDetector: Analyzing '{text}'")
        
        # COMPOUND SENTENCE CHECK: If the text contains multiple sentences/clauses
        # and mixes conversational content with commands, route to LLM so it can
        # handle both parts (e.g. "What's your favorite color? And also take a screenshot")
        if self._is_compound_with_conversation(text_lower):
            logger.info(f"FastActionDetector: Compound sentence with conversational content, routing to LLM: '{text[:80]}'")
            return DetectedAction(
                action_type=ActionType.UNKNOWN,
                tool_name="",
                tool_args={},
                needs_copy_first=False,
                response_type="llm_response",
                confidence=0.0,
                original_text=text
            )
        
        # CRITICAL: Check for specific commands FIRST before conversational/context checks
        # This ensures these commands are always detected, even if they contain conversational words
        cursor_ticket_pattern = re.compile(r'\b(can\s+you\s+)?tell\s+cursor\b', re.IGNORECASE)
        # Analysis patterns: "see my screen", "look at my screen", "analyze the screen"
        screenshot_analysis_pattern = re.compile(r'\b(can\s+you\s+)?(see|look\s+at|analyze|check|examine)\b.*?(my\s+)?(screen|display|monitor|screens)\b', re.IGNORECASE)
        # Direct send patterns: "give me a screenshot", "send me a screenshot", "screenshot of screen 1"
        screenshot_direct_pattern = re.compile(r'\b(give|send|show)\s+(me\s+)?a?\s*screenshot\b|\bscreenshot\s+(of\s+)?screen\s+\d+\b|\btake\s+a\s+(screenshot|picture)\b', re.IGNORECASE)
        
        # Mouse-to-element patterns: "move mouse to the end of...", "move cursor to the X button", etc.
        # These need vision (screenshot_analyzer) and must skip conversational/context checks
        mouse_to_element_pattern = re.compile(r'\b(move|put)\s+(?:(?:the|my)\s+)?(?:mouse|mask|cursor|pointer)\s+(?:to|over|towards)\b', re.IGNORECASE)
        
        if cursor_ticket_pattern.search(text):
            # This is a cursor ticket command - skip conversational/context checks and go straight to pattern matching
            logger.debug(f"FastActionDetector: Detected 'tell cursor' command, skipping conversational/context checks")
        elif screenshot_direct_pattern.search(text):
            # Direct screenshot request - skip conversational checks and go straight to pattern matching
            logger.info(f"FastActionDetector: Detected DIRECT screenshot command: '{text}'")
        elif screenshot_analysis_pattern.search(text):
            # This is a screenshot analysis command - skip conversational/context checks and go straight to pattern matching
            logger.debug(f"FastActionDetector: Detected screenshot analysis command, skipping conversational/context checks")
        elif mouse_to_element_pattern.search(text):
            # Mouse movement to a specific element/position - skip conversational/context checks
            logger.debug(f"FastActionDetector: Detected mouse-to-element command, skipping conversational/context checks")
        else:
            # Check for conversational patterns first
            if self._is_conversational(text):
                logger.info(f"FastActionDetector: Detected conversational query: '{text}'")
                return DetectedAction(
                    action_type=ActionType.CONVERSATIONAL,
                    tool_name="",
                    tool_args={},
                    needs_copy_first=False,
                    response_type="llm_response",
                    confidence=0.8,
                    original_text=text
                )
            
            # Check for context references that need LLM
            if self._needs_context(text) and not has_recent_clipboard_context:
                logger.info(f"FastActionDetector: Needs context, routing to LLM: '{text}'")
                return DetectedAction(
                    action_type=ActionType.UNKNOWN,
                    tool_name="",
                    tool_args={},
                    needs_copy_first=False,
                    response_type="llm_response",
                    confidence=0.5,
                    original_text=text
                )
        
        # Try to match action patterns
        for pattern, action_type, tool_name, tool_args, needs_copy, response_type in self.action_patterns:
            match = pattern.search(text)
            if match:
                # CRITICAL SAFEGUARD: If this is a simple "left" or "right" mouse movement pattern,
                # and "top" or "bottom" appear in the text, skip it (corner patterns should have matched first)
                # This prevents "move mouse top left" from matching as "move mouse left"
                if tool_name == "mouse_movement" and tool_args.get("action") == "move":
                    # Check if "left" or "right" appears in the matched text and "top" or "bottom" appear in the full text
                    matched_text = match.group(0).lower()
                    text_lower = text.lower()
                    
                    # If the match contains "left" or "right" AND the full text contains "top" or "bottom", skip it
                    if (("left" in matched_text or "right" in matched_text) and 
                        ("top" in text_lower or "bottom" in text_lower)):
                        logger.debug(f"FastAction: Skipping simple left/right match - 'top' or 'bottom' found in text '{text[:50]}...' (corner pattern should match instead)")
                        continue  # Skip this match, continue to next pattern
                
                # Copy and customize tool_args
                final_args = tool_args.copy()
                
                # Replace __ORIGINAL_TEXT__ placeholder with actual text
                # This is needed for tools like rework_clipboard and summarize_clipboard
                # that use the text to determine behavior (e.g., "this" vs "clipboard")
                for key, value in final_args.items():
                    if value == "__ORIGINAL_TEXT__":
                        final_args[key] = text
                    elif value == "__MATCH_GROUP__":
                        # Extract matched group - use first group or full match
                        if match.groups():
                            final_args[key] = match.group(1).lower()
                        else:
                            final_args[key] = match.group(0).lower()
                    elif value == "__MATCH_GROUP_LAST__":
                        # Extract matched group - use last group (for patterns like "run action X" where we want X)
                        if match.groups():
                            # Use the last group (highest index)
                            final_args[key] = match.group(len(match.groups())).lower()
                        else:
                            final_args[key] = match.group(0).lower()
                    elif value == "__SCROLL_MATCH__":
                        # Map "up" -> "scroll_up", "down" -> "scroll_down"
                        if match.groups():
                            direction = match.group(1).lower()
                            final_args[key] = f"scroll_{direction}"
                    elif value == "__TAB_MATCH__":
                        # Map "next" -> "next_tab", "previous" -> "previous_tab"
                        if match.groups():
                            direction = match.group(1).lower()
                            final_args[key] = f"{direction}_tab"
                    elif value == "__VOLUME_MATCH__":
                        # Map "volume up" -> "volume_up", "volume down" -> "volume_down", "mute" -> "mute"
                        if match.groups():
                            vol_match = match.group(1).lower()
                            if "up" in vol_match:
                                final_args[key] = "volume_up"
                            elif "down" in vol_match:
                                final_args[key] = "volume_down"
                            elif "mute" in vol_match:
                                final_args[key] = "mute"
                    elif value == "__PAGE_MATCH__":
                        # Map "up" -> "page_up", "down" -> "page_down"
                        if match.groups():
                            direction = match.group(1).lower()
                            final_args[key] = f"page_{direction}"
                    elif value == "__FKEY_MATCH__":
                        # Map "1" -> "F1", "12" -> "F12"
                        if match.groups():
                            fnum = match.group(1)
                            final_args[key] = f"F{fnum}"
                    elif value == "__CLEAR_DELETE_MATCH__":
                        # Map "clear" -> "clear_line", "delete" -> "delete_line"
                        if match.groups():
                            action = match.group(1).lower()
                            final_args[key] = f"{action}_line"
                    elif value == "__EXTRACT_PATH__":
                        # Extract folder path from text (e.g., "my downloads", "my desktop")
                        # Look for folder references in the text - check multiple patterns
                        folder_patterns = [
                            # "take a look at what's in my downloads", "look at what's in downloads"
                            (r'\b(what\'?s?\s+in|what\s+is\s+in)\s+(my\s+)?(desktop|documents|downloads|pictures|music|videos|folder|directory)\b', 3),
                            # "my downloads", "my desktop"
                            (r'\bmy\s+(desktop|documents|downloads|pictures|music|videos)\b', 1),
                            # "downloads folder", "desktop folder"
                            (r'\b(desktop|documents|downloads|pictures|music|videos)\s+folder\b', 1),
                            # "files in my downloads", "files in downloads"
                            (r'\bfiles?\s+(in|on)\s+(my\s+)?(desktop|documents|downloads|pictures|music|videos|folder|directory)\b', 3),
                        ]
                        extracted_path = None
                        for pattern, group_idx in folder_patterns:
                            folder_match = re.search(pattern, text, re.IGNORECASE)
                            if folder_match and folder_match.groups():
                                folder_name = folder_match.group(group_idx) if group_idx <= len(folder_match.groups()) else folder_match.group(-1)
                                # Check if "my" appears before the folder name in the text
                                folder_pos = text.lower().find(folder_name.lower())
                                if folder_pos > 0:
                                    before_text = text.lower()[:folder_pos]
                                    if 'my' in before_text:
                                        extracted_path = f"my {folder_name}"
                                    else:
                                        extracted_path = folder_name
                                else:
                                    extracted_path = folder_name
                                break
                        
                        # If no folder found, try to extract from the full match context
                        if not extracted_path:
                            # Look for common folder names
                            common_folders = ['desktop', 'documents', 'downloads', 'pictures', 'music', 'videos']
                            for folder in common_folders:
                                if folder in text.lower():
                                    # Check if "my" precedes it
                                    folder_idx = text.lower().find(folder)
                                    if folder_idx > 0:
                                        before_text = text.lower()[:folder_idx]
                                        if 'my' in before_text:
                                            extracted_path = f"my {folder}"
                                        else:
                                            extracted_path = folder
                                    else:
                                        extracted_path = folder
                                    break
                        
                        # Default to "Downloads" if nothing found
                        final_args[key] = extracted_path or "my Downloads"
                    elif value == "__EXTRACT_TARGET_FORMAT__":
                        # Extract target format from text (e.g., "mp3", "wav", "flac", "text")
                        # Look for format after "to" in conversion patterns
                        format_patterns = [
                            r'\bto\s+(mp3|wav|flac|m4a|ogg|opus|aac|wma|text)\b',
                            r'\bconvert\s+.*?\s+to\s+(mp3|wav|flac|m4a|ogg|opus|aac|wma|text)\b',
                        ]
                        extracted_format = None
                        for pattern in format_patterns:
                            format_match = re.search(pattern, text, re.IGNORECASE)
                            if format_match and format_match.groups():
                                extracted_format = format_match.group(1).lower()
                                break
                        
                        # Default to "mp3" if nothing found (most common conversion)
                        final_args[key] = extracted_format or "mp3"
                    elif value == "__EXTRACT_DOC_FORMAT__":
                        # Extract document format: pdf, docx, word → docx
                        doc_format_match = re.search(r'\b(pdf|docx|word)\b', text, re.IGNORECASE)
                        fmt = doc_format_match.group(1).lower() if doc_format_match else "pdf"
                        if fmt == "word":
                            fmt = "docx"
                        final_args[key] = fmt
                
                logger.info(f"FastActionDetector: MATCHED '{text}' -> {action_type.value} (tool: {tool_name}, copy_first: {needs_copy})")
                return DetectedAction(
                    action_type=action_type,
                    tool_name=tool_name,
                    tool_args=final_args,
                    needs_copy_first=needs_copy,
                    response_type=response_type,
                    confidence=0.95,
                    original_text=text
                )
        
        # No pattern matched - let LLM decide
        logger.debug(f"FastActionDetector: No pattern matched for '{text}', routing to LLM")
        return DetectedAction(
            action_type=ActionType.UNKNOWN,
            tool_name="",
            tool_args={},
            needs_copy_first=False,
            response_type="llm_response",
            confidence=0.0,
            original_text=text
        )
    
    def _is_compound_with_conversation(self, text_lower: str) -> bool:
        """Detect compound sentences that mix conversational questions with action commands.
        
        E.g. "What's your favorite color? And also take a screenshot and tell me what you see"
        These should go to the LLM which can handle both parts, not be fast-actioned.
        """
        # Split on sentence boundaries (., ?, !) and conjunctions (and also, but also, also)
        parts = re.split(r'[.?!]+\s*|\band\s+also\b|\bbut\s+also\b|\balso\s+', text_lower)
        parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 3]
        
        if len(parts) < 2:
            return False
        
        # Check if at least one part looks conversational (question words without action verbs)
        question_indicators = re.compile(
            r'\b(what\'?s?|who\'?s?|where|when|why|how|is\s+there|are\s+there|do\s+you|can\s+you\s+tell\s+me|tell\s+me\s+about)\b'
        )
        action_indicators = re.compile(
            r'\b(screenshot|screen|take\s+a|look\s+at|analyze|copy|paste|move\s+mouse|click|open|run|play|execute)\b'
        )
        
        has_conversational = False
        has_action = False
        for part in parts:
            if question_indicators.search(part) and not action_indicators.search(part):
                has_conversational = True
            if action_indicators.search(part):
                has_action = True
        
        return has_conversational and has_action
    
    def _is_conversational(self, text: str) -> bool:
        """Check if text is a conversational query (not an action command)."""
        # Very short inputs are likely commands
        if len(text.split()) <= 2:
            return False
        
        # Exclude action commands from being marked as conversational
        action_command_patterns = [
            r'\b(run|play|execute)\b.*\baction\b',
            r'\baction\b.*\b(run|play|execute)\b',
            r'\b(create|make|save|new)\b.*\baction\b',
            r'\b(start|begin|stop|end|finish)\b.*\brecording\b',
            # Screenshot commands
            r'\b(can\s+you\s+)?(see|look\s+at|analyze|check|examine)\b.*?(my\s+)?(screen|display|monitor|screens)\b',
            r'\b(what\s+do\s+you\s+see|what\'?s?\s+on\s+(the\s+)?screen|describe\s+(the\s+)?screen)\b',
        ]
        for pattern in action_command_patterns:
            if re.compile(pattern, re.IGNORECASE).search(text):
                return False  # This is an action command, not conversational
            
        # Check for question indicators without action verbs
        for pattern in self.conversational_patterns:
            if pattern.search(text):
                # But exclude if it also contains action verbs / tool-related words.
                # This list must be comprehensive — a missing verb here causes
                # the fast action detector to swallow tool commands as "conversational".
                action_verbs = [
                    # UI / input
                    'copy', 'cut', 'paste', 'read', 'explain', 'elaborate',
                    'summarize', 'rewrite', 'reword', 'create', 'clear', 'exit',
                    'run', 'play', 'execute', 'start', 'stop', 'recording',
                    'see', 'look', 'analyze', 'check', 'examine', 'screen', 'display', 'monitor',
                    'move', 'click', 'hover', 'drag', 'scroll', 'mouse', 'cursor', 'mask',
                    # File / document operations
                    'convert', 'delete', 'remove', 'rename', 'open', 'save', 'close',
                    'list files', 'show files', 'file', 'folder', 'directory',
                    'pdf', 'docx', 'document',
                    # Web / search
                    'search', 'fetch', 'download', 'upload', 'send', 'browse',
                    # Git
                    'git', 'commit', 'push', 'pull',
                    # Code
                    'code', 'script', 'index',
                    # Media
                    'transcribe', 'record', 'screenshot', 'capture',
                    # Misc tools
                    'snippet', 'telegram', 'generate', 'export', 'import',
                    'automate', 'step runner', 'project', 'kanban', 'ticket',
                ]
                if not any(verb in text.lower() for verb in action_verbs):
                    return True
        
        return False
    
    def _needs_context(self, text: str) -> bool:
        """Check if text references previous context."""
        for pattern in self.context_reference_patterns:
            if pattern.search(text):
                return True
        return False


# Singleton instance for reuse
_detector_instance: Optional[FastActionDetector] = None


def get_detector() -> FastActionDetector:
    """Get or create the singleton detector instance."""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = FastActionDetector()
    return _detector_instance


def detect_fast_action(text: str, has_recent_clipboard_context: bool = False) -> DetectedAction:
    """
    Convenience function to detect action from text.
    
    Args:
        text: User input text
        has_recent_clipboard_context: Whether clipboard is in recent history
        
    Returns:
        DetectedAction result
    """
    return get_detector().detect(text, has_recent_clipboard_context)

