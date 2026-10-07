"""
Media and Sound Control Tools for LangChain.

These tools handle media playback, volume control, and page refresh.
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import time
from distr.core.agent.tools.base import get_platform_modifier_key

# PyAutoGUI - import at module level and disable FAILSAFE
try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

logger = logging.getLogger(__name__)

MEDIA_CONFIRMATIONS = {
    "play": "Playing your media.",
    "pause": "Paused your media.",
    "stop": "Stopped playback.",
    "next_track": "Skipped to the next track.",
    "previous_track": "Went back to the previous track.",
    "volume_up": "Turned the volume up.",
    "volume_down": "Turned the volume down.",
    "mute": "Muted the audio.",
    "refresh": "Refreshed the page.",
    "reload": "Reloaded the page.",
}


class MediaControlTool(BaseTool):
    """Tool for controlling media playback, volume, and page refresh."""
    
    name: str = "media_control"
    description: str = """EXECUTE media controls: play, pause, volume, refresh.
    
    CRITICAL: When user asks to play, pause, change volume, or refresh - CALL THIS TOOL IMMEDIATELY.
    DO NOT explain. DO NOT describe. JUST CALL IT.
    
    Actions: play, pause, stop, next_track, previous_track, volume_up, volume_down, mute, refresh, reload.
    For refresh/reload in a named browser, pass target_app and restore_focus=true so the
    browser is focused before the shortcut and the user's previous app is restored after it.
    
    CALL THE TOOL - never explain it."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
    
    def _run(
        self,
        action: str = "",
        text: str = "",
        target_app: str = "",
        restore_focus: bool = False,
        **kwargs,
    ) -> str:
        """Execute media control action."""
        try:
            if not pyautogui:
                return "Error: pyautogui not available"
            from pynput.keyboard import Key, Controller as KeyboardController
            
            cmd = get_platform_modifier_key()
            
            # Map of action names to their key combinations or special handling
            action_map = {
                "play": "play",
                "pause": "pause",
                "stop": "stop",
                "next_track": "next",
                "previous_track": "previous",
                "volume_up": "volume up",
                "volume_down": "volume down",
                "mute": "mute",
                "refresh": [cmd, "r"],
                "reload": [cmd, "r"]
            }
            
            # Extract action from text if not provided
            if not action and text:
                text_lower = text.lower().strip()
                logger.info(f"Extracting action from text: '{text_lower}'")
                # Try to match common phrases
                if "refresh" in text_lower or "reload" in text_lower:
                    action = "refresh"
                elif "play" in text_lower:
                    action = "play"
                elif "pause" in text_lower:
                    action = "pause"
                elif "stop" in text_lower:
                    action = "stop"
                elif "next" in text_lower and "track" in text_lower:
                    action = "next_track"
                elif "previous" in text_lower or "last" in text_lower:
                    action = "previous_track"
                elif "volume up" in text_lower or "turn up" in text_lower:
                    action = "volume_up"
                elif "volume down" in text_lower or "turn down" in text_lower:
                    action = "volume_down"
                elif "mute" in text_lower:
                    action = "mute"
            
            if not action:
                logger.warning(f"Could not extract action from: action='{action}', text='{text}'")
                return "Error: No action specified. Available: play, pause, stop, next_track, previous_track, volume_up, volume_down, mute, refresh, reload"
            
            logger.info(f"Executing media control action: {action}")
            
            # Handle refresh/reload with pyautogui (standard keyboard shortcut)
            if action in ["refresh", "reload"]:
                previous_app = ""
                focused_app = ""
                if target_app:
                    from distr.core.actions.desktop import (
                        APP_ALIASES,
                        get_frontmost_app_name,
                        get_remembered_external_frontmost_app,
                        is_own_app_name,
                    )
                    from distr.core.agent.tools.input.window_ops import (
                        FocusWindowTool,
                        LaunchAppTool,
                    )

                    focused_app = APP_ALIASES.get(
                        " ".join(target_app.lower().split()), target_app.strip()
                    )
                    current_app = (get_frontmost_app_name() or "").strip()
                    previous_app = (
                        current_app
                        if current_app and not is_own_app_name(current_app)
                        else (get_remembered_external_frontmost_app() or "").strip()
                    )
                    focus_result = FocusWindowTool()._run(process_name=focused_app)
                    if focus_result.startswith("Error:") or "failed" in focus_result.lower():
                        launch_result = LaunchAppTool()._run(executable=focused_app)
                        if launch_result.startswith("Error:") or "failed" in launch_result.lower():
                            return f"Error: Could not focus {focused_app}: {focus_result}"
                        time.sleep(0.3)
                        focus_result = FocusWindowTool()._run(process_name=focused_app)
                        if focus_result.startswith("Error:") or "failed" in focus_result.lower():
                            return f"Error: Could not focus {focused_app}: {focus_result}"

                keys = action_map.get(action.lower())
                if isinstance(keys, list):
                    pyautogui.hotkey(*keys)
                else:
                    pyautogui.press(keys)
                logger.info(f"Executed {action} using pyautogui")
                if restore_focus and previous_app:
                    previous_norm = previous_app.lower()
                    focused_norm = focused_app.lower()
                    same_app = previous_norm in focused_norm or focused_norm in previous_norm
                    if not same_app:
                        time.sleep(0.15)
                        restore_result = FocusWindowTool()._run(process_name=previous_app)
                        if restore_result.startswith("Error:"):
                            return (
                                f"Reloaded {focused_app}, but could not return focus to "
                                f"{previous_app}: {restore_result}"
                            )
                        return f"Reloaded {focused_app} and returned focus to {previous_app}."
                return f"Reloaded {focused_app}." if focused_app else MEDIA_CONFIRMATIONS[action.lower()]
            
            # Handle media keys using pynput (which supports media keys)
            keyboard = KeyboardController()
            key_mapping = {
                "play": Key.media_play_pause,
                "pause": Key.media_play_pause,
                "stop": None,  # No stop key in pynput, use play/pause
                "next_track": Key.media_next,
                "previous_track": Key.media_previous,
                "volume_up": Key.media_volume_up,
                "volume_down": Key.media_volume_down,
                "mute": Key.media_volume_mute
            }
            
            key = key_mapping.get(action.lower())
            if key:
                keyboard.press(key)
                keyboard.release(key)
                logger.info(f"Executed {action} using pynput media key")
                return MEDIA_CONFIRMATIONS[action.lower()]
            elif action.lower() == "stop":
                # For stop, we'll use play/pause (toggle) - this is a limitation
                keyboard.press(Key.media_play_pause)
                keyboard.release(Key.media_play_pause)
                logger.info("Executed stop (using play/pause toggle)")
                return MEDIA_CONFIRMATIONS["stop"]
            else:
                return f"Error: Unknown action '{action}'. Available actions: {', '.join(action_map.keys())}"
            
        except ImportError as e:
            logger.error(f"Error importing required libraries: {e}")
            return f"Error: Required library not available. Install pynput: pip install pynput"
        except Exception as e:
            logger.error(f"Error in MediaControlTool: {e}", exc_info=True)
            return f"Error executing media control: {str(e)}"
    
    async def _arun(
        self,
        action: str = "",
        text: str = "",
        target_app: str = "",
        restore_focus: bool = False,
        **kwargs,
    ) -> str:
        # Filter out any unexpected arguments (like 'transcription' from Ollama)
        return self._run(
            action=action,
            text=text,
            target_app=target_app,
            restore_focus=restore_focus,
        )
