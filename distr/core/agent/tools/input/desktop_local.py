"""
Desktop ops that run inside the Decisions process.

macOS TCC attaches to the calling binary. The sidecar cannot inherit Decisions'
grants, so window/screenshot work falls back here when the sidecar is untrusted
or not running.
"""

from __future__ import annotations

import base64
import logging
import os
import platform
import subprocess
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

LOCAL_DESKTOP_TOOLS = frozenset(
    {
        "list_windows",
        "focus_window",
        "set_window_bounds",
        "launch_app",
        "capture_screen",
    }
)


def run_local_desktop_tool(tool: str, params: dict | None = None) -> dict[str, Any] | None:
    """Run a desktop tool in-process. Returns None if this host cannot handle it."""
    if platform.system() != "Darwin" or tool not in LOCAL_DESKTOP_TOOLS:
        return None
    params = params or {}
    if tool == "list_windows":
        return _list_windows()
    if tool == "focus_window":
        return _focus_window(params)
    if tool == "set_window_bounds":
        return _set_window_bounds(params)
    if tool == "launch_app":
        return _launch_app(params)
    if tool == "capture_screen":
        return _capture_screen()
    return None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _list_windows() -> dict[str, Any]:
    from AppKit import NSWorkspace
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowBounds,
        kCGWindowLayer,
        kCGWindowListExcludeDesktopElements,
        kCGWindowListOptionOnScreenOnly,
        kCGWindowName,
        kCGWindowOwnerName,
        kCGWindowOwnerPID,
    )

    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    front_pid = int(front.processIdentifier()) if front else 0
    items: list[dict[str, Any]] = []
    for window in (
        CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
        or []
    ):
        if int(window.get(kCGWindowLayer, 0) or 0) != 0:
            continue
        bounds = window.get(kCGWindowBounds, {}) or {}
        left = int(bounds.get("X", 0) or 0)
        top = int(bounds.get("Y", 0) or 0)
        width = int(bounds.get("Width", 0) or 0)
        height = int(bounds.get("Height", 0) or 0)
        if width <= 1 or height <= 1:
            continue
        pid = int(window.get(kCGWindowOwnerPID, 0) or 0)
        items.append(
            {
                "title": str(window.get(kCGWindowName, "") or ""),
                "pid": pid,
                "process_name": str(window.get(kCGWindowOwnerName, "") or ""),
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "is_foreground": pid == front_pid,
            }
        )
    return {"windows": items}


def _focus_window(params: dict[str, Any]) -> dict[str, Any]:
    pid = _as_int(params.get("pid"))
    if pid <= 0:
        raise RuntimeError("missing required parameter: pid")
    from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        raise RuntimeError(f"no running application for pid={pid}")
    ok = bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
    return {"success": ok, "pid": pid, "via": "decisions"}


def _primary_visible_rect() -> tuple[int, int, int, int]:
    """Usable primary screen in System Events top-left coordinates."""
    from AppKit import NSScreen

    screens = NSScreen.screens()
    if not screens:
        return 0, 0, 1440, 900
    primary = screens[0]
    full = primary.frame()
    vis = primary.visibleFrame()
    x = int(vis.origin.x)
    y = int(full.size.height - vis.origin.y - vis.size.height)
    return x, y, int(vis.size.width), int(vis.size.height)


def _set_window_bounds(params: dict[str, Any]) -> dict[str, Any]:
    pid = _as_int(params.get("pid"))
    if pid <= 0:
        raise RuntimeError("missing required parameter: pid")
    snap = str(params.get("snap") or "").strip().lower()
    x, y, w, h = _as_int(params.get("x")), _as_int(params.get("y")), _as_int(params.get("w")), _as_int(params.get("h"))
    if snap:
        sx, sy, sw, sh = _primary_visible_rect()
        if snap == "left":
            x, y, w, h = sx, sy, sw // 2, sh
        elif snap == "right":
            x, y, w, h = sx + sw // 2, sy, sw - sw // 2, sh
        elif snap == "maximize":
            x, y, w, h = sx, sy, sw, sh
        else:
            raise RuntimeError(f"unknown snap {snap!r} (use left, right, maximize)")
    if w <= 0 or h <= 0:
        raise RuntimeError("need snap=left|right|maximize or positive w and h")
    script = (
        f'tell application "System Events"\n'
        f"set proc to first process whose unix id is {pid}\n"
        f"tell proc\n"
        f'if (count of windows) is 0 then error "no window"\n'
        f"set position of first window to {{{x}, {y}}}\n"
        f"set size of first window to {{{w}, {h}}}\n"
        f"end tell\n"
        f"end tell"
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "osascript failed")
    return {"success": True, "pid": pid, "x": x, "y": y, "w": w, "h": h, "snap": snap, "via": "decisions"}


def _launch_app(params: dict[str, Any]) -> dict[str, Any]:
    app = str(params.get("executable") or params.get("app_name") or "").strip()
    if not app:
        raise RuntimeError("missing required parameter: executable")
    result = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"could not launch {app}")
    return {"success": True, "app": app, "via": "decisions"}


def _capture_screen() -> dict[str, Any]:
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        result = subprocess.run(
            ["screencapture", "-x", tmp.name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not os.path.isfile(tmp.name) or os.path.getsize(tmp.name) < 32:
            raise RuntimeError(result.stderr.strip() or "screencapture failed")
        with open(tmp.name, "rb") as handle:
            data = handle.read()
        return {
            "type": "screenshot",
            "mime_type": "image/png",
            "data": base64.b64encode(data).decode(),
            "via": "decisions",
        }
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
