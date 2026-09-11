"""Deterministic window, display, and macOS Space management."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.agent.services.computer_use_context import record_action
from distr.core.agent.tools.input.window_ops import _call_sidecar, resolve_window_pid

logger = logging.getLogger(__name__)


class WindowManagementInput(BaseModel):
    action: str = Field(default="", description="minimize, restore, maximize, fullscreen, close, hide, focus, move_to_screen, list_spaces, switch_space, or move_to_space")
    pid: int = Field(default=0, description="Exact target process id")
    process_name: str = Field(default="", description="Target process, such as Terminal")
    app_name: str = Field(default="", description="Target application, such as Codex")
    title: str = Field(default="", description="Substring of the target window title")
    screen_number: Optional[int] = Field(default=None, description="One-based display number")
    screen_name: str = Field(default="", description="Display name or position: left, center, right, built-in, primary")
    space_number: Optional[int] = Field(default=None, description="One-based macOS desktop/Space number")
    text: str = Field(default="", description="Original user request text")


def _parse_action(text: str) -> str:
    value = (text or "").lower()
    if re.search(r"\bminimi[sz]e\b", value):
        return "minimize"
    if re.search(r"\b(restor(?:e|ed)|unminimi[sz]e)\b", value):
        return "restore"
    if re.search(r"\bmaximi[sz]e\b", value):
        return "maximize"
    if re.search(r"\bfull\s*screen\b", value):
        return "fullscreen"
    if re.search(r"\bclose\b.*\b(window|app|application)\b", value):
        return "close"
    if re.search(r"\bhide\b", value):
        return "hide"
    if re.search(r"\b(move|send|put)\b.*\b(desktop|space)\b", value):
        return "move_to_space"
    if re.search(r"\b(open|switch|go)\b.*\b(desktop|space)\b", value):
        return "switch_space"
    if re.search(r"\b(move|send|put)\b.*\b(screen|monitor|display)\b", value):
        return "move_to_screen"
    if re.search(r"\b(bring|focus|activate|show)\b", value):
        return "focus"
    return ""


def _ordinal_number(text: str, noun_pattern: str) -> Optional[int]:
    value = (text or "").lower()
    numeric = re.search(rf"\b(?:{noun_pattern})\s*(\d+)\b", value)
    if numeric:
        return int(numeric.group(1))
    for word, number in {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}.items():
        if re.search(rf"\b{word}\s+(?:{noun_pattern})\b", value):
            return number
    return None


def _screen_index(screen_number: Optional[int], screen_name: str, text: str) -> int:
    requested_number = screen_number or _ordinal_number(text, "screen|monitor|display")
    result = _call_sidecar("get_screen_info", {})
    screens = result.get("screens") or []
    if not screens:
        raise ValueError("no displays were reported by macOS")
    indexed_screens = list(enumerate(screens))
    physical_order = sorted(
        indexed_screens,
        key=lambda item: (
            int(item[1].get("x_offset") or 0),
            int(item[1].get("y_offset") or 0),
            int(item[1].get("index", item[0])),
        ),
    )

    def raw_index(item: tuple[int, dict[str, Any]]) -> int:
        fallback_index, screen = item
        return int(screen.get("index", fallback_index))

    if requested_number is not None:
        if requested_number < 1 or requested_number > len(screens):
            raise ValueError(f"display {requested_number} not found; available displays: 1-{len(screens)}")
        return raw_index(physical_order[requested_number - 1])

    request = f"{(screen_name or '').lower()} {(text or '').lower()}"
    requested_name = (screen_name or "").strip().lower()
    if requested_name and requested_name not in {
        "left", "right", "center", "centre", "middle", "built-in", "built in", "laptop", "primary"
    }:
        for item in indexed_screens:
            if requested_name in str(item[1].get("name") or "").lower():
                return raw_index(item)
    if "primary" in request or "main screen" in request:
        for item in indexed_screens:
            screen = item[1]
            if screen.get("is_primary"):
                return raw_index(item)
    if "built-in" in request or "built in" in request or "laptop" in request:
        for item in indexed_screens:
            screen = item[1]
            if "built" in str(screen.get("name") or "").lower():
                return raw_index(item)
    if "left" in request:
        return raw_index(physical_order[0])
    if "right" in request:
        return raw_index(physical_order[-1])
    if "center" in request or "centre" in request or "middle" in request:
        return raw_index(physical_order[(len(physical_order) - 1) // 2])
    raise ValueError("specify a display number or position: left, center, right, built-in, or primary")


def _verify_screen_move(result: dict[str, Any], raw_screen_index: int) -> tuple[bool, str]:
    """Verify returned window coordinates land inside the live target display."""
    screen_result = _call_sidecar("get_screen_info", {})
    screens = screen_result.get("screens") or []
    indexed_screens = list(enumerate(screens))
    target_item = next(
        (
            item
            for item in indexed_screens
            if int(item[1].get("index", item[0])) == int(raw_screen_index)
        ),
        None,
    )
    if target_item is None:
        return False, "the target display disappeared before verification"
    _, target = target_item
    try:
        screen_x = int(target.get("x_offset") or 0)
        screen_y = int(target.get("y_offset") or 0)
        screen_w = int(target.get("logical_width") or target.get("width") or 0)
        screen_h = int(target.get("logical_height") or target.get("height") or 0)
        window_x = int(result["x"])
        window_y = int(result["y"])
        window_w = int(result["w"])
        window_h = int(result["h"])
    except (KeyError, TypeError, ValueError):
        return False, "macOS did not return enough geometry to verify the move"
    if screen_w <= 0 or screen_h <= 0 or window_w <= 0 or window_h <= 0:
        return False, "macOS returned invalid geometry during verification"
    center_x = window_x + window_w / 2
    center_y = window_y + window_h / 2
    inside = (
        screen_x <= center_x < screen_x + screen_w
        and screen_y <= center_y < screen_y + screen_h
    )
    physical_order = sorted(
        indexed_screens,
        key=lambda item: (
            int(item[1].get("x_offset") or 0),
            int(item[1].get("y_offset") or 0),
            int(item[1].get("index", item[0])),
        ),
    )
    physical_number = next(
        index
        for index, item in enumerate(physical_order, start=1)
        if int(item[1].get("index", item[0])) == int(raw_screen_index)
    )
    label = str(target.get("name") or f"display {physical_number}")
    if not inside:
        return False, f"the window center did not land on display {physical_number} ({label})"
    return True, f"display {physical_number} ({label})"


class WindowManagementTool(BaseTool):
    name: str = "window_management"
    description: str = (
        "Manage the live frontmost window or a named app/window using macOS APIs. "
        "Minimize, restore, maximize, fullscreen, close, hide, or focus it. Move it "
        "to a numbered/named display or macOS desktop/Space, list Spaces, or switch "
        "to a Space. If no app is named, the live foreground window is targeted."
    )
    args_schema: type[BaseModel] = WindowManagementInput

    def get_triggers(self) -> list[str]:
        return ["minimize window", "maximize window", "fullscreen window", "restore window", "close window", "hide app", "focus window", "bring up app", "move window to screen", "move to monitor", "move window to desktop", "move window to space", "switch desktop", "open desktop"]

    def _run(self, action: str = "", pid: int = 0, process_name: str = "", app_name: str = "", title: str = "", screen_number: Optional[int] = None, screen_name: str = "", space_number: Optional[int] = None, text: str = "", **kwargs: Any) -> str:
        requested_action = (action or _parse_action(text)).strip().lower()
        if not requested_action:
            return "Error: specify a window, display, or Space action"
        try:
            if requested_action == "list_spaces":
                return self._list_spaces()
            if requested_action == "switch_space":
                space = space_number or _ordinal_number(text, "desktop|space")
                return self._switch_space(space, screen_number, screen_name, text)

            if not (pid or process_name or app_name or title):
                lower_text = (text or "").lower()
                aliases = {
                    "codecs": "Codex",
                    "codex": "Codex",
                    "terminal": "Terminal",
                    "spotify": "Spotify",
                    "finder": "Finder",
                    "safari": "Safari",
                    "chrome": "Google Chrome",
                    "brave": "Brave Browser",
                    "notes": "Notes",
                    "calculator": "Calculator",
                    "textedit": "TextEdit",
                }
                for alias, canonical in aliases.items():
                    if re.search(rf"\b{re.escape(alias)}\b", lower_text):
                        process_name = canonical
                        break

            resolved, window = resolve_window_pid(pid, process_name, title, app_name)
            label = str(window.get("process_name") or window.get("title") or resolved)
            if requested_action == "move_to_screen":
                screen = _screen_index(screen_number, screen_name, text)
                result = _call_sidecar("set_window_bounds", {"pid": resolved, "screen": screen, "snap": "center"})
            elif requested_action == "move_to_space":
                space = space_number or _ordinal_number(text, "desktop|space")
                result = self._call_space_action("move_window_to_space", space, screen_number, screen_name, text, resolved)
            else:
                result = _call_sidecar("window_action", {"pid": resolved, "action": requested_action})
            if not result.get("success"):
                return f"Error: {requested_action} failed for {label}: {result}"
            if requested_action == "move_to_screen":
                verified, verification = _verify_screen_move(result, screen)
                if not verified:
                    return f"Error: move verification failed for {label}: {verification}"
                record_action("window_management", "success", {"action": requested_action, **result})
                return f"Moved {label} to {verification} and verified its position"
            record_action("window_management", "success", {"action": requested_action, **result})
            return f"{requested_action.replace('_', ' ').title()} succeeded for {label}"
        except (RuntimeError, ValueError) as exc:
            return f"Error: {exc}"
        except Exception as exc:
            logger.error("window_management failed: %s", exc, exc_info=True)
            return f"Error: {exc}"

    def _display_for_space(self, screen_number: Optional[int], screen_name: str, text: str) -> int:
        if screen_number is None and not screen_name and not re.search(r"\b(screen|monitor|display|left|right|center|centre|built-in|primary)\b", text or "", re.IGNORECASE):
            return 0
        return _screen_index(screen_number, screen_name, text)

    def _call_space_action(self, tool: str, space: Optional[int], screen_number: Optional[int], screen_name: str, text: str, pid: int = 0) -> dict:
        if not space or space < 1:
            raise ValueError("specify a desktop/Space number, such as Space 2")
        params: dict[str, Any] = {"space": space, "display": self._display_for_space(screen_number, screen_name, text)}
        if pid:
            params["pid"] = pid
        return _call_sidecar(tool, params)

    def _switch_space(self, space: Optional[int], screen_number: Optional[int], screen_name: str, text: str) -> str:
        result = self._call_space_action("switch_space", space, screen_number, screen_name, text)
        if not result.get("success"):
            return f"Error: switch_space failed: {result}"
        record_action("window_management", "success", {"action": "switch_space", **result})
        return f"Switched to desktop/Space {space}"

    def _list_spaces(self) -> str:
        result = _call_sidecar("list_spaces", {})
        displays = result.get("displays") or []
        lines = [f"Found {len(displays)} display Space set(s):"]
        for display_index, display in enumerate(displays):
            spaces = display.get("Spaces") or []
            current = display.get("Current Space") or {}
            current_id = current.get("ManagedSpaceID") or current.get("id64")
            lines.append(f"  display {display_index + 1}: {len(spaces)} Space(s), current id={current_id}")
        return "\n".join(lines)
