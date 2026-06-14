"""
Screen Capture Utilities

Functions for capturing screenshots, detecting screens, and converting images.
Extracted from screenshot_analyzer.py for better organisation.
"""

import base64
import mimetypes
import logging
import os
import platform
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

# PyAutoGUI - import at module level and disable FAILSAFE
try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None


# ---------------------------------------------------------------------------
# Screen detection
# ---------------------------------------------------------------------------

def get_current_mouse_screen():
    """
    Get the screen that the mouse cursor is currently on.
    Uses cached screen info when Qt can't provide QScreen objects.

    Returns:
        QScreen object or CachedScreenWrapper if available, None otherwise
    """
    if not pyautogui:
        return None
    current_x, current_y = pyautogui.position()

    # FIRST: Try cached screen info (cross-process communication)
    try:
        from distr.core.screen_utils import get_current_mouse_screen_simple, CachedScreenWrapper
        screen_info = get_current_mouse_screen_simple()
        if screen_info:
            try:
                from PyQt6.QtWidgets import QApplication
                from PyQt6.QtGui import QScreen
                screens = QScreen.availableScreens()
                if screens:
                    screens_sorted = sorted(screens, key=lambda s: s.geometry().left())
                    screen_number = screen_info.get('screen_number', 1)
                    if 1 <= screen_number <= len(screens_sorted):
                        return screens_sorted[screen_number - 1]
            except Exception:
                pass
            return CachedScreenWrapper(screen_info)
    except Exception as e:
        logger.debug(f"get_current_mouse_screen_simple failed: {e}")

    # FALLBACK: Try Qt-based detection
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QCursor, QScreen
        from PyQt6.QtCore import QPoint

        app = QApplication.instance()
        if app is None:
            return None

        if hasattr(app, 'screenAt'):
            try:
                cursor_pos = QCursor.pos()
                screen = app.screenAt(cursor_pos)
                if screen:
                    return screen
            except Exception:
                pass
            try:
                cursor_point = QPoint(int(current_x), int(current_y))
                screen = app.screenAt(cursor_point)
                if screen:
                    return screen
            except Exception:
                pass

        screens = QScreen.availableScreens()
        if screens:
            screens_sorted = sorted(screens, key=lambda s: s.geometry().left())
            cursor_point = QPoint(int(current_x), int(current_y))
            for screen in screens_sorted:
                if screen.geometry().contains(cursor_point):
                    return screen

        if hasattr(app, 'primaryScreen'):
            return app.primaryScreen()
    except Exception as e:
        logger.debug(f"Qt-based screen detection failed: {e}")

    return None


def get_screens_sorted_by_position():
    """
    Get all screens sorted by their X position (left to right).
    Uses cached screen info when Qt can't provide QScreen objects.

    Returns:
        List of QScreen objects or CachedScreenWrapper objects sorted by X position
    """
    screens = []

    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QScreen
        try:
            screens = QScreen.availableScreens()
        except Exception:
            app = QApplication.instance()
            if app and hasattr(app, 'screens'):
                screens = app.screens()
    except Exception:
        pass

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

    return sorted(screens, key=lambda s: s.geometry().left())


def get_screen_by_number(screen_number: int):
    """
    Get screen by number (1-indexed), where screens are ordered left to right.

    Args:
        screen_number: Screen number (1 = leftmost, 2 = next to right, etc.)

    Returns:
        QScreen object if available, None otherwise
    """
    try:
        screens = get_screens_sorted_by_position()
        if screens and 1 <= screen_number <= len(screens):
            return screens[screen_number - 1]
        return None
    except Exception as e:
        logger.debug(f"Could not get screen {screen_number}: {e}")
        return None


# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------


def capture_all_screens(output_dir: str) -> list:
    """
    Capture screenshots of all monitors/screens.

    Args:
        output_dir: Directory where screenshots should be saved

    Returns:
        List of paths to captured screenshots, or empty list if failed
    """
    system = platform.system()
    screenshot_paths = []

    try:
        if system == "Darwin":
            import subprocess
            os.makedirs(output_dir, exist_ok=True)

            result = subprocess.run(
                ['system_profiler', 'SPDisplaysDataType'],
                capture_output=True, text=True, timeout=10,
            )

            try:
                from PyQt6.QtWidgets import QApplication
                from PyQt6.QtGui import QScreen
                import sys

                try:
                    screens = QScreen.availableScreens()
                    if screens:
                        screens = sorted(screens, key=lambda s: s.geometry().left())
                        logger.info(f"Found {len(screens)} screen(s) via QScreen.availableScreens()")
                        for i, screen in enumerate(screens):
                            geo = screen.geometry()
                            logger.info(f"Screen {i+1}: {screen.name()} at X={geo.left()}, Y={geo.top()}, {geo.width()}x{geo.height()}")
                            screenshot_path = os.path.join(output_dir, f"screen_{i+1}.png")
                            try:
                                pixmap = screen.grabWindow(0)
                                if pixmap.save(screenshot_path, 'PNG'):
                                    screenshot_paths.append(screenshot_path)
                                    logger.info(f"Captured screen {i+1}: {screen.name()} -> {screenshot_path}")
                                else:
                                    logger.warning(f"Failed to save screenshot for screen {i+1}")
                            except Exception as e:
                                logger.warning(f"Error capturing screen {i+1}: {e}")
                    else:
                        raise AttributeError("No screens available")
                except (AttributeError, TypeError) as e:
                    logger.debug(f"QScreen.availableScreens() failed: {e}, trying QApplication")
                    app = QApplication.instance()
                    if app is None:
                        app = QApplication(sys.argv)

                    if hasattr(app, 'screens'):
                        screens = sorted(app.screens(), key=lambda s: s.geometry().left())
                        logger.info(f"Found {len(screens)} screen(s) via QApplication")
                        for i, screen in enumerate(screens):
                            geo = screen.geometry()
                            logger.info(f"Screen {i+1}: {screen.name()} at X={geo.left()}, Y={geo.top()}, {geo.width()}x{geo.height()}")
                            screenshot_path = os.path.join(output_dir, f"screen_{i+1}.png")
                            try:
                                pixmap = screen.grabWindow(0)
                                if pixmap.save(screenshot_path, 'PNG'):
                                    screenshot_paths.append(screenshot_path)
                                    logger.info(f"Captured screen {i+1}: {screen.name()} -> {screenshot_path}")
                                else:
                                    logger.warning(f"Failed to save screenshot for screen {i+1}")
                            except Exception as e:
                                logger.warning(f"Error capturing screen {i+1}: {e}")
                    else:
                        logger.warning("QApplication.screens() not available")

                    try:
                        result = subprocess.run(
                            ['system_profiler', 'SPDisplaysDataType'],
                            capture_output=True, text=True, timeout=10,
                        )
                        display_count = result.stdout.count("Resolution:")
                        logger.info(f"Detected {display_count} display(s) via system_profiler")

                        screenshot_path = os.path.join(output_dir, "screen_1.png")
                        result = subprocess.run(
                            ['screencapture', screenshot_path],
                            capture_output=True, timeout=10,
                        )
                        if result.returncode == 0 and os.path.exists(screenshot_path):
                            screenshot_paths.append(screenshot_path)
                            logger.warning("Only captured primary screen.")
                    except Exception as e:
                        logger.warning(f"Error detecting displays: {e}")
                        screenshot_path = os.path.join(output_dir, "screen_1.png")
                        result = subprocess.run(
                            ['screencapture', screenshot_path],
                            capture_output=True, timeout=10,
                        )
                        if result.returncode == 0 and os.path.exists(screenshot_path):
                            screenshot_paths.append(screenshot_path)
            except ImportError:
                logger.warning("PyQt6 not available, using fallback method")
                screenshot_path = os.path.join(output_dir, "screen_1.png")
                result = subprocess.run(
                    ['screencapture', screenshot_path],
                    capture_output=True, timeout=10,
                )
                if result.returncode == 0 and os.path.exists(screenshot_path):
                    screenshot_paths.append(screenshot_path)

        elif system == "Windows":
            try:
                from PIL import ImageGrab
                from PyQt6.QtWidgets import QApplication
                from PyQt6.QtGui import QScreen
                import sys

                try:
                    screens = QScreen.availableScreens()
                    if screens:
                        screens = sorted(screens, key=lambda s: s.geometry().left())
                        for i, screen in enumerate(screens):
                            screenshot_path = os.path.join(output_dir, f"screen_{i+1}.png")
                            pixmap = screen.grabWindow(0)
                            if pixmap.save(screenshot_path, 'PNG'):
                                screenshot_paths.append(screenshot_path)
                                logger.info(f"Captured screen {i+1}: {screen.name()} -> {screenshot_path}")
                    else:
                        raise AttributeError("No screens available")
                except (AttributeError, TypeError):
                    app = QApplication.instance()
                    if app is None:
                        app = QApplication(sys.argv)
                    if hasattr(app, 'screens'):
                        for i, screen in enumerate(app.screens()):
                            screenshot_path = os.path.join(output_dir, f"screen_{i+1}.png")
                            pixmap = screen.grabWindow(0)
                            if pixmap.save(screenshot_path, 'PNG'):
                                screenshot_paths.append(screenshot_path)
                                logger.info(f"Captured screen {i+1}: {screen.name()} -> {screenshot_path}")
                    else:
                        screenshot_path = os.path.join(output_dir, "screen_1.png")
                        screenshot = ImageGrab.grab()
                        screenshot.save(screenshot_path, 'PNG')
                        screenshot_paths.append(screenshot_path)
            except ImportError:
                logger.error("PIL/Pillow or PyQt6 not installed")

        else:  # Linux
            try:
                from PyQt6.QtWidgets import QApplication
                from PyQt6.QtGui import QScreen
                import sys

                try:
                    screens = QScreen.availableScreens()
                    if screens:
                        for i, screen in enumerate(screens):
                            screenshot_path = os.path.join(output_dir, f"screen_{i+1}.png")
                            pixmap = screen.grabWindow(0)
                            if pixmap.save(screenshot_path, 'PNG'):
                                screenshot_paths.append(screenshot_path)
                                logger.info(f"Captured screen {i+1}: {screen.name()} -> {screenshot_path}")
                    else:
                        raise AttributeError("No screens available")
                except (AttributeError, TypeError):
                    app = QApplication.instance()
                    if app is None:
                        app = QApplication(sys.argv)
                    if hasattr(app, 'screens'):
                        for i, screen in enumerate(app.screens()):
                            screenshot_path = os.path.join(output_dir, f"screen_{i+1}.png")
                            pixmap = screen.grabWindow(0)
                            if pixmap.save(screenshot_path, 'PNG'):
                                screenshot_paths.append(screenshot_path)
                                logger.info(f"Captured screen {i+1}: {screen.name()} -> {screenshot_path}")
                else:
                    import subprocess
                    screenshot_path = os.path.join(output_dir, "screen_1.png")
                    result = subprocess.run(
                        ['gnome-screenshot', '-f', screenshot_path],
                        capture_output=True, timeout=10,
                    )
                    if result.returncode == 0 and os.path.exists(screenshot_path):
                        screenshot_paths.append(screenshot_path)
            except ImportError:
                logger.error("PyQt6 not installed")

    except Exception as e:
        logger.error(f"Error capturing all screens: {e}", exc_info=True)

    return screenshot_paths


def capture_screenshot(output_path: str, region: str = "full") -> bool:
    """
    Capture a screenshot and save it to the specified path.

    Args:
        output_path: Path where the screenshot should be saved
        region: 'full' for full screen, 'window' for active window, 'selection' for user-selected region

    Returns:
        True if successful, False otherwise
    """
    system = platform.system()

    try:
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        if system == "Darwin":
            import subprocess
            import time

            if region == "window":
                result = subprocess.run(
                    ['screencapture', '-w', output_path],
                    capture_output=True, timeout=10,
                )
            elif region == "selection":
                result = subprocess.run(
                    ['screencapture', '-i', output_path],
                    capture_output=True, timeout=30,
                )
            else:
                result = subprocess.run(
                    ['screencapture', output_path],
                    capture_output=True, timeout=10,
                )

            if result.stdout:
                logger.debug(f"screencapture stdout: {result.stdout.decode('utf-8', errors='ignore')}")
            if result.stderr:
                logger.warning(f"screencapture stderr: {result.stderr.decode('utf-8', errors='ignore')}")

            time.sleep(0.1)

            file_exists = os.path.exists(output_path) if output_path else False
            if result.returncode == 0 and file_exists:
                file_size = os.path.getsize(output_path)
                logger.info(f"Screenshot captured successfully: {output_path} ({file_size} bytes)")
                return True
            else:
                logger.error(f"Screenshot capture failed: returncode={result.returncode}, file exists={file_exists}")
                return False

        elif system == "Windows":
            try:
                from PIL import ImageGrab
                screenshot = ImageGrab.grab()
                screenshot.save(output_path, 'PNG')
                return os.path.exists(output_path)
            except ImportError:
                logger.error("PIL/Pillow not installed.")
                return False

        else:  # Linux
            try:
                import subprocess
                if region == "window":
                    result = subprocess.run(['gnome-screenshot', '-w', '-f', output_path], capture_output=True, timeout=10)
                elif region == "selection":
                    result = subprocess.run(['gnome-screenshot', '-a', '-f', output_path], capture_output=True, timeout=30)
                else:
                    result = subprocess.run(['gnome-screenshot', '-f', output_path], capture_output=True, timeout=10)

                if result.returncode == 0 and os.path.exists(output_path):
                    return True

                from PIL import ImageGrab
                screenshot = ImageGrab.grab()
                screenshot.save(output_path, 'PNG')
                return os.path.exists(output_path)
            except (FileNotFoundError, ImportError):
                try:
                    from PIL import ImageGrab
                    screenshot = ImageGrab.grab()
                    screenshot.save(output_path, 'PNG')
                    return os.path.exists(output_path)
                except ImportError:
                    logger.error("PIL/Pillow not installed.")
                    return False

    except Exception as e:
        logger.error(f"Error capturing screenshot: {e}", exc_info=True)
        return False

# ---------------------------------------------------------------------------
# Image conversion
# ---------------------------------------------------------------------------

def image_to_base64(image_path: str, convert_to_webp: bool = True) -> tuple[Optional[str], str]:
    """
    Convert an image file to base64, optionally converting to WebP.

    Returns:
        (base64_data_or_none, mime_type). When WebP conversion fails, the raw file
        bytes are returned with a guessed MIME (e.g. image/png) — callers must use
        this MIME in data URLs / Anthropic ``media_type`` (never label PNG as webp).
    """
    try:
        if convert_to_webp:
            try:
                from PIL import Image

                img = Image.open(image_path)
                if img.mode in ('RGBA', 'LA', 'P'):
                    rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = rgb_img
                else:
                    img = img.convert('RGB')

                with tempfile.NamedTemporaryFile(suffix='.webp', delete=False) as tmp_file:
                    tmp_webp_path = tmp_file.name

                img.save(tmp_webp_path, 'WEBP', quality=80, method=6)

                with open(tmp_webp_path, 'rb') as webp_file:
                    image_data = webp_file.read()

                original_size = os.path.getsize(image_path)
                webp_size = len(image_data)
                compression_ratio = (1 - (webp_size / original_size)) * 100 if original_size > 0 else 0

                def _fmt(size):
                    if size < 1024:
                        return f"{size} B"
                    elif size < 1024 * 1024:
                        return f"{size / 1024:.1f} KB"
                    return f"{size / (1024 * 1024):.1f} MB"

                logger.info(f"[Vision LLM] ✅ Converted image to WebP: {_fmt(original_size)} → {_fmt(webp_size)} ({compression_ratio:.1f}% smaller)")

                try:
                    os.unlink(tmp_webp_path)
                except OSError:
                    pass

                return base64.b64encode(image_data).decode('utf-8'), "image/webp"
            except Exception as e:
                logger.warning(f"Failed to convert image to WebP, using original bytes: {e}")
                with open(image_path, 'rb') as f:
                    raw = f.read()
                mime, _ = mimetypes.guess_type(image_path)
                if not mime or not mime.startswith("image/"):
                    mime = "image/png"
                return base64.b64encode(raw).decode('utf-8'), mime
        else:
            with open(image_path, 'rb') as f:
                raw = f.read()
            mime, _ = mimetypes.guess_type(image_path)
            if not mime or not mime.startswith("image/"):
                mime = "application/octet-stream"
            return base64.b64encode(raw).decode('utf-8'), mime
    except Exception as e:
        logger.error(f"Error converting image to base64: {e}")
        return None, "image/png"
