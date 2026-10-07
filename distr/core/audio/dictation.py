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

# ponytail: one size cutoff. Newlines count as large because a bare Enter submits the field.
_PASTE_CHAR_THRESHOLD = 180

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
    """Insert the full text with one Unicode event, without touching the clipboard."""
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventKeyboardSetUnicodeString,
        CGEventPost,
        kCGHIDEventTap,
    )

    event = CGEventCreateKeyboardEvent(None, 0, True)
    if event is None:
        logger.warning("Dictation: Could not create the macOS Unicode input event")
        return False
    utf16_length = len(text.encode("utf-16-le")) // 2
    CGEventKeyboardSetUnicodeString(event, utf16_length, text)
    CGEventPost(kCGHIDEventTap, event)
    CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, 0, False))
    if press_enter:
        CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, 36, True))
        CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, 36, False))
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


def should_paste_text(text: str) -> bool:
    """Large or multi-line blobs are pasted. Keystrokes would hit Enter and submit early."""
    if not text:
        return False
    if "\n" in text or "\r" in text:
        return True
    return len(text) >= _PASTE_CHAR_THRESHOLD


def _set_clipboard(text: str) -> bool:
    system = platform.system()
    payload = text if text is not None else ""
    try:
        if system == "Darwin":
            result = subprocess.run(["pbcopy"], input=payload, text=True, timeout=5)
            return result.returncode == 0
        if system == "Windows":
            result = subprocess.run(
                ["powershell", "-command", "Set-Clipboard"],
                input=payload,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            try:
                result = subprocess.run(cmd, input=payload, text=True, timeout=5)
                if result.returncode == 0:
                    return True
            except Exception:
                continue
        return False
    except Exception as e:
        logger.error("Dictation: Could not set clipboard for paste: %s", e)
        return False


def _send_paste_shortcut() -> bool:
    system = platform.system()
    if system == "Darwin":
        script = (
            'tell application "System Events"\n'
            '  keystroke "v" using command down\n'
            'end tell\n'
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.warning("Dictation: Paste shortcut failed: %s", (result.stderr or "").strip())
            return False
        return True
    controller = _get_keyboard_controller()
    if not controller:
        return False
    modifier = Key.ctrl
    try:
        with controller.pressed(modifier):
            controller.press("v")
            controller.release("v")
        return True
    except Exception as e:
        logger.error("Dictation: Paste shortcut failed: %s", e)
        return False


def paste_text_blob(text: str) -> bool:
    """Put the whole blob on the clipboard and paste it. Never presses Enter."""
    if text is None:
        return False
    if not _set_clipboard(text):
        return False
    time.sleep(0.05)
    if not _send_paste_shortcut():
        return False
    logger.info("Dictation: Pasted text blob (%d characters), no enter", len(text))
    return True


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
    use_instant = is_instant_dictation_enabled(settings) if instant is None else bool(instant)
    if use_instant:
        success = instant_type_text(text, press_enter=press_enter)
        if success:
            return True
        if instant is True:
            logger.error("Dictation: Direct insertion failed; refusing clipboard or typing fallback")
            return False
        logger.warning("Dictation: Falling back after instant insert failure")
    if should_paste_text(text):
        if paste_text_blob(text):
            return True
        logger.warning("Dictation: Paste failed; inserting with Shift+Enter and no bare Enter")
        if platform.system() == "Darwin":
            if _instant_type_text_macos_shift_enter(text):
                return True
        return _type_text_with_shift_enter(text)
    if newline_mode == "shift_enter":
        if platform.system() == "Darwin":
            success = _instant_type_text_macos_shift_enter(text)
            if success:
                return True
            logger.warning("Dictation: Falling back to pynput Shift+Enter typing")
        return _type_text_with_shift_enter(text)
    success = type_text(text)
    if success and press_enter:
        controller = _get_keyboard_controller()
        if controller:
            controller.press(Key.enter)
            controller.release(Key.enter)
    return success
