"""
Screen detection utilities that work across processes.

This module provides screen detection that works in both the main GUI process
and the agent process, using platform-specific APIs when Qt is not available.

NOTE: Module-level variables are NOT shared across processes in Python multiprocessing.
We use a Manager dict for cross-process communication.
"""

import logging
import platform
import pyautogui
# Disable pyautogui FAILSAFE to prevent mouse operations from being blocked
pyautogui.FAILSAFE = False

logger = logging.getLogger(__name__)

class CachedScreenWrapper:
    """Wrapper for cached screen info that mimics QScreen interface."""
    def __init__(self, screen_info: dict):
        self._screen_info = screen_info
        self._screen_number = screen_info.get('screen_number', 1)
        self._screen_name = screen_info.get('screen_name', f'Screen {self._screen_number}')
        geo = screen_info.get('geometry', {})
        self._geo = {
            'x': geo.get('x', 0),
            'y': geo.get('y', 0),
            'width': geo.get('width', 1920),
            'height': geo.get('height', 1080)
        }
    
    def name(self):
        return self._screen_name
    
    def screen_number(self):
        """Get the screen number (1-indexed)"""
        return self._screen_number
    
    def geometry(self):
        """Return a QRect-like object with left(), top(), width(), height() methods."""
        class Geometry:
            def __init__(self, geo_dict):
                self._geo = geo_dict
            def left(self): return self._geo['x']
            def top(self): return self._geo['y']
            def right(self): return self._geo['x'] + self._geo['width']
            def bottom(self): return self._geo['y'] + self._geo['height']
            def width(self): return self._geo['width']
            def height(self): return self._geo['height']
            def x(self): return self._geo['x']
            def y(self): return self._geo['y']
            def contains(self, point): 
                x, y = (point.x(), point.y()) if hasattr(point, 'x') else (point[0], point[1])
                return (self.left() <= x <= self.right() and self.top() <= y <= self.bottom())
        return Geometry(self._geo)
    
    def grabWindow(self, window_id):
        """Mock grabWindow for screenshot capture - returns None, actual capture should use system tools."""
        return None

# Use multiprocessing.Manager for cross-process shared state
_screen_info_manager = None
_screen_info_cache = None

def init_screen_cache_manager(manager_dict=None):
    """Initialize the screen cache manager with a shared dict from multiprocessing.Manager()"""
    global _screen_info_cache
    if manager_dict is not None:
        _screen_info_cache = manager_dict
        logger.info("Initialized screen cache with multiprocessing Manager dict")
    else:
        # Fallback: use module-level dict (won't work across processes, but better than nothing)
        if _screen_info_cache is None:
            _screen_info_cache = {}
            logger.warning("Screen cache initialized without Manager - will not work across processes")


def get_current_mouse_screen_simple():
    """
    Get the screen that contains the mouse cursor using simple, cross-platform methods.
    This works in any process, even without Qt.
    
    Returns:
        dict with screen info: {'screen_number': int, 'screen_name': str, 'geometry': dict}
        or None if detection fails
    """
    try:
        current_x, current_y = pyautogui.position()
        logger.info(f"get_current_mouse_screen_simple: Mouse at ({current_x}, {current_y})")
        
        # Try to get screen info from cache (set by main process)
        if _screen_info_cache:
            try:
                screens = _screen_info_cache.get('screens', [])
                logger.info(f"get_current_mouse_screen_simple: Cache has {len(screens)} screen(s), checking cursor at ({current_x}, {current_y})")
                if screens:
                    for screen_info in screens:
                        geo = screen_info['geometry']
                        screen_left = geo['x']
                        screen_right = geo['x'] + geo['width'] - 1  # Inclusive right edge
                        screen_top = geo['y']
                        screen_bottom = geo['y'] + geo['height'] - 1  # Inclusive bottom edge
                        logger.debug(f"  Checking screen {screen_info['screen_number']}: x={screen_left}-{screen_right}, y={screen_top}-{screen_bottom}")
                        # Check if cursor is within screen bounds (inclusive)
                        if (screen_left <= current_x <= screen_right and
                            screen_top <= current_y <= screen_bottom):
                            logger.info(f"✓ get_current_mouse_screen_simple: Found screen {screen_info['screen_number']} from cache (cursor at {current_x},{current_y} is within bounds)")
                            return screen_info
                    # Fallback to first screen
                    logger.warning(f"get_current_mouse_screen_simple: Cursor at ({current_x}, {current_y}) not in any cached screen bounds, using first screen as fallback")
                    return screens[0]
                else:
                    logger.warning(f"get_current_mouse_screen_simple: Cache exists but has no screens")
            except Exception as e:
                logger.error(f"Error reading from screen cache: {e}", exc_info=True)
        else:
            logger.warning(f"get_current_mouse_screen_simple: No screen cache available")
        
        # Fallback: Use pyautogui to get primary screen size
        # This is a simple fallback that works everywhere
        try:
            screen_size = pyautogui.size()
            logger.info(f"get_current_mouse_screen_simple: Using pyautogui fallback - screen size: {screen_size}")
            return {
                'screen_number': 1,
                'screen_name': 'Primary',
                'geometry': {
                    'x': 0,
                    'y': 0,
                    'width': screen_size.width,
                    'height': screen_size.height
                }
            }
        except Exception as e:
            logger.error(f"get_current_mouse_screen_simple: pyautogui fallback failed: {e}")
            return None
            
    except Exception as e:
        logger.error(f"Error in get_current_mouse_screen_simple: {e}", exc_info=True)
        return None


def update_screen_info_cache(screen_info_list):
    """
    Update the global screen info cache.
    This should be called by the main process periodically.
    
    Args:
        screen_info_list: List of screen info dicts
    """
    global _screen_info_cache
    try:
        if _screen_info_cache is None:
            logger.warning("update_screen_info_cache: Cache not initialized, creating empty dict")
            _screen_info_cache = {}
        
        # Update the cache (works with both Manager dict and regular dict)
        _screen_info_cache['screens'] = screen_info_list
        logger.debug(f"Updated screen info cache with {len(screen_info_list)} screen(s)")
        # Log screen details for debugging
        for screen_info in screen_info_list:
            geo = screen_info['geometry']
            logger.debug(f"  Screen {screen_info['screen_number']}: {screen_info['screen_name']} - x={geo['x']}, y={geo['y']}, w={geo['width']}, h={geo['height']}")
    except (BrokenPipeError, ConnectionRefusedError, OSError) as e:
        # During shutdown, multiprocessing connections may be broken - this is expected
        logger.debug(f"Screen cache update failed during shutdown (expected): {e}")
    except Exception as e:
        logger.error(f"Error updating screen info cache: {e}", exc_info=True)


def get_all_screens_info():
    """
    Get info for all screens using Qt (only works in main process with QApplication).
    
    Returns:
        List of screen info dicts
    """
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QScreen
        
        app = QApplication.instance()
        if not app:
            logger.warning("get_all_screens_info: QApplication not available")
            return []
        
        screens = []
        try:
            screens = QScreen.availableScreens()
        except Exception:
            if hasattr(app, 'screens'):
                screens = app.screens()
        
        if not screens:
            return []
        
        # Sort by X position (left to right)
        screens_sorted = sorted(screens, key=lambda s: s.geometry().left())
        
        screen_info_list = []
        for i, screen in enumerate(screens_sorted):
            geo = screen.geometry()
            screen_info_list.append({
                'screen_number': i + 1,
                'screen_name': screen.name(),
                'geometry': {
                    'x': geo.x(),
                    'y': geo.y(),
                    'width': geo.width(),
                    'height': geo.height()
                },
                'scale_factor': screen.devicePixelRatio()
            })
        
        # Update cache
        update_screen_info_cache(screen_info_list)
        
        return screen_info_list
    except Exception as e:
        logger.error(f"Error in get_all_screens_info: {e}", exc_info=True)
        return []

