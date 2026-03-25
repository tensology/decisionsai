"""
Keyboard input — press keys and key combinations.

Used by agent tools for text editing, caret movement, shortcuts, etc.
"""

import platform
import logging
import pyautogui

pyautogui.FAILSAFE = False
logger = logging.getLogger(__name__)


def _normalize_keys_for_platform(keys: list) -> list:
    """Map macOS key names to Windows/Linux equivalents."""
    if platform.system() == 'Darwin' or not keys:
        return keys
    if isinstance(keys[0], dict):
        return keys  # Nested action refs, don't modify
    keys = ['ctrl' if k in ('command', 'cmd') else k for k in keys]
    if len(keys) == 2 and keys[0] == 'fn':
        fn_map = {'up': 'pageup', 'down': 'pagedown', 'left': 'home', 'right': 'end', 'delete': 'delete'}
        if keys[1].lower() in fn_map:
            return [fn_map[keys[1].lower()]]
    return keys


def press_keys(keys):
    """Press a key or key combination.

    Args:
        keys: A single key string, or a list of keys for a combination.
              e.g. "enter", ["command", "c"], ["F5"]
    """
    if isinstance(keys, str):
        keys = [keys]
    keys = _normalize_keys_for_platform(keys)
    logger.debug("Pressing keys: %s", keys)
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)


def get_platform_modifier() -> str:
    """Return 'command' on macOS, 'ctrl' elsewhere."""
    return 'command' if platform.system() == 'Darwin' else 'ctrl'
