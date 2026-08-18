"""
Named-window ops via the sidecar: list, focus, launch, set bounds / snap.

These are OS-API verbs. Prefer them over clicking title bars.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.tools import BaseTool

from distr.core.agent.services.computer_use_context import record_action, record_observation

logger = logging.getLogger(__name__)

_SIDECAR_TIMEOUT = 20


def _call_sidecar(tool: str, params: dict, timeout: int = _SIDECAR_TIMEOUT) -> dict:
    from distr.core.agent.tools.input.sidecar_http import call_sidecar_tool

    return call_sidecar_tool(tool, params, timeout=timeout)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def format_window_list(windows: list[dict]) -> str:
    """Compact list_windows output for the LLM."""
    if not windows:
        return "No windows found."
    lines = [f"Found {len(windows)} window(s):"]
    for w in windows[:40]:
        fg = " [foreground]" if w.get("is_foreground") else ""
        lines.append(
            f"  pid={w.get('pid')} {w.get('process_name') or '?'} "
            f"title={str(w.get('title') or '')!r} "
            f"bounds=({w.get('left')},{w.get('top')})-({w.get('right')},{w.get('bottom')}){fg}"
        )
    if len(windows) > 40:
        lines.append(f"  ... ({len(windows) - 40} more not shown)")
    return "\n".join(lines)


def resolve_window_pid(
    pid: Any = 0,
    process_name: str = "",
    title: str = "",
    app_name: str = "",
) -> tuple[int, dict]:
    """
    Resolve a window to pid using list_windows.

    Matches process_name/app_name against process_name or title (substring, case-insensitive).
    """
    resolved = _as_int(pid)
    needle = (process_name or app_name or "").strip().lower()
    title_needle = (title or "").strip().lower()
    if resolved and not needle and not title_needle:
        return resolved, {}

    result = _call_sidecar("list_windows", {})
    windows = result.get("windows") or []
    if not isinstance(windows, list):
        windows = []

    if resolved:
        for w in windows:
            if _as_int(w.get("pid")) == resolved:
                return resolved, w
        return resolved, {}

    if not needle and not title_needle:
        raise ValueError("provide pid, process_name, app_name, or title")

    matches: list[dict] = []
    for w in windows:
        pn = str(w.get("process_name") or "").lower()
        tt = str(w.get("title") or "").lower()
        if needle and needle not in pn and needle not in tt:
            continue
        if title_needle and title_needle not in tt:
            continue
        matches.append(w)
    if not matches:
        raise ValueError(
            f"no window matching process_name={process_name or app_name!r} title={title!r}"
        )
    matches.sort(key=lambda w: (not bool(w.get("is_foreground")),))
    chosen = matches[0]
    return _as_int(chosen.get("pid")), chosen


class ListWindowsTool(BaseTool):
    name: str = "list_windows"
    description: str = (
        "List visible desktop windows with pid, process_name, title, and bounds. "
        "Use this before focus_window or set_window_bounds when the user names an app "
        "(Terminal, Chrome, TextEdit). Prefer this over screenshots for window layout."
    )

    def _run(self, **kwargs) -> str:
        try:
            result = _call_sidecar("list_windows", {})
            windows = result.get("windows") or []
            record_observation(
                source="window_ops",
                details={"tool": "list_windows", "count": len(windows)},
            )
            return format_window_list(windows)
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("list_windows failed: %s", e, exc_info=True)
            return f"Error: {e}"


class FocusWindowTool(BaseTool):
    name: str = "focus_window"
    description: str = (
        "Bring a window to the foreground. Provide pid from list_windows, "
        "or process_name/app_name/title (e.g. process_name='Terminal')."
    )

    def _run(
        self,
        pid: int = 0,
        process_name: str = "",
        app_name: str = "",
        title: str = "",
        **kwargs,
    ) -> str:
        try:
            resolved, window = resolve_window_pid(pid, process_name, title, app_name)
            result = _call_sidecar("focus_window", {"pid": resolved})
            if result.get("success"):
                record_action("focus_window", "success", {"pid": resolved})
                label = window.get("process_name") or window.get("title") or resolved
                return f"Focused pid={resolved} ({label})"
            return f"Focus failed for pid={resolved}: {result}"
        except (RuntimeError, ValueError) as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("focus_window failed: %s", e, exc_info=True)
            return f"Error: {e}"


class LaunchAppTool(BaseTool):
    name: str = "launch_app"
    description: str = (
        "Launch an application by name or path. "
        "Example: launch_app(executable='Terminal') or launch_app(executable='TextEdit')."
    )

    def _run(self, executable: str = "", app_name: str = "", **kwargs) -> str:
        app = (executable or app_name or "").strip()
        if not app:
            return "Error: executable is required (e.g. Terminal, TextEdit, Google Chrome)"
        try:
            result = _call_sidecar("launch_app", {"executable": app})
            if result.get("success"):
                record_action("launch_app", "success", {"executable": app, "pid": result.get("pid")})
                extra = f" pid={result.get('pid')}" if result.get("pid") else ""
                return f"Launched {app}{extra}"
            return f"Launch failed: {result}"
        except RuntimeError as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("launch_app failed: %s", e, exc_info=True)
            return f"Error: {e}"


class SetWindowBoundsTool(BaseTool):
    name: str = "set_window_bounds"
    description: str = (
        "Move and/or resize a window using OS APIs (not mouse drag). "
        "Identify it with pid, process_name, app_name, or title. "
        "Either pass snap='left'|'right'|'maximize', or explicit x,y,w,h. "
        "Example: set_window_bounds(process_name='Terminal', snap='left')"
    )

    def _run(
        self,
        pid: int = 0,
        process_name: str = "",
        app_name: str = "",
        title: str = "",
        snap: str = "",
        x: int = 0,
        y: int = 0,
        w: int = 0,
        h: int = 0,
        **kwargs,
    ) -> str:
        try:
            resolved, window = resolve_window_pid(pid, process_name, title, app_name)
            params: dict = {"pid": resolved}
            if snap:
                params["snap"] = snap.strip().lower()
            else:
                params["x"] = _as_int(x)
                params["y"] = _as_int(y)
                params["w"] = _as_int(w)
                params["h"] = _as_int(h)
            result = _call_sidecar("set_window_bounds", params)
            if result.get("success"):
                record_action("set_window_bounds", "success", result)
                label = window.get("process_name") or window.get("title") or resolved
                return (
                    f"Moved {label} pid={resolved} to "
                    f"({result.get('x')},{result.get('y')} {result.get('w')}x{result.get('h')})"
                    + (f" snap={result.get('snap')}" if result.get("snap") else "")
                )
            return f"set_window_bounds failed: {result}"
        except (RuntimeError, ValueError) as e:
            return f"Error: {e}"
        except Exception as e:
            logger.error("set_window_bounds failed: %s", e, exc_info=True)
            return f"Error: {e}"
