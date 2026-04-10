"""
Window management tool — minimize, maximize, close, move windows between screens.
"""
import logging
import platform
import subprocess
import time
from typing import Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None


class WindowManagementInput(BaseModel):
    action: str = Field(description="Action: minimize, maximize, fullscreen, close, hide, move_to_screen")
    screen_number: Optional[int] = Field(default=None, description="Target screen number (for move_to_screen)")
    text: Optional[str] = Field(default="", description="Original user request text")


class WindowManagementTool(BaseTool):
    name: str = "window_management"
    description: str = (
        "Manage the focused window: minimize, maximize, fullscreen, close, hide, "
        "or move it to a different screen/monitor. "
        "For move_to_screen, provide the screen_number (1, 2, 3, etc). "
        "Examples: 'minimize the window', 'maximize', 'move window to screen 2', "
        "'close this window', 'fullscreen', 'hide the app'."
    )
    args_schema: type[BaseModel] = WindowManagementInput

    def get_triggers(self) -> list[str]:
        return [
            "minimize", "minimise", "minimize window",
            "maximize", "maximise", "maximize window",
            "fullscreen", "full screen",
            "close window", "close this window",
            "hide app", "hide window", "hide this",
            "move window", "move to screen", "move to monitor",
            "send to screen", "send to monitor",
        ]

    def _run(self, action: str = "", screen_number: Optional[int] = None, text: str = "", **kwargs) -> str:
        action = action.lower().strip()

        # Parse action from text if not explicit
        if not action and text:
            t = text.lower()
            if "minimize" in t or "minimise" in t:
                action = "minimize"
            elif "maximize" in t or "maximise" in t:
                action = "maximize"
            elif "fullscreen" in t or "full screen" in t:
                action = "fullscreen"
            elif "close" in t and "window" in t:
                action = "close"
            elif "hide" in t:
                action = "hide"
            elif "move" in t and "screen" in t or "monitor" in t:
                action = "move_to_screen"
                # Try to extract screen number from text
                import re
                m = re.search(r'(?:screen|monitor)\s*(\d+)', t)
                if m:
                    screen_number = int(m.group(1))

        if not action:
            return "Error: specify an action — minimize, maximize, fullscreen, close, hide, or move_to_screen"

        system = platform.system()
        is_mac = system == "Darwin"

        try:
            from distr.core.actions.keyboard import press_keys
        except ImportError:
            press_keys = None

        if action == "minimize":
            keys = ["command", "m"] if is_mac else ["win", "down"]
            return self._press(keys, "Minimized window")

        elif action == "maximize":
            keys = ["ctrl", "command", "f"] if is_mac else ["win", "up"]
            return self._press(keys, "Maximized window")

        elif action == "fullscreen":
            keys = ["ctrl", "command", "f"] if is_mac else ["f11"]
            return self._press(keys, "Toggled fullscreen")

        elif action == "close":
            keys = ["command", "w"] if is_mac else ["ctrl", "w"]
            return self._press(keys, "Closed window")

        elif action == "hide":
            keys = ["command", "h"] if is_mac else ["win", "d"]
            return self._press(keys, "Hidden app")

        elif action == "move_to_screen":
            if screen_number is None:
                return "Error: specify screen_number (e.g. move_to_screen with screen_number=2)"
            return self._move_window_to_screen(screen_number)

        else:
            return f"Unknown action '{action}'. Use: minimize, maximize, fullscreen, close, hide, move_to_screen"

    def _press(self, keys, success_msg):
        try:
            if pyautogui:
                pyautogui.hotkey(*keys)
                return success_msg
            from distr.core.actions.keyboard import press_keys
            press_keys(keys)
            return success_msg
        except Exception as e:
            return f"Error: {e}"

    def _move_window_to_screen(self, target_screen: int) -> str:
        """Move the focused window to a different screen."""
        system = platform.system()

        if system == "Darwin":
            return self._move_window_macos(target_screen)
        elif system == "Windows":
            return self._move_window_windows(target_screen)
        else:
            return self._move_window_linux(target_screen)

    def _move_window_macos(self, target_screen: int) -> str:
        """Move focused window to target screen on macOS using AppleScript."""
        try:
            # Get screen geometries from the cache
            from distr.core.screen_utils import _screen_info_cache
            screens = (_screen_info_cache or {}).get("screens", [])
            if not screens:
                return "Error: no screen info available"
            if target_screen < 1 or target_screen > len(screens):
                return f"Error: screen {target_screen} not found. Available: 1-{len(screens)}"

            target = screens[target_screen - 1]
            geo = target.get("geometry", {})
            tx = geo.get("x", 0) + 50
            ty = geo.get("y", 0) + 50

            # AppleScript to move the frontmost window
            script = f'''
            tell application "System Events"
                set frontApp to first application process whose frontmost is true
                tell frontApp
                    if (count of windows) > 0 then
                        set position of first window to {{{tx}, {ty}}}
                        return "moved"
                    else
                        return "no_window"
                    end if
                end tell
            end tell
            '''
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and "moved" in result.stdout:
                return f"Moved window to screen {target_screen}"
            elif "no_window" in result.stdout:
                return "Error: no window to move"
            else:
                return f"Error moving window: {result.stderr.strip()}"
        except Exception as e:
            return f"Error: {e}"

    def _move_window_windows(self, target_screen: int) -> str:
        """Move focused window to target screen on Windows."""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            # Get foreground window
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return "Error: no foreground window"

            # Get current window rect
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            win_w = rect.right - rect.left
            win_h = rect.bottom - rect.top

            # Get monitor info
            monitors = []
            def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
                info = ctypes.create_string_buffer(104)
                ctypes.cast(info, ctypes.POINTER(ctypes.c_ulong))[0] = 104
                user32.GetMonitorInfoW(hMonitor, info)
                mi_left = int.from_bytes(info[4:8], 'little', signed=True)
                mi_top = int.from_bytes(info[8:12], 'little', signed=True)
                monitors.append({"x": mi_left, "y": mi_top})
                return True

            MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(wintypes.RECT), ctypes.c_double)
            user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)

            if target_screen < 1 or target_screen > len(monitors):
                return f"Error: screen {target_screen} not found. Available: 1-{len(monitors)}"

            target = monitors[target_screen - 1]
            user32.MoveWindow(hwnd, target["x"] + 50, target["y"] + 50, win_w, win_h, True)
            return f"Moved window to screen {target_screen}"
        except Exception as e:
            return f"Error: {e}"

    def _move_window_linux(self, target_screen: int) -> str:
        """Move focused window to target screen on Linux using xdotool + xrandr."""
        try:
            # Get active window
            result = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=3)
            if result.returncode != 0:
                return "Error: could not get active window"
            wid = result.stdout.strip()

            # Get screen geometries from xrandr
            result = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=3)
            import re
            screens = re.findall(r'(\d+)x(\d+)\+(\d+)\+(\d+)', result.stdout)
            if target_screen < 1 or target_screen > len(screens):
                return f"Error: screen {target_screen} not found. Available: 1-{len(screens)}"

            _, _, sx, sy = screens[target_screen - 1]
            subprocess.run(["xdotool", "windowmove", wid, str(int(sx) + 50), str(int(sy) + 50)], timeout=3)
            return f"Moved window to screen {target_screen}"
        except Exception as e:
            return f"Error: {e}"
