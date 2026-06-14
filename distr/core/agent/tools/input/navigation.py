"""
Navigation and Window Management Tools for LangChain.

These tools handle natural language requests for window management,
application opening, and system navigation.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field, BaseModel
import logging
import os
import re
import platform
import subprocess
from distr.core.agent.tools.base import get_platform_modifier_key

# PyAutoGUI - import at module level and disable FAILSAFE
try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

logger = logging.getLogger(__name__)

KNOWN_FOLDERS = {
    "desktop": {
        "path": os.path.expanduser("~/Desktop"),
        "spoken_label": "desktop folder",
    },
    "downloads": {
        "path": os.path.expanduser("~/Downloads"),
        "spoken_label": "downloads folder",
    },
    "documents": {
        "path": os.path.expanduser("~/Documents"),
        "spoken_label": "documents folder",
    },
    "document": {
        "path": os.path.expanduser("~/Documents"),
        "spoken_label": "documents folder",
    },
    "home": {
        "path": os.path.expanduser("~"),
        "spoken_label": "home folder",
    },
}


class SmartOpenInput(BaseModel):
    """Input schema for smart open tool."""
    target: str = Field(description="The URL, application name, or file to open (e.g., 'http://127.0.0.1:8000', 'Chrome', 'report.pdf')")
    text: Optional[str] = Field(default=None, description="Optional full text of user's request for better context")


class SmartOpenTool(BaseTool):
    """Intelligently opens URLs, applications, or files based on the target."""
    
    name: str = "smart_open"
    description: str = """🎯 SMART OPEN - Intelligently opens URLs, applications, or files.
    
    CRITICAL: Use this tool when the user wants to open ANYTHING - URL, application, or file.
    This tool automatically detects what type of target it is and handles it appropriately.
    
    DETECTION LOGIC (automatic):
    1. If target contains "://" or starts with "http", "https", "file://" → Open as URL in browser
    2. If target looks like a local URL (e.g., "localhost:8000", "127.0.0.1:3000") → Open as URL
    3. If target has a file extension (e.g., ".pdf", ".txt", ".jpg") → Search and open as file
    4. Otherwise → Open as application
    
    Examples:
    - "open http://127.0.0.1:8000" → Opens URL in default browser
    - "open https://google.com" → Opens URL in default browser
    - "open localhost:3000" → Opens URL in default browser
    - "open Chrome" → Opens Chrome application
    - "open Safari" → Opens Safari application
    - "open report.pdf" → Searches for and opens the PDF file
    - "open the file I just created" → Searches for and opens the file
    
    The tool handles everything intelligently - you don't need to choose between tools.
    CALL THIS TOOL immediately when user asks to open something - never explain, just execute."""
    
    args_schema: type[BaseModel] = SmartOpenInput
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
    
    def _is_url(self, target: str) -> bool:
        """Detect if target is a URL."""
        target_lower = target.lower().strip()
        
        # Explicit URL protocols
        if "://" in target_lower:
            return True
        
        # HTTP/HTTPS URLs
        if target_lower.startswith(("http://", "https://", "file://", "ftp://")):
            return True
        
        # Local development URLs
        if target_lower.startswith(("localhost:", "127.0.0.1:", "0.0.0.0:", "192.168.")):
            return True
        
        # Check for domain-like patterns (contains . and looks like a domain)
        if "." in target_lower and "/" not in target_lower.split(".")[0]:
            # e.g., "google.com", "example.org"
            parts = target_lower.split("/")[0].split(".")
            if len(parts) >= 2 and all(part.replace("-", "").isalnum() for part in parts):
                return True
        
        return False
    
    def _is_file(self, target: str) -> bool:
        """Detect if target looks like a file."""
        # Check for file extensions
        common_extensions = [
            '.pdf', '.txt', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp',
            '.mp4', '.mov', '.avi', '.mkv', '.mp3', '.wav', '.flac',
            '.zip', '.rar', '.tar', '.gz', '.7z',
            '.py', '.js', '.html', '.css', '.json', '.xml', '.md', '.csv',
            '.dmg', '.pkg', '.app', '.exe', '.msi'
        ]
        
        target_lower = target.lower()
        return any(target_lower.endswith(ext) for ext in common_extensions)
    
    def _open_url(self, url: str) -> str:
        """Open URL in default browser."""
        try:
            # Ensure URL has protocol
            if not url.startswith(("http://", "https://", "file://")):
                url = "http://" + url
            
            system = platform.system()
            if system == 'Darwin':  # macOS
                subprocess.run(['open', url], check=True)
            elif system == 'Windows':
                os.startfile(url)
            else:  # Linux
                subprocess.run(['xdg-open', url], check=True)
            
            logger.info(f"SmartOpenTool: Opened URL: {url}")
            return f"Opened URL: {url}"
        except Exception as e:
            logger.error(f"SmartOpenTool: Error opening URL: {e}", exc_info=True)
            return f"Error opening URL: {str(e)}"
    
    def _open_file(self, target: str) -> str:
        """Find and open a file."""
        try:
            # Import the OpenFileTool
            from distr.core.agent.tools.files.open_file import OpenFileTool
            
            # Create OpenFileTool instance
            open_file_tool = OpenFileTool()
            
            # Call the tool to find and open the file
            result = open_file_tool._run(file_name=target)
            logger.info(f"SmartOpenTool: File open result: {result}")
            return result
        except Exception as e:
            logger.error(f"SmartOpenTool: Error opening file: {e}", exc_info=True)
            return f"Error opening file: {str(e)}"
    
    def _open_application(self, app_name: str, text: str) -> str:
        """Open an application."""
        try:
            from distr.core.actions.desktop import open_app

            requested_name = (text or app_name or "").strip()
            if not requested_name:
                return "Error opening application: missing application name"

            open_app(requested_name.lower())
            
            logger.info(f"SmartOpenTool: Opened application: {app_name}")
            return f"Opened application: {app_name}"
        except Exception as e:
            logger.error(f"SmartOpenTool: Error opening application: {e}", exc_info=True)
            return f"Error opening application: {str(e)}"

    def _open_known_folder(self, target: str) -> Optional[str]:
        """Open common user folders directly when explicitly requested."""
        target_lower = (target or "").lower()
        if not target_lower:
            return None

        for folder_key, folder_meta in KNOWN_FOLDERS.items():
            folder_path = folder_meta["path"]
            spoken_label = folder_meta["spoken_label"]
            explicit_folder_request = (
                (folder_key in target_lower and "folder" in target_lower)
                or target_lower in {
                folder_key,
                f"my {folder_key}",
                }
            )
            if explicit_folder_request:
                if os.path.isdir(folder_path):
                    system = platform.system()
                    if system == "Darwin":
                        subprocess.run(["open", folder_path], check=True)
                    elif system == "Windows":
                        os.startfile(folder_path)
                    else:
                        subprocess.run(["xdg-open", folder_path], check=True)
                    return f"Opened your {spoken_label}."
        return None
    
    def _run(self, target: str = "", text: str = "", **kwargs) -> str:
        """Execute smart open - automatically detects and handles URLs, files, or applications."""
        try:
            target = (target or "").strip()
            text = (text or "").strip()
            if not target:
                return "Error: No target provided. Please specify a URL, application, or file to open."
            
            logger.info(f"SmartOpenTool: Processing target='{target}', text='{text}'")

            known_folder_result = self._open_known_folder(target)
            if known_folder_result:
                logger.info("SmartOpenTool: Opened known folder for target='%s'", target)
                return known_folder_result
            
            # 1. Check if it's a URL
            if self._is_url(target):
                logger.info(f"SmartOpenTool: Detected as URL")
                return self._open_url(target)
            
            # 2. Check if it's a file
            if self._is_file(target):
                logger.info(f"SmartOpenTool: Detected as file")
                return self._open_file(target)
            
            # 3. Try as application first
            logger.info(f"SmartOpenTool: Attempting as application")
            result = self._open_application(target, text or target)
            
            # If application open was successful, return
            if "Error" not in result or "Successfully" in result:
                return result
            
            # 4. If application failed, try as file (maybe it's a file without extension)
            logger.info(f"SmartOpenTool: Application failed, trying as file")
            file_result = self._open_file(target)
            
            # If file open was successful, return that
            if "Error" not in file_result or "Opened" in file_result:
                return file_result
            
            # 5. Both failed - return the original application error
            return result
            
        except Exception as e:
            logger.error(f"Error in SmartOpenTool: {e}", exc_info=True)
            return f"Error: {str(e)}"
    
    async def _arun(self, target: str = "", text: str = "", **kwargs) -> str:
        """Async execution."""
        return self._run(target=target, text=text, **kwargs)


class OpenWindowTool(BaseTool):
    """Tool for opening or focusing on applications/windows based on natural language."""
    
    name: str = "open_window"
    description: str = """🎯 Opens or focuses on an application or window.
    
    CRITICAL: Use this tool when the user wants to open an application or focus on a window.
    
    Examples: 
    - "open Chrome", "open Safari", "open Firefox"
    - "open Cursor", "open VS Code", "open my code editor"
    - "focus on Safari", "show me Firefox", "launch Terminal"
    - "open Spotify", "open Music", "open Notes"
    
    The tool will automatically:
    - Search shortcut names in config
    - Find running applications
    - Search installed applications
    - Use fuzzy matching to find the best match
    
    CALL THIS TOOL immediately when user asks to open an application - never explain, just execute."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
    
    def _run(self, app_name: str = "", text: str = "", **kwargs) -> str:
        """Execute opening/focusing on a window."""
        try:
            from distr.core.actions.desktop import open_app
            
            # If app_name not provided, try to extract from text
            if not app_name and text:
                app_name = self._extract_app_name(text)
            elif app_name and not text:
                text = app_name
            
            if not app_name:
                return "Error: No application name provided. Please specify which application to open."
            
            open_app(text.lower() if text else app_name.lower())
            
            return f"Successfully opened/focused on: {app_name}"
            
        except Exception as e:
            logger.error(f"Error in OpenWindowTool: {e}", exc_info=True)
            return f"Error opening window: {str(e)}"
    
    async def _arun(self, app_name: str = "", text: str = "", **kwargs) -> str:
        """Async execution."""
        return self._run(app_name=app_name, text=text)
    
    def _extract_app_name(self, text: str) -> str:
        """Extract application name from natural language text with improved parsing."""
        
        # Remove common prefixes and articles
        prefixes = [
            "open", "focus on", "focus", "launch", "start", "show", "show me",
            "bring up", "bring", "run", "execute", "start up"
        ]
        articles = ["the", "a", "an", "my", "this", "that"]
        
        text_lower = text.lower().strip()
        
        # Remove prefixes
        for prefix in sorted(prefixes, key=len, reverse=True):  # Longer prefixes first
            if text_lower.startswith(prefix):
                text_lower = text_lower[len(prefix):].strip()
                break
        
        # Remove leading articles
        for article in articles:
            if text_lower.startswith(article + " "):
                text_lower = text_lower[len(article):].strip()
        
        # Remove trailing phrases like "for me", "please", etc.
        trailing_phrases = [" for me", " please", " now", " app", " application"]
        for phrase in trailing_phrases:
            if text_lower.endswith(phrase):
                text_lower = text_lower[:-len(phrase)].strip()
        
        # Clean up extra spaces
        text_lower = re.sub(r'\s+', ' ', text_lower).strip()
        
        return text_lower


class OpenFileMenuTool(BaseTool):
    """Tool for opening the file menu."""
    
    name: str = "open_file_menu"
    description: str = "Opens the file menu in the current application (Cmd+Shift+F)."
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
    
    def _run(self, **kwargs) -> str:
        """Execute opening file menu."""
        try:
            from distr.core.actions.desktop import open_file_menu
            
            open_file_menu()
            return "File menu opened"
            
        except Exception as e:
            logger.error(f"Error in OpenFileMenuTool: {e}", exc_info=True)
            return f"Error opening file menu: {str(e)}"
    
    async def _arun(self, **kwargs) -> str:  # Already accepts **kwargs
        return self._run(**kwargs)


class OracleControlTool(BaseTool):
    """Tool for controlling the oracle/globe interface visibility."""
    
    name: str = "oracle_control"
    description: str = """🎯 Controls the oracle/globe visual interface visibility.
    
    CRITICAL: Use this tool when the user wants to hide or show the oracle/globe.
    
    Handles both "oracle" and "globe" terminology - they refer to the same thing:
    - "hide oracle", "hide globe", "hide global" → hides the oracle/globe
    - "show oracle", "show globe", "show global" → shows the oracle/globe
    
    Examples: 
    - "hide oracle", "hide globe", "hide global"
    - "show oracle", "show globe", "show global"
    - "hide the oracle", "show the globe"
    
    Actions: 
    - 'hide' to hide the oracle/globe
    - 'show' to show the oracle/globe
    
    CALL THIS TOOL immediately when user asks to hide or show the oracle/globe - never explain, just execute."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    event_queue: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, event_queue=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
        self._event_queue = event_queue
    
    def _run(self, action: str = "", text: str = "", **kwargs) -> str:
        """Execute oracle control action."""
        try:
            from distr.core.actions.oracle_control import hide_oracle, show_oracle, change_oracle
            
            # Extract action from text if not provided
            if not action and text:
                text_lower = text.lower()
                if any(word in text_lower for word in ["hide", "hiding", "hidden"]):
                    action = "hide"
                elif any(word in text_lower for word in ["show", "showing", "display"]):
                    action = "show"
                elif "change" in text_lower:
                    action = "change"
            
            if not action:
                return "Error: Please specify action: 'hide' or 'show'"
            
            action_funcs = {
                "hide": hide_oracle,
                "show": show_oracle,
                "change": change_oracle,
            }
            
            func = action_funcs.get(action.lower())
            if not func:
                return f"Error: Invalid action '{action}'. Use 'hide' or 'show'"
            
            func(event_queue=self._event_queue)
            
            return f"Oracle {action} executed successfully"
            
        except Exception as e:
            logger.error(f"Error in OracleControlTool: {e}", exc_info=True)
            return f"Error controlling oracle: {str(e)}"
    
    async def _arun(self, action: str = "", text: str = "", **kwargs) -> str:
        return self._run(action=action, text=text)


class ModeControlTool(BaseTool):
    """Tool for controlling the input mode (PTT vs Continuous/Hands-free)."""
    
    name: str = "mode_control"
    description: str = """🎯 Controls the input mode between Push-To-Talk (PTT) and Continuous (Hands-free) modes.
    
    CRITICAL: Use this tool when the user wants to change between PTT and continuous modes.
    
    Modes:
    - PTT (Push-To-Talk): User must hold a button/key to speak
    - Continuous (Hands-free): Voice activity detection automatically captures speech
    
    Examples: 
    - "change mode", "switch mode", "toggle mode" → toggles between modes
    - "PTT mode", "push to talk mode", "enable PTT", "switch to PTT" → enables PTT mode
    - "continuous mode", "hands free mode", "enable continuous", "switch to continuous" → enables continuous mode
    
    Actions: 
    - 'toggle' to toggle between PTT and continuous
    - 'ptt' to enable PTT mode (disable hands-free)
    - 'continuous' to enable continuous mode (enable hands-free)
    
    CALL THIS TOOL immediately when user asks to change mode - never explain, just execute."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    event_queue: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, event_queue=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
        self._event_queue = event_queue
    
    def _run(self, action: str = "", text: str = "", **kwargs) -> str:
        """Execute mode control action."""
        try:
            # Extract action from text if not provided
            if not action and text:
                text_lower = text.lower()
                # Check for specific mode requests
                if any(word in text_lower for word in ["ptt", "push to talk", "push-to-talk"]):
                    action = "ptt"
                elif any(word in text_lower for word in ["continuous", "hands free", "hands-free"]):
                    action = "continuous"
                elif any(word in text_lower for word in ["change", "switch", "toggle"]):
                    action = "toggle"
            
            if not action:
                return "Error: Please specify action: 'toggle', 'ptt', or 'continuous'"
            
            # Get current mode from settings to determine toggle direction
            if action == "toggle":
                from distr.core.utils import load_settings_from_db
                settings = load_settings_from_db()
                current_mode = settings.get('hands_free_mode', True)
                # Toggle: if currently hands-free (continuous), switch to PTT
                new_mode = not current_mode
            elif action.lower() == "ptt":
                # Disable hands-free mode (enable PTT)
                new_mode = False
            elif action.lower() == "continuous":
                # Enable hands-free mode (continuous)
                new_mode = True
            else:
                return f"Error: Invalid action '{action}'. Use 'toggle', 'ptt', or 'continuous'"
            
            # Send event to main process via event_queue (tool runs in separate process)
            # The main process will emit the PyQt signal
            if self._event_queue:
                try:
                    self._event_queue.put(('hands_free_mode_changed', {'enabled': new_mode}), block=False)
                    logger.info(f"Sent hands_free_mode_changed event to main process: {new_mode}")
                    mode_name = "Continuous (Hands-free)" if new_mode else "PTT (Push-To-Talk)"
                    return f"Switched to {mode_name} mode"
                except Exception as e:
                    logger.error(f"Error sending hands_free_mode_changed event: {e}")
                    return f"Error changing mode: {str(e)}"
            else:
                logger.warning("No event_queue available - cannot change mode")
                return "Error: No event queue available - cannot change mode"
            
        except Exception as e:
            logger.error(f"Error in ModeControlTool: {e}", exc_info=True)
            return f"Error changing mode: {str(e)}"
    
    async def _arun(self, action: str = "", text: str = "", **kwargs) -> str:
        return self._run(action=action, text=text)


class ShortcutTool(BaseTool):
    """Tool for executing keyboard shortcuts."""
    
    name: str = "keyboard_shortcut"
    description: str = """Executes keyboard shortcuts for window and tab management.
    Use this for: creating new tabs, switching tabs, closing windows, minimizing, maximizing, fullscreen, hiding apps, quitting apps, opening Spotlight or GPT, swapping windows.
    Examples: "new tab", "close this window", "minimize", "maximize", "fullscreen", "hide app", "quit the app", "open spotlight", "swap window".
    Available shortcuts: new_tab, previous_tab, next_tab, close, quit, minimize, maximize, fullscreen, hide, open_spotlight, open_gpt, swap_window, next_window, cycle_window."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
    
    def _move_cursor_to_active_window(self):
        """Move cursor to the center of the currently active/focused window."""
        import platform
        import subprocess
        
        system = platform.system()
        logger.debug(f"[SwapWindow] Moving cursor to active window on {system}")
        
        if system == "Darwin":  # macOS
            try:
                # Use AppleScript to get the frontmost window position and size
                script = '''
                tell application "System Events"
                    set frontApp to first application process whose frontmost is true
                    tell frontApp
                        if (count of windows) > 0 then
                            set frontWindow to first window
                            set {x, y} to position of frontWindow
                            set {w, h} to size of frontWindow
                            set centerX to (x + (w / 2)) as integer
                            set centerY to (y + (h / 2)) as integer
                            return (centerX as text) & "," & (centerY as text)
                        else
                            return "no_window"
                        end if
                    end tell
                end tell
                '''
                result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=3)
                logger.debug(f"[SwapWindow] AppleScript result: returncode={result.returncode}, stdout='{result.stdout.strip()}', stderr='{result.stderr.strip()}'")

                if result.returncode == 0 and result.stdout.strip() and result.stdout.strip() != "no_window":
                    raw_output = result.stdout.strip()
                    # Handle potential extra commas or spaces
                    coords = [c.strip() for c in raw_output.split(',') if c.strip()]
                    logger.debug(f"[SwapWindow] Parsed coords from '{raw_output}': {coords}")

                    if len(coords) >= 2:
                        try:
                            center_x = int(float(coords[0]))
                            center_y = int(float(coords[1]))
                            logger.debug(f"[SwapWindow] Target coordinates: ({center_x}, {center_y})")
                        except ValueError as ve:
                            logger.warning(f"[SwapWindow] Failed to parse coordinates: {ve}")
                            return
                        
                        # Move mouse to center of window using pyautogui
                        try:
                            if pyautogui:
                                from distr.core.agent.tools.input.mouse_utils import smooth_move_to
                                smooth_move_to(center_x, center_y)
                            logger.info(f"Moved cursor to active window center: ({center_x}, {center_y})")
                        except ImportError:
                            # Fallback: use cliclick if available
                            click_result = subprocess.run(['cliclick', f'm:{center_x},{center_y}'], capture_output=True, timeout=2)
                            if click_result.returncode == 0:
                                logger.info(f"Moved cursor to active window center via cliclick: ({center_x}, {center_y})")
                            else:
                                logger.warning(f"[SwapWindow] cliclick failed: {click_result.stderr}")
                else:
                    logger.warning("[SwapWindow] Could not get window position")
                    logger.warning(f"Could not get active window position: {result.stderr}")
            except Exception as e:
                logger.warning(f"Failed to move cursor to active window on macOS: {e}")
        
        elif system == "Windows":
            try:
                if not pyautogui:
                    return None
                import ctypes
                from ctypes import wintypes
                
                # Get foreground window handle
                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                
                # Get window rect
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                
                # Calculate center
                center_x = (rect.left + rect.right) // 2
                center_y = (rect.top + rect.bottom) // 2
                
                # Move mouse
                from distr.core.agent.tools.input.mouse_utils import smooth_move_to
                smooth_move_to(center_x, center_y)
                logger.info(f"Moved cursor to active window center: ({center_x}, {center_y})")
            except Exception as e:
                logger.warning(f"Failed to move cursor to active window on Windows: {e}")
        
        else:  # Linux
            try:
                if not pyautogui:
                    return None
                # Use xdotool to get active window geometry
                result = subprocess.run(['xdotool', 'getactivewindow', 'getwindowgeometry', '--shell'], 
                                       capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    # Parse output like: WINDOW=123\nX=100\nY=100\nWIDTH=800\nHEIGHT=600
                    values = {}
                    for line in result.stdout.strip().split('\n'):
                        if '=' in line:
                            key, val = line.split('=', 1)
                            values[key] = int(val)
                    
                    if 'X' in values and 'Y' in values and 'WIDTH' in values and 'HEIGHT' in values:
                        center_x = values['X'] + values['WIDTH'] // 2
                        center_y = values['Y'] + values['HEIGHT'] // 2
                        from distr.core.agent.tools.input.mouse_utils import smooth_move_to
                        smooth_move_to(center_x, center_y)
                        logger.info(f"Moved cursor to active window center: ({center_x}, {center_y})")
            except Exception as e:
                logger.warning(f"Failed to move cursor to active window on Linux: {e}")
    
    def get_triggers(self) -> list[str]:
        """Get triggers for keyboard shortcuts."""
        return [
            "new tab", "open new tab", "agent new tab",
            "close tab", "close this tab", "close window", "close this window",
            "next tab", "switch tab",
            "previous tab", "last tab", "prev tab",
            "refresh", "reload", "reload page",
            "minimize", "minimise", "minimize window", "minimise window",
            "maximize", "maximise", "maximize window", "maximise window",
            "fullscreen", "full screen", "enter fullscreen", "exit fullscreen",
            "hide app", "hide window", "hide this app",
            "swap window", "swap windows", "next window", "cycle window",
            "switch window", "other window",
        ]
    
    def _run(self, shortcut: str = "", text: str = "", **kwargs) -> str:
        """Execute keyboard shortcut."""
        try:
            from distr.core.actions.keyboard import press_keys
            
            cmd = get_platform_modifier_key()
            is_mac = cmd == 'command'
            
            shortcut_map = {
                "new_tab": [cmd, "t"],
                "previous_tab": [cmd, "alt", "left"],
                "next_tab": [cmd, "alt", "right"],
                "close": [cmd, "w"],
                "quit": [cmd, "q"],
                "minimize": [cmd, "m"],
                "maximize": ["ctrl", cmd, "f"],
                "fullscreen": ["ctrl", cmd, "f"],
                "hide": [cmd, "h"],
                "open_spotlight": [cmd, "space"],
                "open_gpt": ["alt", "space"],
                "spotlight": [cmd, "space"],
                "gpt": ["alt", "space"],
                "swap_window": [cmd, "`"],
                "next_window": [cmd, "`"],
                "cycle_window": [cmd, "`"],
            }
            
            if not is_mac:
                 shortcut_map.update({
                     "previous_tab": [cmd, "shift", "tab"],
                     "next_tab": [cmd, "tab"],
                     "quit": ["alt", "f4"],
                     "minimize": ["win", "down"],
                     "maximize": ["win", "up"],
                     "fullscreen": ["f11"],
                     "hide": ["win", "d"],
                     "open_spotlight": ["win"],
                     "spotlight": ["win"],
                     "swap_window": ["alt", "tab"],
                     "next_window": ["alt", "tab"],
                     "cycle_window": ["alt", "tab"],
                 })
            
            # Extract shortcut from text if not provided
            if not shortcut and text:
                text_lower = text.lower().strip()
                if "new tab" in text_lower:
                    shortcut = "new_tab"
                elif "previous tab" in text_lower or "last tab" in text_lower:
                    shortcut = "previous_tab"
                elif "next tab" in text_lower:
                    shortcut = "next_tab"
                elif "close" in text_lower:
                    shortcut = "close"
                elif "quit" in text_lower or "exit" in text_lower:
                    shortcut = "quit"
                elif "minimize" in text_lower or "minimise" in text_lower:
                    shortcut = "minimize"
                elif "maximize" in text_lower or "maximise" in text_lower or "full screen" in text_lower or "fullscreen" in text_lower:
                    shortcut = "maximize"
                elif "hide" in text_lower and ("window" in text_lower or "app" in text_lower):
                    shortcut = "hide"
                elif "spotlight" in text_lower:
                    shortcut = "open_spotlight"
                elif "gpt" in text_lower:
                    shortcut = "open_gpt"
                elif any(phrase in text_lower for phrase in ["swap window", "next window", "cycle window", "switch window", "other window"]):
                    shortcut = "swap_window"
            
            if not shortcut:
                return "Error: No shortcut specified. Available: new_tab, previous_tab, next_tab, close, quit, open_spotlight, open_gpt"
            
            keys = shortcut_map.get(shortcut.lower())
            if not keys:
                return f"Error: Unknown shortcut '{shortcut}'. Available shortcuts: {', '.join(shortcut_map.keys())}"
            
            press_keys(keys)
            logger.info(f"[Shortcut] Executed: {shortcut} with keys {keys}")
            
            # For window swap shortcuts, move cursor to the newly focused window
            if shortcut.lower() in ["swap_window", "next_window", "cycle_window"]:
                import time
                logger.debug("[SwapWindow] Waiting 400ms for window focus...")
                time.sleep(0.4)
                try:
                    self._move_cursor_to_active_window()
                except Exception as e:
                    logger.warning(f"Could not move cursor to active window: {e}")
            
            return f"Executed shortcut: {shortcut}"
            
        except Exception as e:
            logger.error(f"Error in ShortcutTool: {e}", exc_info=True)
            return f"Error executing shortcut: {str(e)}"
    
    async def _arun(self, shortcut: str = "", text: str = "", **kwargs) -> str:
        return self._run(shortcut=shortcut, text=text)

