"""
Wake Up Tool

A tool that forces the system to wake from sleep.
Useful when the system has gone to sleep and needs to be awakened.
"""

import logging
import platform
import subprocess
from typing import Optional, Any
from langchain.tools import BaseTool
from pydantic import Field

# PyAutoGUI - import at module level and disable FAILSAFE
try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

logger = logging.getLogger(__name__)


class WakeUpTool(BaseTool):
    """
    Tool to wake the system from sleep.
    
    When called, this tool:
    1. Detects the operating system
    2. Uses platform-specific commands to wake the system
    3. Returns confirmation of the wake action
    """
    
    name: str = "wake_up"
    description: str = (
        "🔔 USE THIS TOOL when the user wants to wake the system from sleep or prevent it from sleeping. "
        "This tool attempts to wake the system from display sleep or prevent sleep by simulating user activity. "
        "Note: If the system is in deep sleep, this may not work as the application won't be running. "
        "Examples: 'wake up', 'wake the system', 'wake my computer', 'system wake up', 'prevent sleep'. "
        "This tool uses platform-specific commands and mouse/keyboard simulation to wake or prevent sleep."
    )
    
    def get_triggers(self) -> list:
        """Return trigger phrases that should match this tool."""
        return [
            "wake up",
            "wake the system",
            "wake my computer",
            "system wake up",
            "wake",
            "wake computer",
            "wake system"
        ]
    
    def _run(self, **kwargs) -> str:
        """Execute wake up action."""
        try:
            # Check if this is a Telegram request
            import threading
            has_telegram_request = hasattr(threading.current_thread(), 'telegram_request') and threading.current_thread().telegram_request
            
            # If not found on current thread, check all threads
            if not has_telegram_request:
                import threading as threading_module
                for thread in threading_module.enumerate():
                    if hasattr(thread, 'telegram_request') and thread.telegram_request:
                        has_telegram_request = True
                        break
            
            system = platform.system()
            logger.info(f"WakeUpTool: Attempting to wake system on {system} (telegram_request={has_telegram_request})")
            
            # Wake the system
            wake_result = ""
            if system == "Darwin":  # macOS
                wake_result = self._wake_macos()
            elif system == "Windows":
                wake_result = self._wake_windows()
            elif system == "Linux":
                wake_result = self._wake_linux()
            else:
                wake_result = f"Wake up not supported on {system}. Please wake the system manually."
            
            # If called from Telegram, include remote control link
            if has_telegram_request:
                # Try to get chat_id from settings (stored by telegram_manager)
                try:
                    from distr.core.settings import load_settings_from_db
                    settings = load_settings_from_db()
                    connected_accounts = settings.get('connected_accounts', [])
                    
                    # Find Telegram account
                    telegram_account = None
                    for account in connected_accounts:
                        if isinstance(account, dict) and account.get('provider') == 'telegram':
                            telegram_account = account
                            break
                    
                    if telegram_account and telegram_account.get('user_id'):
                        chat_id = telegram_account.get('user_id')
                        remote_url = f"https://www.decisionsai.net/api/remote/?channel={chat_id}"
                        logger.info(f"WakeUpTool: Found chat_id={chat_id} from settings, returning remote control link")
                        return f"{wake_result}\n\n🔗 Remote Control:\n{remote_url}"
                    else:
                        logger.warning("WakeUpTool: Telegram request but no telegram_account found in settings")
                        return f"{wake_result}\n\n⚠️ Remote control link not available yet. Please try again in a moment."
                except Exception as e:
                    logger.error(f"WakeUpTool: Error getting remote control link from settings: {e}", exc_info=True)
                    return wake_result
            
            return wake_result
        except Exception as e:
            logger.error(f"Error waking system: {e}", exc_info=True)
            return f"Error waking system: {str(e)}"
    
    async def _arun(self, **kwargs) -> str:
        """Async execution."""
        return self._run(**kwargs)
    
    def _wake_macos(self) -> str:
        """Wake macOS system using caffeinate."""
        try:
            # Use caffeinate -u to wake the system
            # -u flag simulates user activity to wake the system
            result = subprocess.run(
                ["caffeinate", "-u", "-t", "1"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                logger.info("WakeUpTool: Successfully woke macOS system")
                return "System awakened successfully."
            else:
                logger.warning(f"WakeUpTool: caffeinate returned non-zero exit code: {result.returncode}")
                # Try alternative method: simulate a key press
                return self._wake_macos_alternative()
        except subprocess.TimeoutExpired:
            logger.error("WakeUpTool: caffeinate command timed out")
            return "Wake command timed out. System may already be awake."
        except FileNotFoundError:
            logger.error("WakeUpTool: caffeinate command not found")
            return self._wake_macos_alternative()
        except Exception as e:
            logger.error(f"WakeUpTool: Error with caffeinate: {e}")
            return self._wake_macos_alternative()
    
    def _wake_macos_alternative(self) -> str:
        """Alternative method to wake macOS: simulate a key press."""
        try:
            if not pyautogui:
                return "Error: pyautogui not available"
            # Move mouse slightly to wake system (minimal movement)
            current_pos = pyautogui.position()
            pyautogui.moveRel(1, 0, duration=0.01)
            pyautogui.moveRel(-1, 0, duration=0.01)
            logger.info("WakeUpTool: Used mouse movement to wake system")
            return "System awakened using mouse movement."
        except Exception as e:
            logger.error(f"WakeUpTool: Error with mouse movement: {e}")
            return f"Could not wake system automatically. Error: {str(e)}. Please wake the system manually."
    
    def _wake_windows(self) -> str:
        """Wake Windows system."""
        try:
            # Use psshutdown or powercfg to wake system
            # Try using SetSuspendState API via Python
            import ctypes
            # SetSuspendState(False, False, False) - hibernate=False, force=False, wakeupEventsDisabled=False
            result = ctypes.windll.powrprof.SetSuspendState(False, False, False)
            if result:
                logger.info("WakeUpTool: Successfully woke Windows system")
                return "System awakened successfully."
            else:
                # Try alternative: simulate key press
                return self._wake_windows_alternative()
        except Exception as e:
            logger.error(f"WakeUpTool: Error waking Windows: {e}")
            return self._wake_windows_alternative()
    
    def _wake_windows_alternative(self) -> str:
        """Alternative method to wake Windows: simulate a key press."""
        try:
            if not pyautogui:
                return "Error: pyautogui not available"
            # Press a key to wake system
            pyautogui.press('shift')
            logger.info("WakeUpTool: Used key press to wake Windows system")
            return "System awakened using key press."
        except Exception as e:
            logger.error(f"WakeUpTool: Error with key press: {e}")
            return f"Could not wake system automatically. Error: {str(e)}. Please wake the system manually."
    
    def _wake_linux(self) -> str:
        """Wake Linux system."""
        try:
            # Try using rtcwake to wake from suspend
            # rtcwake -m off -s 0 wakes immediately
            result = subprocess.run(
                ["rtcwake", "-m", "off", "-s", "0"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                logger.info("WakeUpTool: Attempted to wake Linux system using rtcwake")
                return "Wake command sent. System should wake up."
            else:
                # Try alternative: simulate key press
                return self._wake_linux_alternative()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # rtcwake might not be available or might require root
            return self._wake_linux_alternative()
        except Exception as e:
            logger.error(f"WakeUpTool: Error waking Linux: {e}")
            return self._wake_linux_alternative()
    
    def _wake_linux_alternative(self) -> str:
        """Alternative method to wake Linux: simulate a key press."""
        try:
            if not pyautogui:
                return "Error: pyautogui not available"
            # Move mouse slightly to wake system
            current_pos = pyautogui.position()
            pyautogui.moveRel(1, 0, duration=0.01)
            pyautogui.moveRel(-1, 0, duration=0.01)
            logger.info("WakeUpTool: Used mouse movement to wake Linux system")
            return "System awakened using mouse movement."
        except Exception as e:
            logger.error(f"WakeUpTool: Error with mouse movement: {e}")
            return f"Could not wake system automatically. Error: {str(e)}. Please wake the system manually."

