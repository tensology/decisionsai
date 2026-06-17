"""
Dictation utilities for converting speech to keyboard input.
"""

import logging
import platform
import subprocess
import time
from typing import Optional
from pynput import keyboard
from pynput.keyboard import Key

logger = logging.getLogger(__name__)

# Global keyboard controller (lazy initialization)
_keyboard_controller = None

def _get_keyboard_controller():
    """Get or create the keyboard controller (lazy initialization)"""
    global _keyboard_controller
    if _keyboard_controller is None:
        try:
            _keyboard_controller = keyboard.Controller()
            logger.info("Dictation: Keyboard controller initialized")
        except Exception as e:
            logger.error(f"Dictation: Failed to create keyboard controller: {e}")
            return None
    return _keyboard_controller


def type_text(text: str, delay: float = 0.01):
    """
    Type text as if from keyboard using pynput.
    
    Args:
        text: The text to type
        delay: Delay between keypresses in seconds (default: 0.01)
    """
    controller = _get_keyboard_controller()
    if not controller:
        logger.error("Dictation: Cannot type text - keyboard controller not available")
        return False
    
    try:
        for char in text:
            if char.isupper() or char in '!@#$%^&*()_+{}|:"<>?':
                # Handle uppercase and special characters with shift
                with controller.pressed(Key.shift):
                    controller.press(char.lower())
                    controller.release(char.lower())
            elif char == '\n':
                # Handle newline as Enter key
                controller.press(Key.enter)
                controller.release(Key.enter)
            elif char == '\t':
                # Handle tab
                controller.press(Key.tab)
                controller.release(Key.tab)
            else:
                # Regular character
                controller.press(char)
                controller.release(char)
            
            if delay > 0:
                time.sleep(delay)
        
        logger.info(f"Dictation: Typed text ({len(text)} characters)")
        return True
    except Exception as e:
        logger.error(f"Dictation: Error typing text: {e}", exc_info=True)
        return False


def is_instant_dictation_enabled(settings: Optional[dict] = None) -> bool:
    """Return whether dictation should insert full text in one fast operation."""
    try:
        if settings is None:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db()
        return bool((settings or {}).get("instant_dictation", True))
    except Exception as e:
        logger.debug("Dictation: Could not read instant_dictation setting: %s", e)
        return True


def _instant_type_text_macos(text: str, press_enter: bool = False) -> bool:
    """Insert text through System Events without using or mutating the clipboard."""
    script = (
        'on run argv\n'
        '  tell application "System Events"\n'
        '    keystroke (item 1 of argv)\n'
        + ('    key code 36\n' if press_enter else '')
        + '  end tell\n'
        'end run\n'
    )
    result = subprocess.run(
        ["osascript", "-e", script, text],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        logger.warning("Dictation: Instant macOS insert failed: %s", (result.stderr or "").strip())
        return False
    return True


def _instant_type_text_macos_shift_enter(text: str) -> bool:
    """Insert multi-line text, using Shift+Enter for line breaks."""
    lines = text.split("\n")
    script = (
        'on run argv\n'
        '  tell application "System Events"\n'
        '    repeat with i from 1 to count of argv\n'
        '      set segment to item i of argv\n'
        '      if segment is not "" then keystroke segment\n'
        '      if i is less than count of argv then key code 36 using shift down\n'
        '    end repeat\n'
        '  end tell\n'
        'end run\n'
    )
    result = subprocess.run(
        ["osascript", "-e", script, *lines],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        logger.warning("Dictation: Shift+Enter macOS insert failed: %s", (result.stderr or "").strip())
        return False
    return True


def instant_type_text(text: str, press_enter: bool = False) -> bool:
    """Fast text insertion that avoids clipboard mutation."""
    if not text:
        return True
    try:
        if platform.system() == "Darwin":
            return _instant_type_text_macos(text, press_enter=press_enter)

        controller = _get_keyboard_controller()
        if not controller:
            return False
        controller.type(text)
        if press_enter:
            controller.press(Key.enter)
            controller.release(Key.enter)
        logger.info("Dictation: Instantly inserted text (%d characters)", len(text))
        return True
    except Exception as e:
        logger.error("Dictation: Instant insert failed: %s", e, exc_info=True)
        return False


def _type_text_with_shift_enter(text: str) -> bool:
    controller = _get_keyboard_controller()
    if not controller:
        return False
    try:
        lines = text.split("\n")
        for idx, line in enumerate(lines):
            if line:
                controller.type(line)
            if idx < len(lines) - 1:
                with controller.pressed(Key.shift):
                    controller.press(Key.enter)
                    controller.release(Key.enter)
        return True
    except Exception as e:
        logger.error("Dictation: Shift+Enter typing failed: %s", e, exc_info=True)
        return False


def insert_text(
    text: str,
    *,
    instant: Optional[bool] = None,
    press_enter: bool = False,
    settings: Optional[dict] = None,
    newline_mode: str = "literal",
) -> bool:
    """Shared text insertion path for dictation and remote dictation.

    Default mode preserves the existing character-by-character keyboard behavior.
    Instant mode sends the full text without touching the clipboard.
    """
    if not text:
        return True
    if newline_mode == "shift_enter":
        if platform.system() == "Darwin":
            success = _instant_type_text_macos_shift_enter(text)
            if success:
                return True
            logger.warning("Dictation: Falling back to pynput Shift+Enter typing")
        return _type_text_with_shift_enter(text)
    use_instant = is_instant_dictation_enabled(settings) if instant is None else bool(instant)
    if use_instant:
        success = instant_type_text(text, press_enter=press_enter)
        if success:
            return True
        logger.warning("Dictation: Falling back to standard typing after instant insert failure")
    success = type_text(text)
    if success and press_enter:
        controller = _get_keyboard_controller()
        if controller:
            controller.press(Key.enter)
            controller.release(Key.enter)
    return success
