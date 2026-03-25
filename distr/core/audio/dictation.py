"""
Dictation utilities for converting speech to keyboard input.
"""

import logging
import time
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

