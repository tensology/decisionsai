"""
Desktop automation — open apps, URLs, file menu.

Used by agent tools for application launching and window management.
"""

import os
import platform
import subprocess
import logging
import time

import pyautogui
pyautogui.FAILSAFE = False

from fuzzywuzzy import fuzz

logger = logging.getLogger(__name__)

# Platform-specific imports
if platform.system() == 'Darwin':
    try:
        from AppKit import NSWorkspace, NSApplicationActivateIgnoringOtherApps
        import Quartz
        APPKIT_AVAILABLE = True
    except ImportError:
        APPKIT_AVAILABLE = False
else:
    APPKIT_AVAILABLE = False

# --- App / URL shortcut mappings ---
# Canonical names for voice-triggered app opening.

APP_ALIASES = {
    "word": "Microsoft Word", "excel": "Microsoft Excel", "powerpoint": "Microsoft PowerPoint",
    "chrome": "Google Chrome", "google chrome": "Google Chrome", "safari": "Safari",
    "firefox": "Firefox", "brave": "Brave Browser", "brave browser": "Brave Browser",
    "vlc": "VLC", "v l c": "VLC", "cursor": "Cursor",
    "code": "Visual Studio Code", "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code", "visual studio code": "Visual Studio Code",
    "terminal": "Terminal", "iterm": "iTerm", "item": "iTerm", "i term": "iTerm",
    "finder": "Finder", "notes": "Notes", "reminders": "Reminders", "calendar": "Calendar",
    "music": "Music", "spotify": "Spotify", "slack": "Slack", "discord": "Discord",
    "zoom": "zoom.us", "teams": "Microsoft Teams", "outlook": "Microsoft Outlook",
    "mail": "Mail", "email": "Mail", "my email": "Mail", "messages": "Messages",
    "imessage": "Messages", "facetime": "FaceTime", "photos": "Photos", "preview": "Preview",
    "pages": "Pages", "numbers": "Numbers", "keynote": "Keynote", "xcode": "Xcode",
    "activity monitor": "Activity Monitor", "system preferences": "System Preferences",
    "system settings": "System Settings", "settings": "System Settings",
    "app store": "App Store", "notion": "Notion", "figma": "Figma", "sketch": "Sketch",
    "photoshop": "Adobe Photoshop", "illustrator": "Adobe Illustrator",
    "premiere": "Adobe Premiere Pro", "after effects": "Adobe After Effects",
}

URL_SHORTCUTS = {
    "gmail": "https://mail.google.com", "gmail account": "https://mail.google.com",
    "my gmail": "https://mail.google.com", "my gmail account": "https://mail.google.com",
    "google": "https://www.google.com", "google search": "https://www.google.com",
    "youtube": "https://www.youtube.com", "twitter": "https://twitter.com",
    "x": "https://x.com", "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com", "linkedin": "https://www.linkedin.com",
    "github": "https://github.com", "reddit": "https://www.reddit.com",
    "amazon": "https://www.amazon.com", "netflix": "https://www.netflix.com",
    "google drive": "https://drive.google.com", "google docs": "https://docs.google.com",
    "google sheets": "https://sheets.google.com", "google calendar": "https://calendar.google.com",
    "google meet": "https://meet.google.com", "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai", "whatsapp": "https://web.whatsapp.com",
    "whatsapp web": "https://web.whatsapp.com", "toggl": "https://track.toggl.com/timer",
    "toggle": "https://track.toggl.com/timer",
    "new google doc": "https://docs.google.com/document/create",
    "new google document": "https://docs.google.com/document/create",
    "new document": "https://docs.google.com/document/create",
    "new google sheet": "https://sheets.google.com/create",
    "new spreadsheet": "https://sheets.google.com/create",
    "new google slide": "https://slides.google.com/create",
    "new presentation": "https://slides.google.com/create",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def open_app(name: str):
    """Open or focus an application by name.

    Resolves *name* through APP_ALIASES first, then tries running apps,
    installed apps, and finally the raw name.
    """
    speech = (name or "").lower().strip()
    if not speech:
        logger.warning("open_app: empty app request")
        return
    logger.info("open_app: request='%s'", speech)

    # 1. URL shortcut?
    for shortcut, url in URL_SHORTCUTS.items():
        if shortcut in speech or speech in shortcut:
            logger.info("open_app: matched URL shortcut %s -> %s", shortcut, url)
            return open_url(url)

    # 2. Spotlight shortcut
    if "spotlight" in speech:
        return _open_spotlight()

    # 3. Resolve app name
    app_name = _resolve_app_name(speech)
    if not app_name:
        app_name = speech
        logger.info("open_app: no alias match, using raw name '%s'", app_name)

    # 4. Platform-specific open/focus
    _platform_open(app_name)


def open_url(url: str):
    """Open a URL in the default browser."""
    system = platform.system()
    try:
        if system == 'Darwin':
            subprocess.run(['open', url], check=True)
        elif system == 'Windows':
            subprocess.run(['start', url], shell=True, check=True)
        else:
            subprocess.run(['xdg-open', url], check=True)
    except Exception as e:
        logger.error("Failed to open URL %s: %s", url, e)


def open_file_menu():
    """Open the application's File menu."""
    if platform.system() == 'Darwin':
        pyautogui.hotkey('command', 'shift', 'f')
    else:
        pyautogui.hotkey('alt', 'f')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _open_spotlight():
    system = platform.system()
    if system == 'Darwin':
        pyautogui.hotkey('command', 'space')
    elif system == 'Windows':
        pyautogui.press('win')
    else:
        pyautogui.press('win')


def _resolve_app_name(speech: str):
    """Try APP_ALIASES, then running apps, then installed apps."""
    # Exact / substring match in aliases
    words = speech.split()
    for i in range(len(words)):
        for j in range(i + 1, len(words) + 1):
            candidate = " ".join(words[i:j])
            if candidate in APP_ALIASES:
                return APP_ALIASES[candidate]
            for alias, full in APP_ALIASES.items():
                if candidate in alias or alias in candidate:
                    return full

    # Fuzzy match against running apps
    match = _fuzzy_match_running(speech)
    if match:
        return match

    # Fuzzy match against installed apps
    return _fuzzy_match_installed(speech)


def _fuzzy_match_running(speech: str, threshold=70):
    """Find best fuzzy match among running applications."""
    system = platform.system()
    best, best_score = None, 0

    if system == 'Darwin' and APPKIT_AVAILABLE:
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            name = app.localizedName() or ""
            if not name:
                continue
            score = max(fuzz.partial_ratio(speech, name.lower()),
                        fuzz.ratio(speech, name.lower()),
                        fuzz.token_sort_ratio(speech, name.lower()))
            if speech in name.lower() or name.lower() in speech:
                score = max(score, 90)
            if score > best_score:
                best, best_score = name, score
    elif system == 'Windows':
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                try:
                    name = (proc.info['name'] or '').replace('.exe', '')
                    score = fuzz.partial_ratio(speech, name.lower())
                    if score > best_score:
                        best, best_score = name, score
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except ImportError:
            pass

    return best if best_score > threshold else None


def _fuzzy_match_installed(speech: str, threshold=70):
    """Find best fuzzy match among installed applications."""
    system = platform.system()
    apps = []

    if system == 'Darwin':
        for d in ["/Applications", os.path.expanduser("~/Applications")]:
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                if f.endswith('.app'):
                    apps.append(f.replace('.app', ''))
    elif system == 'Windows':
        # Scan Start Menu shortcuts — this is where apps like Office, Chrome etc. are registered
        start_menu_dirs = [
            os.path.join(os.environ.get('ProgramData', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs'),
            os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs'),
        ]
        for sm_dir in start_menu_dirs:
            if not os.path.isdir(sm_dir):
                continue
            for root, dirs, files in os.walk(sm_dir):
                for f in files:
                    if f.endswith('.lnk'):
                        apps.append(f.replace('.lnk', ''))
        # Also scan Program Files as fallback
        for d in [os.environ.get('ProgramFiles', ''), os.environ.get('ProgramFiles(x86)', '')]:
            if d and os.path.isdir(d):
                apps.extend(os.listdir(d))

    best, best_score = None, 0
    for app in apps:
        score = max(fuzz.partial_ratio(speech, app.lower()),
                    fuzz.ratio(speech, app.lower()),
                    fuzz.token_sort_ratio(speech, app.lower()))
        if speech in app.lower() or app.lower() in speech:
            score = max(score, 90)
        if score > best_score:
            best, best_score = app, score

    return best if best_score > threshold else None


def _center_mouse_on_app(app_name: str):
    """Move mouse to center of the named app's frontmost window."""
    if platform.system() != 'Darwin' or not APPKIT_AVAILABLE:
        screen = pyautogui.size()
        pyautogui.moveTo(screen.width // 2, screen.height // 2)
        return

    ws = NSWorkspace.sharedWorkspace()
    target_name = (app_name or "").lower()
    if not target_name:
        return
    target = next(
        (
            a
            for a in ws.runningApplications()
            if (a.localizedName() or "").lower() == target_name
        ),
        None,
    )
    if not target:
        return

    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID)
    win = next((w for w in wins
                if w.get(Quartz.kCGWindowOwnerName, '').lower() == (target.localizedName() or '').lower()), None)
    if win:
        b = win.get(Quartz.kCGWindowBounds)
        if b:
            pyautogui.moveTo(b['X'] + b['Width'] // 2, b['Y'] + b['Height'] // 2)


def _platform_open(app_name: str):
    """Open or focus *app_name* using platform-native methods."""
    system = platform.system()

    if system == 'Darwin' and APPKIT_AVAILABLE:
        app_name = (app_name or "").strip()
        if not app_name:
            logger.warning("_platform_open: empty app_name on macOS")
            return
        ws = NSWorkspace.sharedWorkspace()
        target_name = app_name.lower()
        target = next(
            (
                a
                for a in ws.runningApplications()
                if (a.localizedName() or "").lower() == target_name
            ),
            None,
        )
        if target:
            target.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            _center_mouse_on_app(app_name)
            return
        # Not running — try to launch
        for method in [
            lambda: ws.launchApplication_(app_name),
            lambda: subprocess.run(["open", "-a", app_name], check=True),
            lambda: subprocess.run(["open", "-a", f"{app_name}.app"], check=True),
        ]:
            try:
                method()
                time.sleep(1)
                _center_mouse_on_app(app_name)
                return
            except Exception:
                continue
        logger.warning("Failed to launch %s on macOS", app_name)

    elif system == 'Windows':
        # Try to focus an already-open window first
        try:
            import win32gui, win32con
            windows = []
            def cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd) and app_name.lower() in win32gui.GetWindowText(hwnd).lower():
                    windows.append(hwnd)
            win32gui.EnumWindows(cb, None)
            if windows:
                try:
                    win32gui.ShowWindow(windows[0], win32con.SW_RESTORE)
                    # Windows blocks SetForegroundWindow from background processes.
                    # Simulate an Alt press to trick Windows into allowing it.
                    import ctypes
                    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
                    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # Alt up
                    win32gui.SetForegroundWindow(windows[0])
                except Exception:
                    # Last resort: use pyautogui alt-tab approach
                    try:
                        win32gui.ShowWindow(windows[0], win32con.SW_SHOW)
                    except Exception:
                        pass
                return
        except ImportError:
            pass

        # Try to find and launch via Start Menu shortcut (.lnk files)
        launched = False
        start_menu_dirs = [
            os.path.join(os.environ.get('ProgramData', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs'),
            os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs'),
        ]
        for sm_dir in start_menu_dirs:
            if not os.path.isdir(sm_dir):
                continue
            for root, dirs, files in os.walk(sm_dir):
                for f in files:
                    if f.endswith('.lnk') and app_name.lower() in f.lower():
                        os.startfile(os.path.join(root, f))
                        launched = True
                        break
                if launched:
                    break
            if launched:
                break

        if not launched:
            # Fallback: use 'start' command which searches PATH and App Paths registry
            subprocess.Popen(f'start "" "{app_name}"', shell=True)

    else:  # Linux
        for tool, args in [('wmctrl', ['-a', app_name]), ('xdotool', ['search', '--name', app_name, 'windowactivate'])]:
            try:
                if subprocess.run([tool] + args, capture_output=True, check=False).returncode == 0:
                    return
            except FileNotFoundError:
                continue
        subprocess.Popen([app_name], shell=False)
