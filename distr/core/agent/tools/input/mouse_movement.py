"""
Mouse Movement Tools for LangChain.

Handles mouse movement: relative movement (up, down, left, right) and absolute positioning (center, top, bottom, etc.).
"""

from typing import Any, Optional
from langchain.tools import BaseTool
from pydantic import Field
import logging
import pyautogui
# Disable pyautogui FAILSAFE to prevent mouse operations from being blocked
pyautogui.FAILSAFE = False

logger = logging.getLogger(__name__)

from distr.core.agent.tools.input.mouse_utils import smooth_move_to

# Define the screen edge percentage (matching windows.py)
SCREEN_EDGE_PERCENTAGE = 15

# Try to import Qt for multi-screen support
try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QCursor
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False
    logger.warning("PyQt6 not available - multi-screen support will be limited")


class MouseMovementTool(BaseTool):
    """Tool for controlling mouse movement (relative and absolute positioning)."""
    
    name: str = "mouse_movement"
    description: str = """EXECUTE mouse movement for SIMPLE directional or positional commands ONLY.
    
    ⚠️ DO NOT USE THIS TOOL if the user mentions:
    - A specific UI element (button, link, field, etc.) - use screenshot_analyzer instead
    - "that I'm looking at", "what I see", "on my screen" - use screenshot_analyzer instead
    - Any visual element they want to interact with - use screenshot_analyzer instead
    - When screenshot_analyzer returned "TARGET NOT FOUND" - do NOT fall back to moving to screen center; ask the user to bring the target into view instead
    ""
    ONLY use this tool for:
    - Simple directional movement: "move mouse up", "move mouse left", "move mouse right", "move mouse down"
    - Simple positional movement: "move mouse to center", "move mouse to top", "move mouse to bottom"
    - Relative movement: "move mouse a bit up", "move mouse slowly right"
    - Screen selection: "move mouse to screen 2" (moves to center of screen)
    ""
    Absolute positions: move_center, move_top, move_bottom, move_left, move_right, move_middle, move_vertical_middle.
    Relative movement: move (with direction: up, down, left, right, slow_up, slow_down, slow_left, slow_right).
    Screen selection: move_to_screen (with screen_number: 1, 2, 3, etc.) - moves to center of specified screen.
    ""
    If the user wants to move to a specific button, link, or any UI element, use screenshot_analyzer instead."""
    
    chat_manager: Optional[Any] = Field(default=None, exclude=True)
    
    def __init__(self, chat_manager=None, **kwargs):
        super().__init__(**kwargs)
        self._chat_manager = chat_manager
    
    def _get_current_screen(self):
        """Get the screen that contains the mouse cursor.
        
        In the agent process, we only have QCoreApplication (no screenAt method).
        So we use the screen cache from the main process, or fall back to geometry-based detection.
        """
        import pyautogui
        current_x, current_y = pyautogui.position()
        
        # FIRST: Try to use the screen cache from main process (cross-process communication)
        try:
            from distr.core.screen_utils import get_current_mouse_screen_simple
            screen_info = get_current_mouse_screen_simple()
            if screen_info:
                screen_number = screen_info.get('screen_number', 1)
                cached_geo = screen_info.get('geometry', {})
                
                # Try to convert cached screen info to QScreen object if Qt is available
                if QT_AVAILABLE:
                    try:
                        from PyQt6.QtGui import QScreen
                        from PyQt6.QtCore import QRect, QPoint
                        from PyQt6.QtWidgets import QApplication
                        
                        # Get screens via QApplication (QScreen.availableScreens() doesn't exist)
                        app = QApplication.instance()
                        screens = []
                        if app and hasattr(app, 'screens'):
                            screens = app.screens()
                        logger.debug(f"QApplication.screens() returned {len(screens) if screens else 0} screen(s)")
                        if screens:
                            screens_sorted = sorted(screens, key=lambda s: s.geometry().left())
                            if 1 <= screen_number <= len(screens_sorted):
                                screen = screens_sorted[screen_number - 1]
                                geo = screen.geometry()
                                logger.info(f"🖥️  CURRENT SCREEN (from cache): Screen {screen_number} ({screen.name()}) - Mouse at ({current_x}, {current_y}) - Screen geometry: X={geo.x()}, Y={geo.y()}, W={geo.width()}, H={geo.height()}")
                                return screen
                            else:
                                logger.warning(f"Screen number {screen_number} out of range (1-{len(screens_sorted)})")
                        else:
                            logger.warning("QApplication.screens() returned empty list")
                    except Exception as e:
                        logger.warning(f"Could not convert cached screen info to QScreen: {e}", exc_info=True)
                    
                    # If QApplication.screens() didn't work, try to match by geometry
                    try:
                        from PyQt6.QtGui import QScreen
                        from PyQt6.QtCore import QRect, QPoint
                        from PyQt6.QtWidgets import QApplication
                        
                        app = QApplication.instance()
                        screens = []
                        if app and hasattr(app, 'screens'):
                            screens = app.screens()
                        if screens:
                            # Try to find a screen that matches the cached geometry
                            cached_rect = QRect(
                                cached_geo.get('x', 0),
                                cached_geo.get('y', 0),
                                cached_geo.get('width', 1920),
                                cached_geo.get('height', 1080)
                            )
                            cursor_point = QPoint(int(current_x), int(current_y))
                            
                            for screen in screens:
                                if screen.geometry() == cached_rect or screen.geometry().contains(cursor_point):
                                    screens_sorted = sorted(screens, key=lambda s: s.geometry().left())
                                    screen_number = screens_sorted.index(screen) + 1 if screen in screens_sorted else 1
                                    geo = screen.geometry()
                                    logger.info(f"🖥️  CURRENT SCREEN (geometry match): Screen {screen_number} ({screen.name()}) - Mouse at ({current_x}, {current_y})")
                                    return screen
                    except Exception as e:
                        logger.debug(f"Could not match screen by geometry: {e}")
                
                # If we can't get QScreen, return a wrapper that uses cached geometry
                from distr.core.screen_utils import CachedScreenWrapper
                logger.debug(f"Using cached screen info wrapper for Screen {screen_number}")
                return CachedScreenWrapper(screen_info)
        except Exception as e:
            logger.debug(f"get_current_mouse_screen_simple failed: {e}")
        
        # FALLBACK: Try Qt-based detection (only works if QApplication is available)
        if not QT_AVAILABLE:
            # Even without Qt, try to use cached screen info directly
            try:
                from distr.core.screen_utils import _screen_info_cache, CachedScreenWrapper
                if _screen_info_cache and 'screens' in _screen_info_cache:
                    cached_screens = _screen_info_cache['screens']
                    cursor_x, cursor_y = pyautogui.position()
                    for screen_info in cached_screens:
                        geo = screen_info.get('geometry', {})
                        if (geo.get('x', 0) <= cursor_x <= geo.get('x', 0) + geo.get('width', 1920) and
                            geo.get('y', 0) <= cursor_y <= geo.get('y', 0) + geo.get('height', 1080)):
                            return CachedScreenWrapper(screen_info)
            except Exception:
                pass
            return None
        
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtGui import QCursor, QScreen
            from PyQt6.QtCore import QPoint
            
            app = QApplication.instance()
            if not app:
                logger.warning("QApplication instance not available, cannot detect screen")
                return None
            
            # Check if app has screenAt method (QCoreApplication doesn't have it)
            if hasattr(app, 'screenAt'):
                # Use the same method as oracle window: QApplication.screenAt(QCursor.pos())
                try:
                    cursor_pos = QCursor.pos()
                    screen = app.screenAt(cursor_pos)
                    if screen:
                        screens_sorted = self._get_screens_sorted_by_position()
                        screen_number = screens_sorted.index(screen) + 1 if screen in screens_sorted else 1
                        geo = screen.geometry()
                        logger.info(f"🖥️  CURRENT SCREEN (screenAt): Screen {screen_number} ({screen.name()}) - Mouse at ({current_x}, {current_y})")
                        return screen
                except Exception as e:
                    logger.debug(f"QCursor.pos() failed: {e}")
                
                # Fallback: Use QPoint from pyautogui coordinates
                try:
                    cursor_point = QPoint(int(current_x), int(current_y))
                    screen = app.screenAt(cursor_point)
                    if screen:
                        screens_sorted = self._get_screens_sorted_by_position()
                        screen_number = screens_sorted.index(screen) + 1 if screen in screens_sorted else 1
                        logger.info(f"🖥️  CURRENT SCREEN (screenAt QPoint): Screen {screen_number} ({screen.name()}) - Mouse at ({current_x}, {current_y})")
                        return screen
                except Exception as e:
                    logger.debug(f"app.screenAt(QPoint) failed: {e}")
            
            # If no screenAt, use geometry-based detection
            screens = []
            if hasattr(app, 'screens'):
                screens = app.screens()
            
            if screens:
                screens_sorted = sorted(screens, key=lambda s: s.geometry().left())
                cursor_point = QPoint(int(current_x), int(current_y))
                
                for i, screen in enumerate(screens_sorted):
                    geo = screen.geometry()
                    if geo.contains(cursor_point):
                        screen_number = i + 1
                        logger.info(f"🖥️  CURRENT SCREEN (geometry): Screen {screen_number} ({screen.name()}) - Mouse at ({current_x}, {current_y})")
                        return screen
            
            # Final fallback: try to get any screen from availableScreens
            screens_sorted = self._get_screens_sorted_by_position()
            if screens_sorted:
                # Use first screen as fallback
                primary = screens_sorted[0]
                screen_number = 1
                logger.warning(f"🖥️  CURRENT SCREEN (FALLBACK): Screen {screen_number} ({primary.name()}) - Mouse at ({current_x}, {current_y})")
                return primary
            else:
                # Last resort: try primaryScreen
                primary = app.primaryScreen() if hasattr(app, 'primaryScreen') else None
                if primary:
                    logger.warning(f"🖥️  CURRENT SCREEN (FALLBACK PRIMARY): {primary.name()} - Mouse at ({current_x}, {current_y})")
                return primary
            
        except Exception as e:
            logger.error(f"Error getting current screen: {e}", exc_info=True)
            return None
    
    def _log_screen_after_move(self):
        """Log which screen the mouse is on after a move operation."""
        try:
            new_screen = self._get_current_screen()
            new_x, new_y = pyautogui.position()
            if new_screen:
                screens_sorted = self._get_screens_sorted_by_position()
                new_screen_number = screens_sorted.index(new_screen) + 1 if new_screen in screens_sorted else 1
                logger.info(f"📍 AFTER MOVE: Mouse at ({new_x}, {new_y}) on Screen {new_screen_number} ({new_screen.name()})")
            else:
                logger.info(f"📍 AFTER MOVE: Mouse at ({new_x}, {new_y}) - Could not detect screen")
        except Exception as e:
            logger.debug(f"Error logging screen after move: {e}")
    
    def _get_screens_sorted_by_position(self):
        """Get all screens sorted by their X position (left to right)."""
        screens = []
        
        # Try Qt screens first
        if QT_AVAILABLE:
            try:
                from PyQt6.QtWidgets import QApplication
                
                app = QApplication.instance()
                if app and hasattr(app, 'screens'):
                    screens = app.screens()
            except Exception:
                pass

        # If no Qt screens, use cached screen info
        if not screens:
            try:
                from distr.core.screen_utils import _screen_info_cache, CachedScreenWrapper
                if _screen_info_cache and 'screens' in _screen_info_cache:
                    cached_screens = _screen_info_cache['screens']
                    screens = [CachedScreenWrapper(si) for si in cached_screens]
            except Exception:
                pass
        
        if not screens:
            return []
        
        # Sort screens by X position (left to right)
        screens_sorted = sorted(screens, key=lambda s: s.geometry().left())
        return screens_sorted
    
    def _get_screen_by_number(self, screen_number: int):
        """Get screen by number (1-indexed), where screens are ordered left to right."""
        try:
            screens = self._get_screens_sorted_by_position()
            if screens and 1 <= screen_number <= len(screens):
                screen = screens[screen_number - 1]  # Convert to 0-indexed
                geo = screen.geometry()
                logger.info(f"Screen {screen_number} (left to right): {screen.name()} at X={geo.left()}, Y={geo.top()}, {geo.width()}x{geo.height()}")
                return screen
            return None
        except Exception as e:
            logger.debug(f"Could not get screen {screen_number}: {e}")
            return None
    
    def _run(self, action: str = "", direction: str = "", text: str = "", screen_number: Optional[int] = None, **kwargs) -> str:
        """Execute mouse movement action directly using pyautogui."""
        try:
            logger.info(f"MouseMovementTool._run called with action='{action}', direction='{direction}', text='{text}'")
            
            # Extract action and direction from text if not provided
            if text:
                text_lower = text.lower().strip()
                logger.info(f"Extracting action/direction from text: '{text_lower}' (action='{action}', direction='{direction}', screen_number={screen_number})")
                
                # ALWAYS extract screen number first (even if action is already set)
                # This ensures "move to screen X" works regardless of how action was determined
                # Also extract if action is already "move_to_screen" from fast action detector
                if not screen_number or action == "move_to_screen":
                    import re
                    
                    logger.info(f"Extracting screen number from text: '{text_lower}'")
                    
                    # First, normalize common STT mistakes for numbers
                    # Replace "too" with "two" when it appears after "screen" or "to screen"
                    text_normalized = re.sub(r'screen\s+too\b', 'screen two', text_lower, flags=re.IGNORECASE)
                    text_normalized = re.sub(r'to\s+screen\s+too\b', 'to screen two', text_normalized, flags=re.IGNORECASE)
                    text_normalized = re.sub(r'\bto\s+screen\s+to\b', 'to screen two', text_normalized, flags=re.IGNORECASE)
                    logger.info(f"Normalized text: '{text_normalized}'")
                    
                    # Map word numbers to digits
                    word_to_number = {
                        'one': 1, 'won': 1, 'first': 1,
                        'two': 2, 'too': 2, 'to': 2, 'second': 2,
                        'three': 3, 'tree': 3, 'third': 3,
                        'four': 4, 'for': 4, 'fore': 4, 'fourth': 4,
                        'five': 5, 'fife': 5, 'fifth': 5,
                        'six': 6, 'sicks': 6, 'sixth': 6,
                        'seven': 7, 'seventh': 7,
                        'eight': 8, 'ate': 8, 'eighth': 8,
                        'nine': 9, 'ninth': 9,
                        'ten': 10, 'tenth': 10
                    }
                    
                    # Try numeric pattern first (e.g., "screen 1", "screen 2", "move to screen 3")
                    screen_match = re.search(r'(?:move\s+)?(?:to\s+)?screen\s*(\d+)', text_normalized, re.IGNORECASE)
                    if screen_match:
                        screen_number = int(screen_match.group(1))
                        logger.info(f"✓ Extracted screen number from numeric pattern: {screen_number}")
                    # Also check for "move to screen 1" pattern (more specific)
                    if not screen_number:
                        screen_match = re.search(r'move\s+to\s+screen\s*(\d+)', text_normalized, re.IGNORECASE)
                        if screen_match:
                            screen_number = int(screen_match.group(1))
                            logger.info(f"✓ Extracted screen number from 'move to screen' pattern: {screen_number}")
                    
                    # Try word-based numbers (e.g., "screen two", "screen too", "move to screen two")
                    if not screen_number:
                        # Check "move to screen [word]" first (more specific)
                        for word, num in word_to_number.items():
                            pattern = rf'move\s+to\s+screen\s+{word}\b'
                            if re.search(pattern, text_normalized, re.IGNORECASE):
                                screen_number = num
                                logger.info(f"✓ Extracted screen number from 'move to screen {word}': {screen_number}")
                                break
                        # Then check "screen [word]" or "to screen [word]"
                        if not screen_number:
                            for word, num in word_to_number.items():
                                pattern = rf'(?:to\s+)?screen\s+{word}\b'
                                if re.search(pattern, text_normalized, re.IGNORECASE):
                                    screen_number = num
                                    logger.info(f"✓ Extracted screen number from word '{word}': {screen_number}")
                                    break
                    
                    # Try ordinal patterns: "the third screen", "first screen", "second monitor"
                    if not screen_number:
                        ordinal_map = {
                            'first': 1, 'second': 2, 'third': 3, 'fourth': 4,
                            'fifth': 5, 'sixth': 6, 'seventh': 7, 'eighth': 8,
                            'ninth': 9, 'tenth': 10,
                        }
                        for word, num in ordinal_map.items():
                            pattern = rf'\b{word}\s+(?:screen|monitor|display)\b'
                            if re.search(pattern, text_normalized, re.IGNORECASE):
                                screen_number = num
                                logger.info(f"✓ Extracted screen number from ordinal '{word} screen': {screen_number}")
                                break
                    
                    if screen_number:
                        logger.info(f"✓ FINAL: Extracted screen_number={screen_number} from text: '{text_lower}'")
                    else:
                        logger.warning(f"✗ Could not extract screen number from text: '{text_lower}' (normalized: '{text_normalized}')")
                
                # If action is not provided, extract it from text
                # CRITICAL: Only extract if action is empty - fast action detector already provides the correct action
                # CRITICAL: Check patterns in order from MOST SPECIFIC to LEAST SPECIFIC
                # Corner patterns MUST come before simple "top" or "bottom" patterns
                if not action:
                    # 1. CORNER POSITIONS (most specific - check FIRST)
                    if "top" in text_lower and "right" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        action = "move_top_right"
                        logger.info("Detected: move_top_right")
                    elif "top" in text_lower and "left" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        action = "move_top_left"
                        logger.info("Detected: move_top_left")
                    elif "bottom" in text_lower and "right" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        action = "move_bottom_right"
                        logger.info("Detected: move_bottom_right")
                    elif "bottom" in text_lower and "left" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        action = "move_bottom_left"
                        logger.info("Detected: move_bottom_left")
                    # 2. CENTER-ALIGNED POSITIONS (top center, bottom center, etc.)
                    elif "top" in text_lower and "center" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        action = "move_top_center"
                        logger.info("Detected: move_top_center")
                    elif "bottom" in text_lower and "center" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        action = "move_bottom_center"
                        logger.info("Detected: move_bottom_center")
                    elif "left" in text_lower and "center" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        action = "move_left_center"
                        logger.info("Detected: move_left_center")
                    elif "right" in text_lower and "center" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        action = "move_right_center"
                        logger.info("Detected: move_right_center")
                    # 3. MIDDLE POSITIONS (middle left, middle right, etc.)
                    elif "middle" in text_lower and "left" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        action = "move_middle_left"
                        logger.info("Detected: move_middle_left")
                    elif "middle" in text_lower and "right" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        action = "move_middle_right"
                        logger.info("Detected: move_middle_right")
                    elif "middle" in text_lower:
                        if "vertical" in text_lower:
                            action = "move_vertical_middle"
                        else:
                            action = "move_middle"
                        logger.info(f"Detected: {action}")
                    # 4. EDGE POSITIONS (left edge, right edge, etc.)
                    elif "left" in text_lower and ("edge" in text_lower or "far" in text_lower or "of screen" in text_lower):
                        action = "move_left"
                        logger.info("Detected: move_left")
                    elif "right" in text_lower and ("edge" in text_lower or "far" in text_lower or "of screen" in text_lower):
                        action = "move_right"
                        logger.info("Detected: move_right")
                    # 5. CENTER (generic center - check after all specific positions)
                    elif "center" in text_lower:
                        if "vertical" in text_lower:
                            action = "move_vertical_middle"
                        elif "horizontal" in text_lower:
                            action = "move_middle"
                        else:
                            action = "move_center"
                        logger.info(f"Detected position action: {action}")
                    # 6. RELATIVE MOVEMENT (move up, move down, etc.)
                    elif "move" in text_lower or "mouse" in text_lower:
                        action = "move"
                        # Extract direction
                        if "up" in text_lower:
                            direction = "slow_up" if "slow" in text_lower else "up"
                        elif "down" in text_lower:
                            direction = "slow_down" if "slow" in text_lower else "down"
                        elif "left" in text_lower:
                            direction = "slow_left" if "slow" in text_lower else "left"
                        elif "right" in text_lower:
                            direction = "slow_right" if "slow" in text_lower else "right"
                        logger.info(f"Detected: move with direction={direction}")
                    # 7. SIMPLE TOP/BOTTOM (LEAST SPECIFIC - check LAST to avoid catching corners)
                    elif "top" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        # Only match if NOT followed by left/right/center (already checked above)
                        if "left" not in text_lower and "right" not in text_lower and "center" not in text_lower:
                            action = "move_top"
                            logger.info("Detected: move_top")
                    elif "bottom" in text_lower and ("move" in text_lower or "mouse" in text_lower):
                        # Only match if NOT followed by left/right/center (already checked above)
                        if "left" not in text_lower and "right" not in text_lower and "center" not in text_lower:
                            action = "move_bottom"
                            logger.info("Detected: move_bottom")
                else:
                    # Action was provided (e.g., from fast action detector) - use it and log it
                    logger.info(f"Using provided action: '{action}' (from fast action detector or explicit call)")
                
                # If action is "move" but direction is missing, try to extract direction from text
                if action == "move" and not direction:
                    logger.info(f"Action is 'move' but direction is missing, extracting from text: '{text_lower}'")
                    if "up" in text_lower:
                        direction = "slow_up" if "slow" in text_lower else "up"
                    elif "down" in text_lower:
                        direction = "slow_down" if "slow" in text_lower else "down"
                    elif "left" in text_lower:
                        direction = "slow_left" if "slow" in text_lower else "left"
                    elif "right" in text_lower:
                        direction = "slow_right" if "slow" in text_lower else "right"
                    logger.info(f"Extracted direction from text: {direction}")
                
            
            if not action:
                logger.warning(f"Could not extract action from: action='{action}', text='{text}'")
                return "Error: No action specified. Available: move_center, move_top, move_bottom, move_left, move_right, move_middle, move_vertical_middle, move (with direction), or move to screen (1, 2, 3, etc.)"
            
            logger.info(f"Executing mouse movement: {action} (direction={direction}, screen_number={screen_number})")
            
            # Handle move to specific screen (takes priority)
            if screen_number:
                try:
                    target_screen = self._get_screen_by_number(screen_number)
                    if target_screen:
                        screen_geo = target_screen.geometry()
                        center_x = screen_geo.left() + screen_geo.width() // 2
                        center_y = screen_geo.top() + screen_geo.height() // 2
                        smooth_move_to(center_x, center_y)
                        logger.info(f"Moved mouse to center of screen {screen_number}: ({center_x}, {center_y})")
                        return f"Moved mouse to center of screen {screen_number}"
                    else:
                        # Safely get screen count and list screen positions
                        num_screens = "unknown"
                        screen_info = []
                        try:
                            screens = self._get_screens_sorted_by_position()
                            if screens:
                                num_screens = len(screens)
                                # List screen positions for debugging
                                for i, screen in enumerate(screens, 1):
                                    geo = screen.geometry()
                                    screen_info.append(f"Screen {i}: X={geo.left()}, Y={geo.top()}")
                                logger.info(f"Available screens (left to right): {', '.join(screen_info)}")
                        except Exception:
                            pass
                        
                        error_msg = f"Error: Screen {screen_number} not found. Available screens: {num_screens}"
                        if screen_info:
                            error_msg += f" ({', '.join(screen_info)})"
                        return error_msg
                except Exception as e:
                    logger.error(f"Error moving to screen {screen_number}: {e}", exc_info=True)
                    return f"Error moving to screen {screen_number}: {str(e)}"
            
            # Handle relative movement
            if action == "move":
                if not direction:
                    return "Error: Direction required for move action. Use: up, down, left, right, slow_up, slow_down, slow_left, slow_right"
                
                try:
                    # Map directions to relative movement amounts
                    move_params = {
                        "up": [0, -80],
                        "down": [0, 80],
                        "left": [-80, 0],
                        "right": [80, 0],
                        "slow_up": [0, -30],
                        "slow_down": [0, 30],
                        "slow_left": [-30, 0],
                        "slow_right": [30, 0]
                    }
                    
                    params = move_params.get(direction.lower())
                    if not params:
                        return f"Error: Unknown direction '{direction}'"
                    
                    # Execute relative movement with smooth animation
                    x, y = params
                    cx, cy = pyautogui.position()
                    smooth_move_to(cx + x, cy + y)
                    
                    logger.info(f"Moved mouse {direction} by ({x}, {y})")
                    return f"Moved mouse {direction}"
                except Exception as e:
                    logger.error(f"Error executing mouse movement: {e}", exc_info=True)
                    return f"Error moving mouse: {str(e)}"
            
            # Handle absolute position actions
            if action.startswith("move_"):
                try:
                    # Get the current screen FIRST (screen mouse is on BEFORE move)
                    # This must be done before getting position to ensure we use the correct screen
                    current_screen = self._get_current_screen()
                    
                    # Also get cached screen info as fallback
                    cached_screen_info = None
                    if not current_screen:
                        try:
                            from distr.core.screen_utils import get_current_mouse_screen_simple
                            cached_screen_info = get_current_mouse_screen_simple()
                        except Exception:
                            pass
                    
                    current_x, current_y = pyautogui.position()
                    
                    if current_screen:
                        screen_geo = current_screen.geometry()
                        screens_sorted = self._get_screens_sorted_by_position()
                        screen_number = screens_sorted.index(current_screen) + 1 if current_screen in screens_sorted else 1
                        logger.info(f"📍 BEFORE MOVE: Mouse at ({current_x}, {current_y}) on Screen {screen_number} ({current_screen.name()})")
                    elif cached_screen_info:
                        # Use cached screen info
                        screen_number = cached_screen_info.get('screen_number', 1)
                        cached_geo = cached_screen_info.get('geometry', {})
                        logger.info(f"📍 BEFORE MOVE: Mouse at ({current_x}, {current_y}) on Screen {screen_number} (from cache) - Geometry: X={cached_geo.get('x', 0)}, Y={cached_geo.get('y', 0)}, W={cached_geo.get('width', 1920)}, H={cached_geo.get('height', 1080)}")
                    else:
                        logger.warning("📍 BEFORE MOVE: Could not detect current screen, using primary screen fallback")
                    
                    if action == "move_center":
                        if current_screen:
                            # Use current screen's geometry
                            screen_geo = current_screen.geometry()
                            center_x = screen_geo.left() + screen_geo.width() // 2
                            center_y = screen_geo.top() + screen_geo.height() // 2
                            logger.info(f"Moving to center of current screen: {current_screen.name()}, geometry: {screen_geo}")
                            screen_name = current_screen.name()
                        elif cached_screen_info:
                            # Use cached screen geometry
                            cached_geo = cached_screen_info.get('geometry', {})
                            center_x = cached_geo.get('x', 0) + cached_geo.get('width', 1920) // 2
                            center_y = cached_geo.get('y', 0) + cached_geo.get('height', 1080) // 2
                            screen_number = cached_screen_info.get('screen_number', 1)
                            logger.info(f"Moving to center of cached screen {screen_number}, geometry: {cached_geo}")
                            screen_name = f"Screen {screen_number}"
                        else:
                            # Fallback to pyautogui (primary screen)
                            screen_size = pyautogui.size()
                            logger.info("Using primary screen (Qt not available)")
                            center_x = screen_size.width // 2
                            center_y = screen_size.height // 2
                            screen_name = "primary"
                        
                        smooth_move_to(center_x, center_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to center: ({center_x}, {center_y})")
                        return f"Moved mouse to center of {screen_name} screen"
                    
                    elif action == "move_middle":
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            middle_x = screen_geo.left() + screen_geo.width() // 2
                        else:
                            screen_size = pyautogui.size()
                        middle_x = screen_size.width // 2
                        smooth_move_to(middle_x, current_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to horizontal middle: ({middle_x}, {current_y})")
                        return f"Moved mouse to horizontal middle"
                    
                    elif action == "move_vertical_middle":
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            middle_y = screen_geo.top() + screen_geo.height() // 2
                        else:
                            screen_size = pyautogui.size()
                        middle_y = screen_size.height // 2
                        smooth_move_to(current_x, middle_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to vertical middle: ({current_x}, {middle_y})")
                        return f"Moved mouse to vertical middle"
                    
                    elif action == "move_top":
                        # Move to top edge, keeping current X position
                        # current_x is already available from line 569
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            top_y = screen_geo.top() + int(screen_geo.height() * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = current_screen.name()
                        else:
                            screen_size = pyautogui.size()
                            top_y = int(screen_size.height * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = "primary"
                        # Keep the current X position - only change Y
                        smooth_move_to(current_x, top_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to top (X={current_x} unchanged, Y={top_y})")
                        return f"Moved mouse to top (X position unchanged)"
                    
                    elif action == "move_top_center":
                        # Move to top center (center X, top Y)
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            center_x = screen_geo.left() + screen_geo.width() // 2
                            top_y = screen_geo.top() + int(screen_geo.height() * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = current_screen.name()
                        else:
                            screen_size = pyautogui.size()
                            center_x = screen_size.width // 2
                            top_y = int(screen_size.height * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = "primary"
                        smooth_move_to(center_x, top_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to top center: ({center_x}, {top_y})")
                        return f"Moved mouse to top center of {screen_name} screen"
                    
                    elif action == "move_bottom":
                        # Move to bottom edge, keeping current X position
                        # current_x is already available from line 569
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            bottom_y = screen_geo.bottom() - int(screen_geo.height() * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = current_screen.name()
                        else:
                            screen_size = pyautogui.size()
                            bottom_y = screen_size.height - int(screen_size.height * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = "primary"
                        # Keep the current X position - only change Y
                        smooth_move_to(current_x, bottom_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to bottom (X={current_x} unchanged, Y={bottom_y})")
                        return f"Moved mouse to bottom (X position unchanged)"
                    
                    elif action == "move_bottom_center":
                        # Move to bottom center (center X, bottom Y)
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            center_x = screen_geo.left() + screen_geo.width() // 2
                            bottom_y = screen_geo.bottom() - int(screen_geo.height() * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = current_screen.name()
                        else:
                            screen_size = pyautogui.size()
                            center_x = screen_size.width // 2
                            bottom_y = screen_size.height - int(screen_size.height * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = "primary"
                        smooth_move_to(center_x, bottom_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to bottom center: ({center_x}, {bottom_y})")
                        return f"Moved mouse to bottom center of {screen_name} screen"
                    
                    elif action == "move_left":
                        # Move to far left edge, keeping current Y position
                        current_x, current_y = pyautogui.position()
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            left_x = screen_geo.left() + int(screen_geo.width() * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = current_screen.name()
                        else:
                            screen_size = pyautogui.size()
                            left_x = int(screen_size.width * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = "primary"
                        # Keep the current Y position - only change X
                        smooth_move_to(left_x, current_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to far left (X={left_x}, Y={current_y} unchanged)")
                        return f"Moved mouse to far left (Y position unchanged)"
                    
                    elif action == "move_left_center":
                        # Move to left center (left X, center Y)
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            left_x = screen_geo.left() + int(screen_geo.width() * (SCREEN_EDGE_PERCENTAGE / 100))
                            center_y = screen_geo.top() + screen_geo.height() // 2
                            screen_name = current_screen.name()
                        else:
                            screen_size = pyautogui.size()
                            left_x = int(screen_size.width * (SCREEN_EDGE_PERCENTAGE / 100))
                            center_y = screen_size.height // 2
                            screen_name = "primary"
                        smooth_move_to(left_x, center_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to left center: ({left_x}, {center_y})")
                        return f"Moved mouse to left center of {screen_name} screen"
                    
                    elif action == "move_right":
                        # Move to far right edge, keeping current Y position
                        current_x, current_y = pyautogui.position()
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            right_x = screen_geo.right() - int(screen_geo.width() * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = current_screen.name()
                        else:
                            screen_size = pyautogui.size()
                            right_x = screen_size.width - int(screen_size.width * (SCREEN_EDGE_PERCENTAGE / 100))
                            screen_name = "primary"
                        # Keep the current Y position - only change X
                        smooth_move_to(right_x, current_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to far right (X={right_x}, Y={current_y} unchanged)")
                        return f"Moved mouse to far right (Y position unchanged)"
                    
                    elif action == "move_right_center":
                        # Move to right center (right X, center Y)
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            right_x = screen_geo.right() - int(screen_geo.width() * (SCREEN_EDGE_PERCENTAGE / 100))
                            center_y = screen_geo.top() + screen_geo.height() // 2
                            screen_name = current_screen.name()
                        else:
                            screen_size = pyautogui.size()
                            right_x = screen_size.width - int(screen_size.width * (SCREEN_EDGE_PERCENTAGE / 100))
                            center_y = screen_size.height // 2
                            screen_name = "primary"
                        smooth_move_to(right_x, center_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to right center: ({right_x}, {center_y})")
                        return f"Moved mouse to right center of {screen_name} screen"
                    
                    elif action == "move_top_right":
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            top_y = screen_geo.top() + int(screen_geo.height() * (SCREEN_EDGE_PERCENTAGE / 100))
                            right_x = screen_geo.right() - int(screen_geo.width() * (SCREEN_EDGE_PERCENTAGE / 100))
                        else:
                            screen_size = pyautogui.size()
                            top_y = int(screen_size.height * (SCREEN_EDGE_PERCENTAGE / 100))
                            right_x = screen_size.width - int(screen_size.width * (SCREEN_EDGE_PERCENTAGE / 100))
                        smooth_move_to(right_x, top_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to top right: ({right_x}, {top_y})")
                        return f"Moved mouse to top right corner"
                    
                    elif action == "move_top_left":
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            top_y = screen_geo.top() + int(screen_geo.height() * (SCREEN_EDGE_PERCENTAGE / 100))
                            left_x = screen_geo.left() + int(screen_geo.width() * (SCREEN_EDGE_PERCENTAGE / 100))
                        else:
                            screen_size = pyautogui.size()
                            top_y = int(screen_size.height * (SCREEN_EDGE_PERCENTAGE / 100))
                            left_x = int(screen_size.width * (SCREEN_EDGE_PERCENTAGE / 100))
                        smooth_move_to(left_x, top_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to top left: ({left_x}, {top_y})")
                        return f"Moved mouse to top left corner"
                    
                    elif action == "move_bottom_right":
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            bottom_y = screen_geo.bottom() - int(screen_geo.height() * (SCREEN_EDGE_PERCENTAGE / 100))
                            right_x = screen_geo.right() - int(screen_geo.width() * (SCREEN_EDGE_PERCENTAGE / 100))
                        else:
                            screen_size = pyautogui.size()
                            bottom_y = screen_size.height - int(screen_size.height * (SCREEN_EDGE_PERCENTAGE / 100))
                            right_x = screen_size.width - int(screen_size.width * (SCREEN_EDGE_PERCENTAGE / 100))
                        smooth_move_to(right_x, bottom_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to bottom right: ({right_x}, {bottom_y})")
                        return f"Moved mouse to bottom right corner"
                    
                    elif action == "move_bottom_left":
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            bottom_y = screen_geo.bottom() - int(screen_geo.height() * (SCREEN_EDGE_PERCENTAGE / 100))
                            left_x = screen_geo.left() + int(screen_geo.width() * (SCREEN_EDGE_PERCENTAGE / 100))
                        else:
                            screen_size = pyautogui.size()
                            bottom_y = screen_size.height - int(screen_size.height * (SCREEN_EDGE_PERCENTAGE / 100))
                            left_x = int(screen_size.width * (SCREEN_EDGE_PERCENTAGE / 100))
                        smooth_move_to(left_x, bottom_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to bottom left: ({left_x}, {bottom_y})")
                        return f"Moved mouse to bottom left corner"
                    
                    elif action == "move_middle_left":
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            middle_y = screen_geo.top() + screen_geo.height() // 2
                            left_x = screen_geo.left() + int(screen_geo.width() * (SCREEN_EDGE_PERCENTAGE / 100))
                        else:
                            screen_size = pyautogui.size()
                            middle_y = screen_size.height // 2
                            left_x = int(screen_size.width * (SCREEN_EDGE_PERCENTAGE / 100))
                        smooth_move_to(left_x, middle_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to middle left: ({left_x}, {middle_y})")
                        return f"Moved mouse to middle left"
                    
                    elif action == "move_middle_right":
                        if current_screen:
                            screen_geo = current_screen.geometry()
                            middle_y = screen_geo.top() + screen_geo.height() // 2
                            right_x = screen_geo.right() - int(screen_geo.width() * (SCREEN_EDGE_PERCENTAGE / 100))
                        else:
                            screen_size = pyautogui.size()
                            middle_y = screen_size.height // 2
                            right_x = screen_size.width - int(screen_size.width * (SCREEN_EDGE_PERCENTAGE / 100))
                        smooth_move_to(right_x, middle_y)
                        self._log_screen_after_move()
                        logger.info(f"Moved mouse to middle right: ({right_x}, {middle_y})")
                        return f"Moved mouse to middle right"
                    
                    else:
                        return f"Error: Unknown position action '{action}'"
                except Exception as e:
                    logger.error(f"Error executing mouse position action: {e}", exc_info=True)
                    return f"Error moving mouse: {str(e)}"
            
            else:
                return f"Error: Unknown action '{action}'"
            
        except Exception as e:
            logger.error(f"Error in MouseMovementTool: {e}", exc_info=True)
            return f"Error controlling mouse: {str(e)}"
    
    async def _arun(self, action: str = "", direction: str = "", text: str = "", screen_number: Optional[int] = None, **kwargs) -> str:
        return self._run(action=action, direction=direction, text=text, screen_number=screen_number)

