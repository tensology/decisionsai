"""
Desktop ops that run inside the Decisions process.

macOS TCC attaches to the calling binary. The sidecar cannot inherit Decisions'
grants, so window/screenshot work falls back here when the sidecar is untrusted
or not running.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import platform
import subprocess
import tempfile
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

LOCAL_DESKTOP_TOOLS = frozenset(
    {
        "list_windows",
        "focus_window",
        "set_window_bounds",
        "window_action",
        "get_screen_info",
        "launch_app",
        "capture_screen",
        "get_window_tree",
        "find_element",
        "move_mouse",
        "click_element",
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
    if tool == "window_action":
        return _window_action(params)
    if tool == "get_screen_info":
        return _get_screen_info()
    if tool == "launch_app":
        return _launch_app(params)
    if tool == "capture_screen":
        return _capture_screen()
    if tool == "get_window_tree":
        return _get_window_tree(params)
    if tool == "find_element":
        return _find_element(params)
    if tool == "move_mouse":
        return _move_mouse(params)
    if tool == "click_element":
        return _click_element(params)
    return None


_element_cache: list[dict[str, Any]] = []
_element_cache_lock = threading.Lock()


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
        kCGWindowNumber,
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
                "window_id": int(window.get(kCGWindowNumber, 0) or 0),
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


def _screen_rect(screen_index: int) -> tuple[int, int, int, int]:
    from AppKit import NSScreen

    screens = list(NSScreen.screens() or [])
    if not screens:
        return 0, 0, 1440, 900
    if screen_index < 0 or screen_index >= len(screens):
        raise RuntimeError(f"display {screen_index + 1} not found; available displays: 1-{len(screens)}")
    main_height = float(screens[0].frame().size.height)
    visible = screens[screen_index].visibleFrame()
    x = int(visible.origin.x)
    y = int(main_height - (visible.origin.y + visible.size.height))
    return x, y, int(visible.size.width), int(visible.size.height)


def _get_screen_info() -> dict[str, Any]:
    from AppKit import NSScreen

    screens = list(NSScreen.screens() or [])
    if not screens:
        return {"screens": []}
    main_height = float(screens[0].frame().size.height)
    rows = []
    for index, screen in enumerate(screens):
        frame = screen.frame()
        rows.append(
            {
                "index": index,
                "name": str(screen.localizedName() or f"Screen {index + 1}"),
                "display_id": int(screen.deviceDescription().get("NSScreenNumber", 0) or 0),
                "logical_width": int(frame.size.width),
                "logical_height": int(frame.size.height),
                "x_offset": int(frame.origin.x),
                "y_offset": int(main_height - (frame.origin.y + frame.size.height)),
                "scale_factor": float(screen.backingScaleFactor()),
                "is_primary": index == 0,
            }
        )
    return {"screens": rows, "primary": rows[0], "via": "decisions"}


def _set_window_bounds(params: dict[str, Any]) -> dict[str, Any]:
    pid = _as_int(params.get("pid"))
    if pid <= 0:
        raise RuntimeError("missing required parameter: pid")
    snap = str(params.get("snap") or "").strip().lower()
    x, y, w, h = _as_int(params.get("x")), _as_int(params.get("y")), _as_int(params.get("w")), _as_int(params.get("h"))
    if snap:
        screen = _as_int(params.get("screen"), 0)
        sx, sy, sw, sh = _primary_visible_rect() if screen == 0 else _screen_rect(screen)
        if snap == "left":
            x, y, w, h = sx, sy, sw // 2, sh
        elif snap == "right":
            x, y, w, h = sx + sw // 2, sy, sw - sw // 2, sh
        elif snap == "maximize":
            x, y, w, h = sx, sy, sw, sh
        elif snap == "center":
            w, h = sw * 4 // 5, sh * 4 // 5
            x, y = sx + (sw - w) // 2, sy + (sh - h) // 2
        else:
            raise RuntimeError(f"unknown snap {snap!r} (use left, right, center, maximize)")
    if w <= 0 or h <= 0:
        raise RuntimeError("need snap=left|right|center|maximize or positive w and h")
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


def _window_action(params: dict[str, Any]) -> dict[str, Any]:
    pid = _as_int(params.get("pid"))
    action = str(params.get("action") or "").strip().lower()
    if pid <= 0:
        raise RuntimeError("missing required parameter: pid")
    if action == "maximize":
        return _set_window_bounds({"pid": pid, "screen": params.get("screen", 0), "snap": "maximize"})
    bodies = {
        "minimize": 'set value of attribute "AXMinimized" of first window to true',
        "minimise": 'set value of attribute "AXMinimized" of first window to true',
        "restore": 'try\nset value of attribute "AXFullScreen" of first window to false\nend try\nset value of attribute "AXMinimized" of first window to false',
        "fullscreen": 'set currentValue to value of attribute "AXFullScreen" of first window\nset value of attribute "AXFullScreen" of first window to (not currentValue)',
        "close": 'click (first button of first window whose subrole is "AXCloseButton")',
        "hide": "set visible to false",
        "focus": 'set frontmost to true\ntry\nset value of attribute "AXMinimized" of first window to false\nend try',
    }
    body = bodies.get(action)
    if body is None:
        raise RuntimeError(f"unknown window action {action!r}")
    script = (
        'tell application "System Events"\n'
        f"set proc to first process whose unix id is {pid}\n"
        "tell proc\n"
        'if (count of windows) is 0 then error "no window"\n'
        f"{body}\n"
        "end tell\n"
        "end tell"
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=8)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "osascript failed")
    return {"success": True, "pid": pid, "action": action, "via": "decisions"}


def _launch_app(params: dict[str, Any]) -> dict[str, Any]:
    app = str(params.get("executable") or params.get("app_name") or "").strip()
    if not app:
        raise RuntimeError("missing required parameter: executable")
    result = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"could not launch {app}")
    from AppKit import NSApplicationActivateIgnoringOtherApps, NSWorkspace

    requested = app.lower()
    for _ in range(20):
        for running in NSWorkspace.sharedWorkspace().runningApplications():
            name = str(running.localizedName() or "")
            bundle = str(running.bundleIdentifier() or "")
            if requested in {name.lower(), bundle.lower()}:
                running.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                return {
                    "success": True,
                    "app": app,
                    "name": name,
                    "bundle_id": bundle,
                    "pid": int(running.processIdentifier()),
                    "activated": True,
                    "via": "decisions",
                }
        time.sleep(0.15)
    raise RuntimeError(f"{app} started but could not be verified in NSWorkspace")


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


def _target_pid(params: dict[str, Any]) -> int:
    pid = _as_int(params.get("pid"))
    if pid > 0:
        return pid
    from AppKit import NSWorkspace

    app_name = str(params.get("app_name") or "").strip().lower()
    workspace = NSWorkspace.sharedWorkspace()
    if app_name:
        for app in workspace.runningApplications():
            name = str(app.localizedName() or "").lower()
            bundle = str(app.bundleIdentifier() or "").lower()
            if app_name in name or app_name in bundle:
                return int(app.processIdentifier())
        raise RuntimeError(f"no running application matching {app_name!r}")
    front = workspace.frontmostApplication()
    if front is None:
        raise RuntimeError("no frontmost application")
    return int(front.processIdentifier())


def _get_window_tree(params: dict[str, Any]) -> dict[str, Any]:
    pid = _target_pid(params)
    depth = max(1, min(_as_int(params.get("depth"), 3), 8))
    script = f"""
var se = Application('System Events');
var maxDepth = {depth};
var procs = se.processes.whose({{unixId: {pid}}});
if (procs.length === 0) {{ JSON.stringify({{error:'Process not found',pid:{pid}}}); }}
else {{
  var proc = procs[0], elements = [];
  function walk(el, level) {{
    if (level > maxDepth || elements.length > 300) return;
    try {{
      var role = el.role(), name = '', pos = [0,0], sz = [0,0];
      try {{ name = el.name() || ''; }} catch(e) {{}}
      try {{ pos = el.position(); sz = el.size(); }} catch(e) {{}}
      if (sz[0] > 0 && sz[1] > 0) elements.push({{
        id:elements.length,name:String(name).substring(0,100),control_type:role,
        enabled:true,rect:{{x:pos[0],y:pos[1],w:sz[0],h:sz[1]}}
      }});
      var children = el.uiElements();
      for (var i=0; i<children.length && i<50; i++) walk(children[i],level+1);
    }} catch(e) {{}}
  }}
  var wins = proc.windows();
  for (var w=0; w<wins.length; w++) walk(wins[w],0);
  var title = '';
  try {{ title = wins.length ? wins[0].name() : ''; }} catch(e) {{}}
  JSON.stringify({{window_title:title,pid:{pid},element_count:elements.length,elements:elements}});
}}"""
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "accessibility tree failed")
    tree = json.loads(result.stdout.strip())
    elements = tree.get("elements") or []
    with _element_cache_lock:
        _element_cache.clear()
        _element_cache.extend(elements)
    tree["via"] = "decisions"
    return tree


def _find_element(params: dict[str, Any]) -> dict[str, Any]:
    tree = _get_window_tree(params)
    name = str(params.get("name") or "").strip().lower()
    control_type = str(params.get("control_type") or "").strip().lower()
    matches = []
    for element in tree.get("elements") or []:
        element_name = str(element.get("name") or "").lower()
        element_type = str(element.get("control_type") or "").lower()
        if name and name not in element_name:
            continue
        if control_type and control_type not in element_type:
            continue
        matches.append(element)
    return {"elements": matches, "count": len(matches), "pid": tree.get("pid"), "via": "decisions"}


def _cached_element(element_id: int) -> dict[str, Any]:
    with _element_cache_lock:
        if element_id < 0 or element_id >= len(_element_cache):
            raise RuntimeError(f"element [{element_id}] is not cached; call find_element first")
        return dict(_element_cache[element_id])


def _element_center(element_id: int) -> tuple[int, int]:
    rect = _cached_element(element_id).get("rect") or {}
    return (
        _as_int(rect.get("x")) + _as_int(rect.get("w")) // 2,
        _as_int(rect.get("y")) + _as_int(rect.get("h")) // 2,
    )


def _run_cliclick(command: str) -> None:
    result = subprocess.run(["cliclick", command], capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "cliclick failed")


def _move_mouse(params: dict[str, Any]) -> dict[str, Any]:
    if "element_id" in params:
        x, y = _element_center(_as_int(params.get("element_id")))
    else:
        x, y = _as_int(params.get("x")), _as_int(params.get("y"))
    _run_cliclick(f"m:{x},{y}")
    return {"success": True, "x": x, "y": y, "via": "decisions"}


def _click_element(params: dict[str, Any]) -> dict[str, Any]:
    x, y = _element_center(_as_int(params.get("element_id")))
    action = str(params.get("action") or "click").strip().lower()
    command = {"click": "c", "double_click": "dc", "right_click": "rc"}.get(action, "c")
    _run_cliclick(f"{command}:{x},{y}")
    return {"success": True, "action": action, "x": x, "y": y, "via": "decisions"}
