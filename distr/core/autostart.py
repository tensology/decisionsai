"""
Autostart Utility — Register/unregister the app to launch on system startup.

Supports:
  - macOS: Login Items via a LaunchAgent plist in ~/Library/LaunchAgents/
  - Windows: Registry key in HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
"""
import logging
import os
import platform
import sys

logger = logging.getLogger(__name__)

APP_NAME = "DecisionsAI"
MACOS_PLIST_LABEL = "net.decisionsai.app"


def _get_launch_command() -> str:
    """Return the command that should be used to launch the app on startup."""
    # Use sys.executable (the Python interpreter) + the start script
    start_script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "bin", "start.py",
    )
    return f'{sys.executable} "{start_script}"'


# ---------------------------------------------------------------------------
# macOS — LaunchAgent plist
# ---------------------------------------------------------------------------

def _plist_path() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, "Library", "LaunchAgents", f"{MACOS_PLIST_LABEL}.plist")


def _enable_macos() -> bool:
    """Create a LaunchAgent plist so the app starts on login."""
    python_bin = sys.executable
    start_script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "bin", "start.py",
    )

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{MACOS_PLIST_LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>{python_bin}</string>
    <string>{start_script}</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <false/>
</dict>
</plist>
"""
    path = _plist_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(plist_content)
        logger.info("macOS autostart enabled: %s", path)
        return True
    except Exception as e:
        logger.error("Failed to enable macOS autostart: %s", e)
        return False


def _disable_macos() -> bool:
    """Remove the LaunchAgent plist."""
    path = _plist_path()
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info("macOS autostart disabled (removed %s)", path)
        return True
    except Exception as e:
        logger.error("Failed to disable macOS autostart: %s", e)
        return False


def _is_enabled_macos() -> bool:
    return os.path.exists(_plist_path())


# ---------------------------------------------------------------------------
# Windows — Registry Run key
# ---------------------------------------------------------------------------

_WIN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _enable_windows() -> bool:
    """Add a Run registry entry so the app starts on login."""
    try:
        import winreg
        cmd = _get_launch_command()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        logger.info("Windows autostart enabled via registry")
        return True
    except Exception as e:
        logger.error("Failed to enable Windows autostart: %s", e)
        return False


def _disable_windows() -> bool:
    """Remove the Run registry entry."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass  # Already removed
        logger.info("Windows autostart disabled via registry")
        return True
    except Exception as e:
        logger.error("Failed to disable Windows autostart: %s", e)
        return False


def _is_enabled_windows() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_REG_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_autostart(enabled: bool) -> bool:
    """Enable or disable launch-on-startup for the current platform.

    Returns True on success, False on failure.
    """
    system = platform.system()
    if system == "Darwin":
        return _enable_macos() if enabled else _disable_macos()
    elif system == "Windows":
        return _enable_windows() if enabled else _disable_windows()
    else:
        logger.warning("Autostart not supported on %s", system)
        return False


def is_autostart_enabled() -> bool:
    """Check whether launch-on-startup is currently configured."""
    system = platform.system()
    if system == "Darwin":
        return _is_enabled_macos()
    elif system == "Windows":
        return _is_enabled_windows()
    return False
